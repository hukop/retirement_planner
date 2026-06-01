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


def _compute_incremental_tax(
    base_ordinary: float,
    conversion_amount: float,
    long_term_gains: float,
    ss_income: float,
    filing_status: str,
) -> float:
    """
    Compute the additional tax owed from converting ``conversion_amount``.

    Returns
    -------
    Incremental tax (always >= 0).
    """
    tax_without = calculate_taxes(
        ordinary_income=base_ordinary,
        long_term_gains=long_term_gains,
        ss_income=ss_income,
        filing_status=filing_status,
    ).total_tax

    tax_with = calculate_taxes(
        ordinary_income=base_ordinary + conversion_amount,
        long_term_gains=long_term_gains,
        ss_income=ss_income,
        filing_status=filing_status,
    ).total_tax

    return max(0.0, tax_with - tax_without)


def _estimate_annual_ordinary_income(
    profile: PlanProfile,
    year: int,
    start_year: int,
) -> float:
    """
    Estimate total ordinary income for a given year (salary only, pre-retirement).
    Used to compute marginal tax bracket impact of conversions.
    """
    years_of_raises = max(0, year - start_year)
    total = 0.0
    for src in profile.incomes:
        # Check if the owner is still working
        owner = profile.self_person if src.owner == "self" else profile.spouse
        if year < owner.retirement_year:
            annual = src.annual_amount * (1 + src.annual_raise_pct / 100) ** years_of_raises
            total += annual
    return total


def _estimate_annual_rental_income(
    profile: PlanProfile,
    year: int,
    start_year: int,
) -> float:
    """
    Estimate net rental income for a given year (considering appreciation and inflation).
    """
    years_elapsed = max(0, year - start_year)
    cola = profile.inflation_rate_pct / 100

    total_rental_net = 0.0
    for prop in profile.properties:
        # Monthly income and expenses inflate
        monthly_income = prop.monthly_rental_income * (1 + prop.rental_inflation_rate_pct / 100) ** years_elapsed
        monthly_expenses = prop.monthly_expenses * (1 + prop.rental_inflation_rate_pct / 100) ** years_elapsed

        annual_net = (monthly_income - monthly_expenses) * 12
        total_rental_net += annual_net

    return max(0.0, total_rental_net)


def _estimate_annual_ss_income(
    profile: PlanProfile,
    year: int,
    start_year: int,
) -> float:
    """
    Estimate annual Social Security income for a given year.
    """
    from engine.social_security import compute_ss_benefit

    cola = profile.inflation_rate_pct / 100
    total_ss = 0.0

    for person, person_role in [(profile.self_person, "self"), (profile.spouse, "spouse")]:
        # Compute SS benefit for this person
        ss_benefit = compute_ss_benefit(person, start_year)

        # Check if they're claiming this year
        if year >= ss_benefit.claim_year:
            # Get the monthly amount for this year
            monthly_amount = ss_benefit.monthly_in_year(year, cola)
            total_ss += monthly_amount * 12

    return total_ss


def _grow_balance(balance: float, rate_pct: float, years: int) -> float:
    """Compound a balance at the given annual rate for a number of years."""
    return balance * ((1 + rate_pct / 100) ** years)


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
      2. **Conversion** — a modified profile where ``annual_amount`` is moved
         from Traditional IRA → Roth IRA each year during the conversion window

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
    _, baseline_annual = run_projection(profile)

    # ── Step 2: Build conversion-modified profile ────────────────────────
    conv_profile = _deep_copy_profile(profile)

    # Find source (trad_ira) and destination (roth_ira) accounts
    source_indices = _find_source_accounts(conv_profile, config.source_account_types)
    roth_idx = _find_roth_account(conv_profile)

    # Auto-create a Roth IRA if the user doesn't have one yet.
    if roth_idx is None:
        conv_profile.accounts.append(InvestmentAccount(
            name="Roth IRA (Auto-created)",
            account_type="roth_ira",
            balance=0.0,
            cost_basis=0.0,
            annual_return_pct=7.0,
            owner="self"
        ))
        roth_idx = len(conv_profile.accounts) - 1

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

    # ── Step 3: Perform year-by-year conversions on the profile ──────────
    conversion_details: list[dict] = []
    total_converted = 0.0
    total_tax_cost = 0.0

    # Build a map of baseline withdrawal amounts by year (for tax calculations)
    # Withdraw ordinary income (Trad IRA/401k) separately from capital gains (brokerage)
    baseline_withdrawal_ordinary = {}
    baseline_withdrawal_gains = {}
    for _, row in baseline_annual.iterrows():
        year_key = int(row["year"])
        # withdrawal_ordinary = Trad IRA/401k withdrawals (taxed as ordinary income)
        baseline_withdrawal_ordinary[year_key] = row.get("withdrawal_ordinary", 0.0)
        # withdrawal_gains = capital gains from brokerage (taxed as long-term capital gains)
        baseline_withdrawal_gains[year_key] = row.get("withdrawal_gains", 0.0)

    for year in range(start_year, end_year + 1):
        remaining = config.annual_amount
        year_converted = 0.0

        # Estimate the ordinary income for this year (for tax calculation)
        base_ordinary = _estimate_annual_ordinary_income(
            conv_profile, year, current_year
        )

        # Add rental income (taxable as ordinary income)
        base_ordinary += _estimate_annual_rental_income(
            conv_profile, year, current_year
        )

        # Add withdrawal income from baseline projection
        # Withdrawals from Traditional IRA/401k are ordinary income
        withdrawal_ordinary = baseline_withdrawal_ordinary.get(year, 0.0)
        base_ordinary += withdrawal_ordinary

        # Add Social Security income
        base_ss_income = _estimate_annual_ss_income(
            conv_profile, year, current_year
        )

        # Capital gains from withdrawals (from brokerage account)
        base_cap_gains = baseline_withdrawal_gains.get(year, 0.0)

        # Withdraw from source accounts
        for src_idx in source_indices:
            if remaining <= 0:
                break
            source_acct = conv_profile.accounts[src_idx]
            available = source_acct.balance
            if available <= 0:
                continue

            amount_to_convert = min(remaining, available)

            # Reduce source balance
            conv_profile.accounts[src_idx] = InvestmentAccount(
                name=source_acct.name,
                account_type=source_acct.account_type,
                balance=source_acct.balance - amount_to_convert,
                cost_basis=source_acct.cost_basis,
                annual_contribution=source_acct.annual_contribution,
                employer_match=source_acct.employer_match,
                annual_return_pct=source_acct.annual_return_pct,
                owner=source_acct.owner,
            )

            year_converted += amount_to_convert
            remaining -= amount_to_convert

        if year_converted > 0:
            # Increase Roth balance
            roth_acct = conv_profile.accounts[roth_idx]
            conv_profile.accounts[roth_idx] = InvestmentAccount(
                name=roth_acct.name,
                account_type=roth_acct.account_type,
                balance=roth_acct.balance + year_converted,
                cost_basis=roth_acct.cost_basis,
                annual_contribution=roth_acct.annual_contribution,
                employer_match=roth_acct.employer_match,
                annual_return_pct=roth_acct.annual_return_pct,
                owner=roth_acct.owner,
            )

            # Compute incremental tax cost
            incr_tax = _compute_incremental_tax(
                base_ordinary=base_ordinary,
                conversion_amount=year_converted,
                long_term_gains=base_cap_gains,
                ss_income=base_ss_income,
                filing_status=conv_profile.filing_status,
            )

            # Deduct tax from brokerage account (most liquid source)
            # This is crucial: taxes must actually reduce the portfolio in the
            # conversion scenario, otherwise the timing of conversions doesn't matter
            brokerage_idx = None
            for i, acct in enumerate(conv_profile.accounts):
                if acct.account_type == "brokerage":
                    brokerage_idx = i
                    break

            if brokerage_idx is not None and incr_tax > 0:
                brok_acct = conv_profile.accounts[brokerage_idx]
                tax_paid = min(incr_tax, brok_acct.balance)  # Pay what we can afford
                conv_profile.accounts[brokerage_idx] = InvestmentAccount(
                    name=brok_acct.name,
                    account_type=brok_acct.account_type,
                    balance=brok_acct.balance - tax_paid,
                    cost_basis=max(0, brok_acct.cost_basis - tax_paid),
                    annual_contribution=brok_acct.annual_contribution,
                    employer_match=brok_acct.employer_match,
                    annual_return_pct=brok_acct.annual_return_pct,
                    owner=brok_acct.owner,
                )

            # Compute marginal rates
            rates = marginal_rates(
                ordinary_income=base_ordinary + year_converted,
                filing_status=conv_profile.filing_status,
            )

            conversion_details.append({
                "year": year,
                "conversion_amount": round(year_converted, 2),
                "incremental_tax": round(incr_tax, 2),
                "marginal_rate_federal": round(rates["federal_marginal"] * 100, 1),
                "marginal_rate_ca": round(rates["ca_marginal"] * 100, 1),
                "marginal_rate_combined": round(rates["combined_marginal"] * 100, 1),
                "source_balance_after": round(
                    sum(conv_profile.accounts[i].balance for i in source_indices), 2
                ),
                "roth_balance_after": round(conv_profile.accounts[roth_idx].balance, 2),
            })

            total_converted += year_converted
            total_tax_cost += incr_tax

        # Apply one year of growth to all accounts (approximate compounding
        # between conversion years) using each account's own return rate
        for i, acct in enumerate(conv_profile.accounts):
            growth = acct.balance * (acct.annual_return_pct / 100)
            conv_profile.accounts[i] = InvestmentAccount(
                name=acct.name,
                account_type=acct.account_type,
                balance=acct.balance + growth,
                cost_basis=acct.cost_basis,
                annual_contribution=acct.annual_contribution,
                employer_match=acct.employer_match,
                annual_return_pct=acct.annual_return_pct,
                owner=acct.owner,
            )

    # ── Step 4: Build a clean conversion profile ──────────────────────────
    # The year-by-year loop above tracks transfers and applies manual growth
    # that ProjectionEngine would double-count.  So we start fresh from the
    # original profile and apply the total conversion as a lump-sum at today's
    # balances (preferable to the old approach of growing to start_year then
    # applying, which caused double-compounding).
    #
    # We do NOT deduct the incremental tax from any account balance here.
    # The conversion tax is paid during the conversion years from that year's
    # income/withdrawals, not as a lump sum at year 0.  Instead we inject the
    # incremental tax into the annual tax column for the correct years below,
    # which allows the projection engine's withdrawal logic to handle the
    # actual cash flow impact.

    conv_profile_final = _deep_copy_profile(profile)
    source_indices_final = _find_source_accounts(conv_profile_final, config.source_account_types)
    roth_idx_final = _find_roth_account(conv_profile_final)

    if roth_idx_final is None:
        conv_profile_final.accounts.append(InvestmentAccount(
            name="Roth IRA (Auto-created)",
            account_type="roth_ira",
            balance=0.0,
            cost_basis=0.0,
            annual_return_pct=7.0,
            owner="self"
        ))
        roth_idx_final = len(conv_profile_final.accounts) - 1

    # Apply the lump-sum transfer at today's balances
    cumulative_converted = total_converted

    total_source_start = sum(
        conv_profile_final.accounts[i].balance for i in source_indices_final
    )

    if total_source_start > 0 and cumulative_converted > 0:
        actual_transfer = min(cumulative_converted, total_source_start)

        # Remove from source accounts (proportional to their balances)
        remaining_to_remove = actual_transfer
        for src_idx in source_indices_final:
            if remaining_to_remove <= 0:
                break
            acct = conv_profile_final.accounts[src_idx]
            remove = min(remaining_to_remove, acct.balance)
            acct.balance -= remove
            remaining_to_remove -= remove

        # Add to Roth account (first one found)
        roth_acct = conv_profile_final.accounts[roth_idx_final]
        roth_acct.balance += actual_transfer

    # ── Step 5: Run conversion projection & adjust tax yearly ─────────────
    _, conversion_annual = run_projection(conv_profile_final)

    # Inject the incremental tax into the correct years.  This is the ONLY
    # place the conversion tax cost is reflected — the balance change above
    # (IRA -> Roth) affects future RMDs/withdrawal taxes naturally via the
    # projection engine's own tax estimation.
    for detail in conversion_details:
        year = detail["year"]
        incr_tax = detail["incremental_tax"]
        mask = conversion_annual["year"] == year
        if mask.any():
            conversion_annual.loc[mask, "tax_annual_est"] += incr_tax
            # Also reduce net worth in that year by the tax amount
            conversion_annual.loc[mask, "net_worth_eoy"] -= incr_tax
            # Reduce investment balance (tax is paid from investments)
            conversion_annual.loc[mask, "balance_investment_total"] -= incr_tax
            # Reduce total assets similarly
            if "total_assets_eoy" in conversion_annual.columns:
                conversion_annual.loc[mask, "total_assets_eoy"] -= incr_tax

    # ── Step 6: Compute comparison metrics ───────────────────────────────
    # Align DataFrames by year
    baseline_years = baseline_annual["year"].values
    conversion_years = conversion_annual["year"].values

    # Net worth at plan end
    baseline_nw_end = float(baseline_annual["net_worth_eoy"].iloc[-1])
    conversion_nw_end = float(conversion_annual["net_worth_eoy"].iloc[-1])
    net_worth_delta = conversion_nw_end - baseline_nw_end

    # Breakeven year: first year conversion NW >= baseline NW
    breakeven_year = None
    for i in range(len(baseline_annual)):
        b_nw = float(baseline_annual["net_worth_eoy"].iloc[i])
        c_nw = float(conversion_annual["net_worth_eoy"].iloc[i])
        year = int(baseline_annual["year"].iloc[i])
        if c_nw >= b_nw and year > end_year:
            breakeven_year = year
            break

    # Lifetime tax comparison
    baseline_total_tax = float(baseline_annual["tax_annual_est"].sum())
    conversion_total_tax = float(conversion_annual["tax_annual_est"].sum())
    lifetime_tax_savings = baseline_total_tax - conversion_total_tax + total_tax_cost

    # RMD reduction
    baseline_rmd = 0.0
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