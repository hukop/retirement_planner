"""
Roth conversion projection engine.

Compares two scenarios — **baseline (no conversion)** vs. **with conversion** —
and computes the net worth delta, breakeven year, total tax cost, lifetime tax
savings, and RMD reduction over the full plan horizon.

Conversion Mechanics
-------------------
During each year of the conversion window (``start_year`` → ``end_year``),
the engine:

  1. Withdraws ``annual_amount`` from Traditional IRA accounts (capped at
     available balance).
  2. Deposits that amount into the first Roth IRA account.
  3. Adds the conversion amount to ordinary income for tax purposes that year.

The analysis runs two full deterministic projections (via ``ProjectionEngine``)
and compares the resulting annual DataFrames row-by-row.

Limitations
-----------
- Conversions come from ``trad_ira`` accounts only (401k not supported).
- Pro-rata rule is NOT modeled — all converted dollars are fully taxable.
- No Monte Carlo integration (deterministic comparison only).
- Conversions are modeled by modifying the profile's account balances
  year-by-year and re-running projection from each adjusted state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from engine.models import PlanProfile, InvestmentAccount, RothConversionConfig
from engine.projections import ProjectionEngine, run_projection
from engine.taxes import calculate_taxes, marginal_rates


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class RothConversionResult:
    """
    Output of ``run_roth_conversion_analysis()``.

    Contains both the full annual DataFrames for charting and headline
    summary metrics for the KPI cards.
    """

    # Full annual projection DataFrames (list-of-records for JSON serialization)
    baseline_annual: list[dict]
    conversion_annual: list[dict]

    # Year-by-year conversion detail rows
    conversion_details: list[dict]   # year, amount, incremental_tax, marginal_rate, ...

    # Headline metrics
    total_converted: float
    total_tax_cost: float            # incremental taxes paid due to conversions
    net_worth_delta_at_end: float    # conversion NW − baseline NW at plan end
    breakeven_year: Optional[int]    # year conversion NW overtakes baseline NW
    lifetime_tax_savings: float      # total taxes (baseline) − total taxes (conversion)
    rmd_reduction: float             # total RMD (baseline) − total RMD (conversion)
    years_of_conversion: int

    # Config snapshot
    annual_amount: float
    start_year: int
    end_year: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_roth_account(profile: PlanProfile) -> Optional[int]:
    """Return the index of the first Roth IRA account, or None."""
    for i, acct in enumerate(profile.accounts):
        if acct.account_type == "roth_ira":
            return i
    return None


def _find_source_accounts(
    profile: PlanProfile,
    source_types: list[str],
) -> list[int]:
    """Return indices of accounts eligible as conversion sources."""
    return [
        i for i, acct in enumerate(profile.accounts)
        if acct.account_type in source_types
    ]


def _deep_copy_profile(profile: PlanProfile) -> PlanProfile:
    """Create a deep copy of a PlanProfile via round-trip serialization."""
    return PlanProfile.from_dict(profile.to_dict())


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def run_roth_conversion_analysis(
    profile: PlanProfile,
    config: RothConversionConfig,
) -> RothConversionResult:
    """
    Run a Roth conversion comparison analysis.

    Runs two deterministic projections:
      1. **Baseline** — the profile as-is (no conversions)
      2. **Conversion** — the same profile but with ``annual_amount`` moved
         from Traditional IRA → Roth IRA in January of each scheduled year,
         inside the month-by-month simulation loop (i.e. at live, compounded
         balances for that year — not a lump-sum applied at t=0).

    Parameters
    ----------
    profile : PlanProfile
        The user's retirement plan.
    config : RothConversionConfig
        Conversion parameters (amount, start/end year, source accounts).

    Returns
    -------
    RothConversionResult with comparison data and summary metrics.
    """
    current_year = date.today().year

    # Resolve config defaults against the profile
    start_year = config.start_year if config.start_year > 0 else current_year
    end_year = config.end_year if config.end_year > 0 else profile.retirement_year_self
    end_year = min(end_year, profile.plan_end_year)
    start_year = max(start_year, current_year)

    if end_year < start_year:
        end_year = start_year

    # ── Step 1: Baseline projection ──────────────────────────────────────
    baseline_engine = ProjectionEngine(profile)
    _, baseline_annual = baseline_engine.run()

    # ── Step 2: Build conversion profile ────────────────────────────────
    # Ensure a Roth IRA account exists on the conversion profile so the
    # engine has somewhere to deposit converted funds.
    conv_profile = _deep_copy_profile(profile)

    source_indices = _find_source_accounts(conv_profile, config.source_account_types)
    roth_idx = _find_roth_account(conv_profile)

    if roth_idx is None:
        conv_profile.accounts.append(InvestmentAccount(
            name="Roth IRA (Auto-created)",
            account_type="roth_ira",
            balance=0.0,
            cost_basis=0.0,
            annual_return_pct=7.0,
            owner="self"
        ))

    # If no eligible source accounts exist, return baseline as-is
    if not source_indices:
        return RothConversionResult(
            baseline_annual=baseline_annual.to_dict("records"),
            conversion_annual=baseline_annual.to_dict("records"),
            conversion_details=[],
            total_converted=0.0,
            total_tax_cost=0.0,
            net_worth_delta_at_end=0.0,
            breakeven_year=None,
            lifetime_tax_savings=0.0,
            rmd_reduction=0.0,
            years_of_conversion=0,
            annual_amount=config.annual_amount,
            start_year=start_year,
            end_year=end_year,
        )

    # ── Step 3: Run conversion projection via engine ─────────────────────
    # Build the schedule: one entry per year in the conversion window.
    conversion_schedule = {
        yr: config.annual_amount
        for yr in range(start_year, end_year + 1)
    }

    conv_engine = ProjectionEngine(
        conv_profile,
        conversion_schedule=conversion_schedule,
        conversion_source_types=config.source_account_types,
    )
    _, conversion_annual = conv_engine.run()

    # Conversion details come straight from the engine's event log.
    conversion_details = conv_engine.conversion_events
    total_converted = sum(e["conversion_amount"] for e in conversion_details)
    total_tax_cost = sum(e["incremental_tax"] for e in conversion_details)

    # Inject incremental tax back into the DataFrame for accurate display in UI/tests
    for detail in conversion_details:
        yr = detail["year"]
        incr_tax = detail["incremental_tax"]
        mask = conversion_annual["year"] == yr
        if mask.any():
            conversion_annual.loc[mask, "tax_annual_est"] += incr_tax

    # ── Step 4: Compute comparison metrics ───────────────────────────────
    # Net worth at plan end
    baseline_nw_end  = float(baseline_annual["net_worth_eoy"].iloc[-1])
    conversion_nw_end = float(conversion_annual["net_worth_eoy"].iloc[-1])
    net_worth_delta  = conversion_nw_end - baseline_nw_end

    # Breakeven year: first year conversion NW >= baseline NW (after conversion window)
    breakeven_year = None
    for i in range(len(baseline_annual)):
        b_nw = float(baseline_annual["net_worth_eoy"].iloc[i])
        c_nw = float(conversion_annual["net_worth_eoy"].iloc[i])
        yr   = int(baseline_annual["year"].iloc[i])
        if c_nw >= b_nw and yr > end_year:
            breakeven_year = yr
            break

    # Lifetime tax comparison
    baseline_total_tax  = float(baseline_annual["tax_annual_est"].sum())
    conversion_total_tax = float(conversion_annual["tax_annual_est"].sum())
    lifetime_tax_savings = baseline_total_tax - conversion_total_tax + total_tax_cost

    # RMD reduction
    baseline_rmd   = 0.0
    conversion_rmd = 0.0
    if "withdrawal_rmd" in baseline_annual.columns:
        baseline_rmd = float(baseline_annual["withdrawal_rmd"].sum())
    if "withdrawal_rmd" in conversion_annual.columns:
        conversion_rmd = float(conversion_annual["withdrawal_rmd"].sum())
    rmd_reduction = baseline_rmd - conversion_rmd

    return RothConversionResult(
        baseline_annual=baseline_annual.to_dict("records"),
        conversion_annual=conversion_annual.to_dict("records"),
        conversion_details=conversion_details,
        total_converted=round(total_converted, 2),
        total_tax_cost=round(total_tax_cost, 2),
        net_worth_delta_at_end=round(net_worth_delta, 2),
        breakeven_year=breakeven_year,
        lifetime_tax_savings=round(lifetime_tax_savings, 2),
        rmd_reduction=round(rmd_reduction, 2),
        years_of_conversion=len(conversion_details),
        annual_amount=config.annual_amount,
        start_year=start_year,
        end_year=end_year,
    )


def roth_conversion_result_to_dict(result: RothConversionResult) -> dict:
    """Serialize RothConversionResult to a JSON-safe dict for dcc.Store."""
    from dataclasses import asdict
    return asdict(result)
