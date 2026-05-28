"""
Monte Carlo simulation engine.

Overview
--------
Runs N independent projection trials, each with a different randomly-generated
sequence of monthly market returns, then aggregates the results to produce:

  - Probability of success (portfolio > $0 at plan end)
  - Net worth percentile time series (for fan charts)
  - Terminal net worth distribution
  - Worst / median / best trial annual DataFrames
  - Ruin year distribution (when money runs out, by trial)

Return Model
------------
Uses a **parametric log-normal** model.  For each trial and each month, a
random monthly return is drawn from a log-normal distribution whose parameters
are calibrated to match the user's specified annual mean return and standard
deviation:

    ln(1 + r_monthly) ~ N(mu_m, sigma_m^2)

where mu_m and sigma_m are derived from the annual arithmetic mean (μ_a)
and standard deviation (σ_a) via:

    sigma_m = sqrt( ln(1 + (σ_a / (1 + μ_a))^2) / 12 )
    mu_m    = ln(1 + μ_a) / 12 - 0.5 * sigma_m^2

This ensures:
  - Returns can never go below -100% (log-normal is always positive)
  - The distribution is right-skewed, matching real equity returns
  - Geometric mean and annualised vol match user inputs

Account Bucketing
-----------------
All equity-like accounts (401k, trad_ira, roth_ira, roth_401k, brokerage)
share the SAME randomised return each month — this correctly models the fact
that market risk moves all equity positions together (systematic risk).

Low-volatility accounts (savings, hsa) use a SEPARATE low-volatility return
sequence drawn from the bond/cash parameters in MonteCarloConfig.

Performance
-----------
The deterministic baseline and selected detailed scenarios still use
ProjectionEngine, but the bulk trials use a single-threaded primitive kernel.
It keeps the same month-by-month math while avoiding per-trial DataFrame
construction and most AccountState/WithdrawalResult object allocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from engine.models import PlanProfile, MonteCarloConfig, ACCOUNT_TAX_TREATMENT
from engine.projections import ProjectionEngine
from engine.taxes import calculate_taxes
from engine.withdrawal import RMD_ACCOUNT_TYPES, WITHDRAWAL_TIERS, distribution_period


# ---------------------------------------------------------------------------
# Account bucketing
# ---------------------------------------------------------------------------

# Equity-like accounts share the main (volatile) return sequence
_EQUITY_ACCOUNT_TYPES = {"401k", "trad_ira", "roth_ira", "roth_401k", "brokerage"}
# Low-volatility accounts get a separate bond/cash return sequence
_BOND_ACCOUNT_TYPES   = {"savings", "hsa"}

_TAXABLE = 0
_TAX_DEFERRED = 1
_TAX_FREE = 2

_TAX_TREATMENT_CODES = {
    "taxable": _TAXABLE,
    "tax_deferred": _TAX_DEFERRED,
    "tax_free": _TAX_FREE,
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MonteCarloResult:
    """
    Aggregated results from a Monte Carlo simulation run.

    All NumPy arrays are serialisable via .tolist() for Dash store storage.
    """

    # Run metadata
    num_trials:  int
    num_months:  int
    start_year:  int
    years:       list[int]       # calendar years in the simulation

    # ── Headline metric ──────────────────────────────────────────────────
    success_rate: float           # fraction of trials where money outlasts plan

    # ── Terminal net worth distribution ───────────────────────────────────
    terminal_net_worths: list[float]    # one value per trial (sorted ascending)

    # ── Time-series percentile bands (annual granularity) ─────────────────
    # Shape: (num_years, num_percentiles)
    # Percentile levels: [5, 10, 25, 50, 75, 90, 95]
    percentile_labels:    list[int]
    net_worth_percentiles: list[list[float]]   # [year_idx][pct_idx]

    # ── Select trial annual DataFrames (as list-of-records for JSON) ──────
    median_trial_df:        list[dict]   # trial closest to median terminal NW
    worst_trial_df:         list[dict]   # trial with lowest terminal NW
    best_trial_df:          list[dict]   # trial with highest terminal NW
    deterministic_df:       list[dict]   # base-case deterministic run

    # ── Ruin analysis ─────────────────────────────────────────────────────
    # Year in which money first hits $0 for failing trials; NaN otherwise
    ruin_years: list[Optional[int]]

    # ── Config snapshot ───────────────────────────────────────────────────
    num_trials_actual: int      # may differ if profile had fewer simulation months
    mean_return_pct:   float
    std_dev_pct:       float


# ---------------------------------------------------------------------------
# Return sequence generation
# ---------------------------------------------------------------------------

def _annual_to_monthly_lognormal_params(
    annual_mean_pct: float,
    annual_std_pct:  float,
) -> tuple[float, float]:
    """
    Convert annualised arithmetic mean and standard deviation (in %) to
    the mu and sigma of the monthly log-normal distribution.

    Returns
    -------
    (mu_monthly, sigma_monthly) — parameters for np.random.normal that,
    when exponentiated (np.exp(sample)), give a valid monthly return
    multiplier: (1 + r_monthly).
    """
    mu_a    = annual_mean_pct / 100.0
    sigma_a = annual_std_pct  / 100.0

    # Monthly log-normal sigma (annualised vol / sqrt(12))
    sigma_m = np.sqrt(np.log(1 + (sigma_a / (1 + mu_a)) ** 2) / 12)

    # Monthly log-normal mu so that geometric mean matches annual mean/12
    mu_m = np.log(1 + mu_a) / 12 - 0.5 * sigma_m ** 2

    return mu_m, sigma_m


def generate_return_sequences(
    config:     MonteCarloConfig,
    num_months: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate random monthly return sequences for equity and bond buckets.

    Parameters
    ----------
    config     : MonteCarloConfig with mean/std dev and trial count
    num_months : number of monthly time steps per trial

    Returns
    -------
    equity_returns : shape (num_trials, num_months) — monthly return multipliers
                     for equity-like accounts (401k, IRA, brokerage …)
    bond_returns   : shape (num_trials, num_months) — monthly return multipliers
                     for low-volatility accounts (savings, HSA)

    Each value is a monthly return *rate* (not multiplier), so the caller does:
        new_balance = old_balance * (1 + equity_returns[trial, month])
    """
    rng = np.random.default_rng(config.random_seed)

    # Equity parameters
    mu_eq, sigma_eq = _annual_to_monthly_lognormal_params(
        config.mean_return_pct, config.std_dev_pct
    )
    # Bond parameters
    mu_bd, sigma_bd = _annual_to_monthly_lognormal_params(
        config.bond_mean_return_pct, config.bond_std_dev_pct
    )

    n = config.num_trials
    m = num_months

    # Draw from normal, then convert to return rate via exp(x) - 1
    equity_log_returns = rng.normal(loc=mu_eq, scale=sigma_eq, size=(n, m))
    bond_log_returns   = rng.normal(loc=mu_bd, scale=sigma_bd, size=(n, m))

    # Convert log-normal draws to return rates: r = e^x - 1
    equity_returns = np.exp(equity_log_returns) - 1.0
    bond_returns   = np.exp(bond_log_returns)   - 1.0

    return equity_returns, bond_returns


# ---------------------------------------------------------------------------
# Mixed return sequence (blends equity/bond per account bucket)
# ---------------------------------------------------------------------------

def _compute_blend_weights(
    profile: PlanProfile,
) -> tuple[float, float]:
    """
    Compute portfolio-weighted equity/bond blend weights from initial balances.

    Returns (eq_weight, bond_weight) that sum to 1.0.
    If total is 0, returns (1.0, 0.0) as a fallback.
    """
    total_equity = sum(
        a.balance for a in profile.accounts
        if a.account_type in _EQUITY_ACCOUNT_TYPES
    )
    total_bond = sum(
        a.balance for a in profile.accounts
        if a.account_type in _BOND_ACCOUNT_TYPES
    )
    total = total_equity + total_bond

    if total <= 0:
        return 1.0, 0.0  # fallback: all equity

    return total_equity / total, total_bond / total


def _build_all_blended_sequences(
    equity_seqs: np.ndarray,  # shape (num_trials, num_months)
    bond_seqs:   np.ndarray,  # shape (num_trials, num_months)
    profile:     PlanProfile,
) -> np.ndarray:
    """
    Build blended monthly return sequences for ALL trials at once via
    vectorized NumPy operations.

    Returns shape (num_trials, num_months).
    """
    eq_weight, bond_weight = _compute_blend_weights(profile)
    return eq_weight * equity_seqs + bond_weight * bond_seqs


# ---------------------------------------------------------------------------
# Adaptive spending: expense multipliers from market returns
# ---------------------------------------------------------------------------

def _compute_expense_multipliers(
    monthly_returns: np.ndarray,
    num_months: int,
    cut_max: float = 0.15,
    boost_max: float = 0.07,
) -> np.ndarray:
    """
    Compute a per-month expense multiplier based on trailing 12-month returns.

    Logic
    -----
    For each month, compute the cumulative return over the prior 12 months
    (or all available months if fewer than 12 exist).  Map that annualised
    return to a spending multiplier:

      - Annual return <= -20%  →  multiplier = 1 - cut_max   (0.85)
      - Annual return >= +20%  →  multiplier = 1 + boost_max (1.07)
      - 0% return              →  multiplier = 1.0 (no change)
      - Linear interpolation in between, clamped at the extremes

    The asymmetry (15% cut vs 7% boost) reflects real household behaviour:
    people cut discretionary spending more aggressively in downturns than
    they increase it in good times.

    Parameters
    ----------
    monthly_returns : 1-D array of monthly return rates for one trial
    num_months      : total simulation months
    cut_max         : maximum fractional expense reduction (default 0.15 = 15%)
    boost_max       : maximum fractional expense increase (default 0.07 = 7%)

    Returns
    -------
    1-D numpy array of length num_months, each value in [1-cut_max, 1+boost_max].
    """
    multipliers = np.ones(num_months)
    # Cumulative log returns for efficient trailing window computation
    log_returns = np.log1p(monthly_returns[:num_months])

    for m in range(num_months):
        # Trailing window: up to 12 months back
        lookback = min(m, 12)
        if lookback < 1:
            # First month — no history, keep multiplier at 1.0
            continue

        # Annualised return from trailing window
        cum_log = np.sum(log_returns[m - lookback:m])
        # Annualise: scale to 12 months
        annual_return = np.exp(cum_log * (12 / lookback)) - 1.0

        # Map to multiplier via clamped linear interpolation
        # Negative returns: scale down (0% → 1.0, -20% → 0.85)
        # Positive returns: scale up  (0% → 1.0, +20% → 1.07)
        if annual_return <= 0:
            # Normalise to [-1, 0] range (clamped at -20%)
            norm = max(-1.0, annual_return / 0.20)
            multipliers[m] = 1.0 + norm * cut_max    # at -1: 1 - 0.15 = 0.85
        else:
            norm = min(1.0, annual_return / 0.20)
            multipliers[m] = 1.0 + norm * boost_max  # at +1: 1 + 0.07 = 1.07

    return multipliers


# ---------------------------------------------------------------------------
# Ruin year helper
# ---------------------------------------------------------------------------

def _find_ruin_year(annual_df: pd.DataFrame) -> Optional[int]:
    """
    Return the first calendar year in which net worth hits or goes below $0.
    Returns None if money never runs out.
    """
    ruin_rows = annual_df[annual_df["net_worth_eoy"] <= 0]
    if ruin_rows.empty:
        return None
    return int(ruin_rows["year"].iloc[0])


# ---------------------------------------------------------------------------
# Primitive fast kernel for bulk Monte Carlo trials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FastMonteCarloContext:
    """Immutable inputs reused by every primitive Monte Carlo trial."""

    num_months: int
    years: list[int]
    filing_status: str

    income_totals: np.ndarray
    expense_totals: np.ndarray
    re_equity: np.ndarray
    taxable_monthly_income: np.ndarray
    annual_income_ordinary: np.ndarray
    annual_ss: np.ndarray

    month_numbers: np.ndarray
    year_numbers: np.ndarray
    year_indices: np.ndarray
    self_ages: np.ndarray
    spouse_ages: np.ndarray
    self_working: np.ndarray
    spouse_working: np.ndarray
    both_retired: np.ndarray

    initial_balances: np.ndarray
    initial_cost_basis: np.ndarray
    monthly_contributions: np.ndarray
    owner_codes: np.ndarray
    tax_codes: np.ndarray
    rmd_eligible: np.ndarray
    ordered_indices: np.ndarray
    self_contribution_indices: tuple[int, ...]
    spouse_contribution_indices: tuple[int, ...]
    account_names: list[str]
    first_taxable_idx: int

    start_net_worth: float
    bootstrap_eff_rate: float
    det_annual_nw: dict[int, float]


def _build_fast_context(
    profile: PlanProfile,
    det_engine: ProjectionEngine,
    det_annual_nw: dict[int, float],
    years: list[int],
    num_months: int,
) -> _FastMonteCarloContext:
    """Build per-run metadata for the primitive Monte Carlo trial kernel."""
    accounts = profile.accounts
    tax_treatments = [
        ACCOUNT_TAX_TREATMENT.get(account.account_type, "taxable")
        for account in accounts
    ]

    initial_balances = np.array(
        [float(account.balance) for account in accounts],
        dtype=float,
    )
    initial_cost_basis = np.array(
        [
            float(account.cost_basis)
            if account.cost_basis is not None
            else float(account.balance) * 0.50
            for account in accounts
        ],
        dtype=float,
    )
    monthly_contributions = np.array(
        [
            (float(account.annual_contribution) + float(account.employer_match)) / 12.0
            for account in accounts
        ],
        dtype=float,
    )
    owner_codes = np.array(
        [
            0 if account.owner == "self" else 1 if account.owner == "spouse" else -1
            for account in accounts
        ],
        dtype=np.int8,
    )
    tax_codes = np.array(
        [_TAX_TREATMENT_CODES.get(treatment, _TAXABLE) for treatment in tax_treatments],
        dtype=np.int8,
    )
    rmd_eligible = np.array(
        [account.account_type in RMD_ACCOUNT_TYPES for account in accounts],
        dtype=bool,
    )
    self_contribution_indices = tuple(
        idx for idx, account in enumerate(accounts) if account.owner == "self"
    )
    spouse_contribution_indices = tuple(
        idx for idx, account in enumerate(accounts) if account.owner == "spouse"
    )

    tier_order = {tier: idx for idx, tier in enumerate(WITHDRAWAL_TIERS)}
    ordered_indices = np.array(
        sorted(
            range(len(accounts)),
            key=lambda idx: tier_order.get(tax_treatments[idx], 99),
        ),
        dtype=np.intp,
    )
    first_taxable_idx = next(
        (idx for idx, treatment in enumerate(tax_treatments) if treatment == "taxable"),
        -1,
    )

    month_offsets = np.arange(num_months, dtype=np.int32)
    year_indices = month_offsets // 12
    month_numbers = (month_offsets % 12) + 1
    year_numbers = det_engine.start_year + year_indices
    self_ages = profile.self_person.current_age + year_indices
    spouse_ages = profile.spouse.current_age + year_indices
    self_working = self_ages < profile.self_person.retirement_age
    spouse_working = spouse_ages < profile.spouse.retirement_age

    annual_salary = sum(float(src.annual_amount) for src in profile.incomes)
    bootstrap_eff_rate = calculate_taxes(
        ordinary_income=annual_salary,
        filing_status=profile.filing_status,
    ).effective_rate

    income_totals = np.asarray(det_engine.precomputed_income_totals, dtype=float)
    rental_net = np.asarray(det_engine.precomputed_rental_net, dtype=float)
    ss_totals = (
        np.asarray(det_engine.precomputed_ss_self, dtype=float)
        + np.asarray(det_engine.precomputed_ss_spouse, dtype=float)
    )
    taxable_monthly_income = income_totals - rental_net
    ordinary_income_by_month = taxable_monthly_income - ss_totals
    annual_income_ordinary = ordinary_income_by_month.reshape(-1, 12).sum(axis=1)
    annual_ss = ss_totals.reshape(-1, 12).sum(axis=1)
    start_net_worth = float(
        initial_balances.sum()
        + sum(float(prop.net_equity) for prop in profile.properties)
    )

    return _FastMonteCarloContext(
        num_months=num_months,
        years=years,
        filing_status=profile.filing_status,
        income_totals=income_totals,
        expense_totals=np.asarray(det_engine.precomputed_expense_totals, dtype=float),
        re_equity=np.asarray(det_engine.precomputed_re_equity, dtype=float),
        taxable_monthly_income=taxable_monthly_income,
        annual_income_ordinary=annual_income_ordinary,
        annual_ss=annual_ss,
        month_numbers=month_numbers,
        year_numbers=year_numbers,
        year_indices=year_indices,
        self_ages=self_ages,
        spouse_ages=spouse_ages,
        self_working=self_working,
        spouse_working=spouse_working,
        both_retired=np.logical_not(np.logical_or(self_working, spouse_working)),
        initial_balances=initial_balances,
        initial_cost_basis=initial_cost_basis,
        monthly_contributions=monthly_contributions,
        owner_codes=owner_codes,
        tax_codes=tax_codes,
        rmd_eligible=rmd_eligible,
        ordered_indices=ordered_indices,
        self_contribution_indices=self_contribution_indices,
        spouse_contribution_indices=spouse_contribution_indices,
        account_names=[account.name for account in accounts],
        first_taxable_idx=first_taxable_idx,
        start_net_worth=start_net_worth,
        bootstrap_eff_rate=float(bootstrap_eff_rate),
        det_annual_nw=det_annual_nw,
    )


def _withdraw_account_fast(
    balances: np.ndarray,
    cost_basis: np.ndarray,
    idx: int,
    amount: float,
    tax_code: int,
) -> tuple[float, float]:
    """Withdraw from one primitive account and return (withdrawn, capital_gain)."""
    if amount <= 0.0:
        return 0.0, 0.0

    balance = float(balances[idx])
    if balance <= 0.0:
        return 0.0, 0.0

    actual = amount if amount < balance else balance
    cap_gain = 0.0

    if tax_code == _TAXABLE:
        basis = float(cost_basis[idx])
        if basis < balance:
            gain_frac = (balance - basis) / balance
            if gain_frac > 1.0:
                gain_frac = 1.0
            cap_gain = actual * gain_frac
            basis_used = actual - cap_gain
        else:
            basis_used = actual
        new_basis = basis - basis_used
        cost_basis[idx] = new_basis if new_basis > 0.0 else 0.0

    balances[idx] = balance - actual
    return actual, cap_gain


def _withdraw_in_order_fast(
    balances: np.ndarray,
    cost_basis: np.ndarray,
    ordered_indices: np.ndarray,
    tax_codes: np.ndarray,
    need: float,
) -> tuple[float, float]:
    """Satisfy a withdrawal need in tier order. Returns (withdrawn, gains)."""
    remaining = need if need > 0.0 else 0.0
    total_withdrawn = 0.0
    total_gains = 0.0

    for raw_idx in ordered_indices:
        if remaining <= 0.0:
            break
        idx = int(raw_idx)
        withdrawn, cap_gain = _withdraw_account_fast(
            balances,
            cost_basis,
            idx,
            remaining,
            int(tax_codes[idx]),
        )
        if withdrawn <= 0.0:
            continue
        total_withdrawn += withdrawn
        total_gains += cap_gain
        remaining -= withdrawn

    return total_withdrawn, total_gains


def _execute_annual_withdrawals_fast(
    context: _FastMonteCarloContext,
    balances: np.ndarray,
    cost_basis: np.ndarray,
    prior_balance_by_name: dict[str, float],
    net_need: float,
    self_age: int,
    spouse_age: int,
) -> tuple[float, float, float]:
    """
    Primitive equivalent of execute_annual_withdrawals for Monte Carlo trials.

    Returns (total_withdrawn, total_capital_gains, total_rmd).
    """
    net_need = net_need if net_need > 0.0 else 0.0
    total_withdrawn = 0.0
    total_gains = 0.0
    total_rmd = 0.0

    for idx, is_rmd_eligible in enumerate(context.rmd_eligible):
        if not bool(is_rmd_eligible):
            continue

        owner_code = int(context.owner_codes[idx])
        age = self_age if owner_code == 0 else spouse_age if owner_code == 1 else 0
        period = distribution_period(age)
        if period is None:
            continue

        balance_for_rmd = prior_balance_by_name.get(
            context.account_names[idx],
            float(balances[idx]),
        )
        if balance_for_rmd <= 0.0:
            continue

        rmd_amount = balance_for_rmd / period
        withdrawn, cap_gain = _withdraw_account_fast(
            balances,
            cost_basis,
            idx,
            rmd_amount,
            int(context.tax_codes[idx]),
        )
        total_rmd += withdrawn
        total_withdrawn += withdrawn
        total_gains += cap_gain

    remaining_need = net_need - total_rmd
    if remaining_need < 0.0:
        remaining_need = 0.0

    if total_rmd > net_need and context.first_taxable_idx >= 0:
        rmd_excess = total_rmd - net_need
        deposit_idx = context.first_taxable_idx
        balances[deposit_idx] += rmd_excess
        cost_basis[deposit_idx] += rmd_excess

    if remaining_need > 0.0:
        withdrawn, cap_gain = _withdraw_in_order_fast(
            balances,
            cost_basis,
            context.ordered_indices,
            context.tax_codes,
            remaining_need,
        )
        total_withdrawn += withdrawn
        total_gains += cap_gain

    return total_withdrawn, total_gains, total_rmd


def _compute_trailing_12m_returns(monthly_returns: np.ndarray) -> np.ndarray:
    """Return trailing 12-month returns aligned to ProjectionEngine logic."""
    num_months = len(monthly_returns)
    trailing = np.zeros(num_months, dtype=float)
    if num_months <= 12:
        return trailing

    cumulative = np.empty(num_months + 1, dtype=float)
    cumulative[0] = 0.0
    np.cumsum(np.log1p(monthly_returns[:num_months]), out=cumulative[1:])
    window_logs = cumulative[12:num_months] - cumulative[: num_months - 12]
    trailing[12:] = np.exp(window_logs) - 1.0
    return trailing


def _run_fast_trial(
    context: _FastMonteCarloContext,
    monthly_returns: np.ndarray,
    adaptive_spending: bool = False,
) -> tuple[np.ndarray, Optional[int]]:
    """Run one Monte Carlo trial using primitive account arrays."""
    balances = context.initial_balances.copy()
    cost_basis = context.initial_cost_basis.copy()
    num_years = len(context.years)
    eoy_net_worths = np.empty(num_years, dtype=float)
    ruin_year: Optional[int] = None

    prior_eff_rate = context.bootstrap_eff_rate
    eff_rate_eoy = context.bootstrap_eff_rate
    year_cap_gains = 0.0
    year_rmd = 0.0

    prior_balance_by_name = {
        name: float(balances[idx])
        for idx, name in enumerate(context.account_names)
    }
    trial_eoy_net_worths: dict[int, float] = {}
    trailing_returns = (
        _compute_trailing_12m_returns(monthly_returns)
        if adaptive_spending
        else None
    )

    for m in range(context.num_months):
        month = int(context.month_numbers[m])

        if month == 1:
            if m > 0:
                prev_year_idx = int(context.year_indices[m]) - 1
                tax_result = calculate_taxes(
                    ordinary_income=float(context.annual_income_ordinary[prev_year_idx]) + year_rmd,
                    long_term_gains=year_cap_gains,
                    ss_income=float(context.annual_ss[prev_year_idx]),
                    filing_status=context.filing_status,
                )
                eff_rate_eoy = tax_result.effective_rate

            prior_eff_rate = eff_rate_eoy
            year_cap_gains = 0.0
            year_rmd = 0.0
            prior_balance_by_name = {
                name: float(balances[idx])
                for idx, name in enumerate(context.account_names)
            }

        income_total = float(context.income_totals[m])
        base_expense_total = float(context.expense_totals[m])

        mult = 1.0
        if adaptive_spending and trailing_returns is not None and m >= 12:
            annual_return = float(trailing_returns[m])
            if annual_return <= 0.0:
                norm = annual_return / 0.20
                if norm < -1.0:
                    norm = -1.0
                mult = 1.0 + norm * 0.15
            else:
                year = int(context.year_numbers[m])
                prior_year = year - 1
                trial_prior_nw = trial_eoy_net_worths.get(
                    prior_year,
                    context.start_net_worth,
                )
                baseline_prior_nw = context.det_annual_nw.get(
                    prior_year,
                    context.start_net_worth,
                )
                if trial_prior_nw >= baseline_prior_nw:
                    norm = annual_return / 0.20
                    if norm > 1.0:
                        norm = 1.0
                    mult = 1.0 + norm * 0.07

        expense_total = base_expense_total * mult

        if bool(context.self_working[m]):
            for idx in context.self_contribution_indices:
                contribution = float(context.monthly_contributions[idx])
                balances[idx] += contribution
                cost_basis[idx] += contribution

        if bool(context.spouse_working[m]):
            for idx in context.spouse_contribution_indices:
                contribution = float(context.monthly_contributions[idx])
                balances[idx] += contribution
                cost_basis[idx] += contribution

        monthly_rate = float(monthly_returns[m])
        balances += balances * monthly_rate

        taxable_monthly_income = float(context.taxable_monthly_income[m])
        if prior_eff_rate > 0.0 and taxable_monthly_income > 0.0:
            monthly_tax_est = taxable_monthly_income * prior_eff_rate
        else:
            monthly_tax_est = 0.0

        monthly_need = expense_total - income_total + monthly_tax_est
        if monthly_need < 0.0:
            monthly_need = 0.0

        wd_gains = 0.0
        wd_rmd = 0.0

        if bool(context.both_retired[m]) and monthly_need > 0.0:
            if month == 12:
                _, wd_gains, wd_rmd = _execute_annual_withdrawals_fast(
                    context,
                    balances,
                    cost_basis,
                    prior_balance_by_name,
                    monthly_need * 12.0,
                    int(context.self_ages[m]),
                    int(context.spouse_ages[m]),
                )
            else:
                _, wd_gains = _withdraw_in_order_fast(
                    balances,
                    cost_basis,
                    context.ordered_indices,
                    context.tax_codes,
                    monthly_need,
                )

        year_cap_gains += wd_gains
        year_rmd += wd_rmd

        if month == 12:
            year_idx = int(context.year_indices[m])
            net_worth = float(balances.sum() + context.re_equity[m])
            eoy_net_worths[year_idx] = net_worth
            year = int(context.year_numbers[m])
            trial_eoy_net_worths[year] = net_worth
            if net_worth <= 0.0 and ruin_year is None:
                ruin_year = year

    return eoy_net_worths, ruin_year


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def _build_partial_result(
    terminal_net_worths: np.ndarray | list[float],
    ruin_years: list[int | None],
    all_annual_nw: np.ndarray | list[list[float]],
    all_annual_dfs: Optional[list[pd.DataFrame]],
    det_annual: pd.DataFrame,
    years: list[int],
    num_months: int,
    start_year: int,
    config: MonteCarloConfig,
    n_completed: int,
) -> MonteCarloResult:
    """Build a MonteCarloResult from the trials completed so far."""
    nw_array   = np.array(all_annual_nw)
    term_array = np.array(terminal_net_worths)

    success_rate = float(np.mean(term_array > 0))

    pct_labels = [5, 10, 25, 50, 75, 90, 95]
    pct_matrix = np.percentile(nw_array, pct_labels, axis=0)
    net_worth_percentiles = pct_matrix.T.tolist()

    sorted_indices = np.argsort(term_array)
    worst_idx      = int(sorted_indices[0])
    best_idx       = int(sorted_indices[-1])
    p50 = float(np.percentile(term_array, 50))
    median_idx = int(np.argmin(np.abs(term_array - p50)))

    worst_trial_df = []
    best_trial_df = []
    median_trial_df = []

    if all_annual_dfs and len(all_annual_dfs) > max(worst_idx, best_idx, median_idx):
        worst_trial_df = all_annual_dfs[worst_idx].to_dict("records")
        best_trial_df = all_annual_dfs[best_idx].to_dict("records")
        median_trial_df = all_annual_dfs[median_idx].to_dict("records")

    return MonteCarloResult(
        num_trials=n_completed,
        num_months=num_months,
        start_year=start_year,
        years=years,
        success_rate=float(success_rate),
        terminal_net_worths=[float(x) for x in sorted(terminal_net_worths)],
        percentile_labels=pct_labels,
        net_worth_percentiles=net_worth_percentiles,
        median_trial_df=median_trial_df,
        worst_trial_df=worst_trial_df,
        best_trial_df=best_trial_df,
        deterministic_df=det_annual.to_dict("records"),
        ruin_years=[int(x) if x is not None else None for x in ruin_years],
        num_trials_actual=n_completed,
        mean_return_pct=float(config.mean_return_pct),
        std_dev_pct=float(config.std_dev_pct),
    )


def run_monte_carlo(
    profile: PlanProfile,
    progress_callback: Optional[callable] = None,
    intermediate_callback: Optional[callable] = None,
    intermediate_interval: int = 0,
    adaptive_spending: Optional[bool] = None,
) -> MonteCarloResult:
    """
    Run the full Monte Carlo simulation for ``profile``.

    Parameters
    ----------
    profile           : PlanProfile containing Monte Carlo config in ``profile.monte_carlo``
    progress_callback : optional callable(current_trial: int, total_trials: int).
                        Called every 25 trials so callers can report progress.
                        Used by the Dash background callback to update the progress bar.
    intermediate_callback : optional callable(result: MonteCarloResult).
                        Called every ``intermediate_interval`` trials with partial
                        aggregated results for live chart updates.
    intermediate_interval : int, how many trials between intermediate updates.
                        0 means no intermediate updates (wait until end).
    adaptive_spending : bool, if True, scale monthly expenses based on trailing
                        market returns (cut up to 15% in bad years, boost up to
                        7% in good years). If None, uses profile.monte_carlo.

    Returns
    -------
    MonteCarloResult with all aggregated statistics and select trial DataFrames.
    """
    config = profile.monte_carlo
    if adaptive_spending is None:
        adaptive_spending = bool(getattr(config, "adaptive_spending", False))
    n      = config.num_trials

    # ── Step 1: Deterministic baseline ───────────────────────────────────
    det_engine = ProjectionEngine(profile)
    _, det_annual = det_engine.run()
    start_year  = det_engine.start_year
    num_months  = (profile.plan_end_year - start_year + 1) * 12
    years       = sorted(det_annual["year"].unique().tolist())
    det_annual_nw = dict(zip(det_annual["year"], det_annual["net_worth_eoy"]))

    # ── Step 2: Generate all return sequences at once ─────────────────────
    equity_seqs, bond_seqs = generate_return_sequences(config, num_months)

    # ── Step 3: Vectorized blending (all trials at once) ──────────────────
    all_blended = _build_all_blended_sequences(equity_seqs, bond_seqs, profile)

    # Step 4: Run N trials using the primitive Monte Carlo fast kernel.
    fast_context = _build_fast_context(
        profile=profile,
        det_engine=det_engine,
        det_annual_nw=det_annual_nw,
        years=years,
        num_months=num_months,
    )
    terminal_net_worths = np.empty(n, dtype=float)
    ruin_years: list[Optional[int]] = [None] * n
    all_annual_nw = np.empty((n, len(years)), dtype=float)

    _PROGRESS_INTERVAL = 25   # report every N trials

    for trial_idx in range(n):
        eoy_nws, r_yr = _run_fast_trial(
            fast_context,
            all_blended[trial_idx],
            adaptive_spending=adaptive_spending,
        )

        terminal_nw = float(eoy_nws[-1])
        terminal_net_worths[trial_idx] = terminal_nw
        ruin_years[trial_idx] = r_yr
        all_annual_nw[trial_idx] = eoy_nws

        completed = trial_idx + 1

        # Report progress every _PROGRESS_INTERVAL trials and on the last trial
        if progress_callback is not None:
            if completed % _PROGRESS_INTERVAL == 0 or completed == n:
                progress_callback(completed, n)

        # Fire intermediate results callback (passes None/empty for detailed dfs)
        if (intermediate_callback is not None
                and intermediate_interval > 0
                and completed >= 2  # need at least 2 trials for percentiles
                and completed % intermediate_interval == 0
                and completed != n):  # skip on last trial (final result handles it)
            partial = _build_partial_result(
                terminal_net_worths[:completed],
                ruin_years[:completed],
                all_annual_nw[:completed],
                None, det_annual, years, num_months,
                start_year, config, completed,
            )
            intermediate_callback(partial)

    # ── Step 5: Re-run Worst, Best, and Median trials in High-Detail ──────
    term_array = np.array(terminal_net_worths)
    sorted_indices = np.argsort(term_array)
    worst_idx      = int(sorted_indices[0])
    best_idx       = int(sorted_indices[-1])
    p50 = float(np.percentile(term_array, 50))
    median_idx = int(np.argmin(np.abs(term_array - p50)))

    detailed_dfs = {}
    for idx in {worst_idx, best_idx, median_idx}:
        slow_engine = ProjectionEngine(
            profile,
            return_overrides=all_blended[idx],
            det_annual_nw=det_annual_nw,
            adaptive_spending=adaptive_spending,
            precomputed_salary_self=det_engine.precomputed_salary_self,
            precomputed_salary_spouse=det_engine.precomputed_salary_spouse,
            precomputed_ss_self=det_engine.precomputed_ss_self,
            precomputed_ss_spouse=det_engine.precomputed_ss_spouse,
            precomputed_expenses=det_engine.precomputed_expenses,
            precomputed_expense_totals=det_engine.precomputed_expense_totals,
            precomputed_re_equity=det_engine.precomputed_re_equity,
            precomputed_rental_net=det_engine.precomputed_rental_net,
            precomputed_income_totals=det_engine.precomputed_income_totals,
        )
        _, slow_annual_df = slow_engine.run(fast_path=False)
        detailed_dfs[idx] = slow_annual_df

    # Construct mock all_annual_dfs containing only the target trial DataFrames
    all_annual_dfs_mock = [None] * n
    for idx, df in detailed_dfs.items():
        all_annual_dfs_mock[idx] = df

    # ── Step 6: Aggregate final statistics ────────────────────────────────
    return _build_partial_result(
        terminal_net_worths, ruin_years, all_annual_nw,
        all_annual_dfs_mock, det_annual, years, num_months,
        start_year, config, n,
    )



def monte_carlo_result_to_dict(result: MonteCarloResult) -> dict:
    """Serialize MonteCarloResult to a JSON-safe dict for dcc.Store."""
    from dataclasses import asdict
    return asdict(result)
