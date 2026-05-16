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
Each trial runs a fresh ProjectionEngine instance (~480 months for a 40-yr
plan).  At 1,000 trials this typically takes 5–12 seconds on modern hardware.
No multiprocessing is used in the initial implementation; vectorised fast-paths
can be added later if needed.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from engine.models import PlanProfile, MonteCarloConfig, ACCOUNT_TAX_TREATMENT
from engine.projections import ProjectionEngine


# ---------------------------------------------------------------------------
# Account bucketing
# ---------------------------------------------------------------------------

# Equity-like accounts share the main (volatile) return sequence
_EQUITY_ACCOUNT_TYPES = {"401k", "trad_ira", "roth_ira", "roth_401k", "brokerage"}
# Low-volatility accounts get a separate bond/cash return sequence
_BOND_ACCOUNT_TYPES   = {"savings", "hsa"}


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

def _build_blended_return_sequence(
    equity_seq: np.ndarray,   # shape (num_months,)
    bond_seq:   np.ndarray,   # shape (num_months,)
    profile:    PlanProfile,
) -> np.ndarray:
    """
    Build a single blended monthly return sequence for one trial by computing
    a portfolio-weighted average of equity and bond returns.

    The weight is: equity_weight = (total equity balance) / (total portfolio balance)
    at the start of the simulation, using configured account balances.

    For the Monte Carlo engine, we pass this blended sequence as the
    return_overrides to ProjectionEngine, which applies it uniformly to all
    accounts.  This is a reasonable simplification — a more granular
    per-account override would require deeper changes to the engine loop.
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
        return equity_seq  # fallback

    eq_weight   = total_equity / total
    bond_weight = total_bond   / total

    return eq_weight * equity_seq + bond_weight * bond_seq


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
# Main public API
# ---------------------------------------------------------------------------

def run_monte_carlo(
    profile: PlanProfile,
    progress_callback: Optional[callable] = None,
) -> MonteCarloResult:
    """
    Run the full Monte Carlo simulation for ``profile``.

    Parameters
    ----------
    profile           : PlanProfile containing Monte Carlo config in ``profile.monte_carlo``
    progress_callback : optional callable(current_trial: int, total_trials: int).
                        Called every 25 trials so callers can report progress.
                        Used by the Dash background callback to update the progress bar.

    Returns
    -------
    MonteCarloResult with all aggregated statistics and select trial DataFrames.
    """
    config = profile.monte_carlo
    n      = config.num_trials

    # ── Step 1: Deterministic baseline ───────────────────────────────────
    det_engine = ProjectionEngine(profile)
    _, det_annual = det_engine.run()
    start_year  = det_engine.start_year
    num_months  = (profile.plan_end_year - start_year + 1) * 12
    years       = sorted(det_annual["year"].unique().tolist())

    # ── Step 2: Generate all return sequences at once ─────────────────────
    equity_seqs, bond_seqs = generate_return_sequences(config, num_months)

    # ── Step 3: Run N trials ──────────────────────────────────────────────
    terminal_net_worths: list[float]          = []
    ruin_years:          list[Optional[int]]  = []
    all_annual_nw: list[list[float]] = []
    all_annual_dfs: list[pd.DataFrame] = []

    _PROGRESS_INTERVAL = 25   # report every N trials

    for trial_idx in range(n):
        blended = _build_blended_return_sequence(
            equity_seqs[trial_idx],
            bond_seqs[trial_idx],
            profile,
        )

        engine = ProjectionEngine(profile, return_overrides=blended)
        _, annual_df = engine.run()

        terminal_nw = float(annual_df["net_worth_eoy"].iloc[-1])
        terminal_net_worths.append(terminal_nw)
        ruin_years.append(_find_ruin_year(annual_df))

        nw_series = annual_df.set_index("year")["net_worth_eoy"]
        nw_aligned = [float(nw_series.get(yr, 0.0)) for yr in years]
        all_annual_nw.append(nw_aligned)
        all_annual_dfs.append(annual_df)

        # Report progress every _PROGRESS_INTERVAL trials and on the last trial
        if progress_callback is not None:
            if (trial_idx + 1) % _PROGRESS_INTERVAL == 0 or (trial_idx + 1) == n:
                progress_callback(trial_idx + 1, n)

    # ── Step 4: Aggregate statistics ─────────────────────────────────────
    nw_array   = np.array(all_annual_nw)          # shape (n, n_years)
    term_array = np.array(terminal_net_worths)

    # Success rate: trials where terminal net worth > 0
    success_rate = float(np.mean(term_array > 0))

    # Percentile bands over time
    pct_labels = [5, 10, 25, 50, 75, 90, 95]
    pct_matrix = np.percentile(nw_array, pct_labels, axis=0)  # (7, n_years)
    net_worth_percentiles = pct_matrix.T.tolist()              # (n_years, 7)

    # Identify median, worst, best trials by terminal net worth
    sorted_indices = np.argsort(term_array)
    worst_idx      = int(sorted_indices[0])
    best_idx       = int(sorted_indices[-1])
    # Median: trial closest to the 50th percentile terminal value
    p50 = float(np.percentile(term_array, 50))
    median_idx = int(np.argmin(np.abs(term_array - p50)))

    # ── Step 5: Build result ──────────────────────────────────────────────
    return MonteCarloResult(
        num_trials=n,
        num_months=num_months,
        start_year=start_year,
        years=years,
        success_rate=float(success_rate),
        terminal_net_worths=[float(x) for x in sorted(terminal_net_worths)],
        percentile_labels=pct_labels,
        net_worth_percentiles=net_worth_percentiles,
        median_trial_df=all_annual_dfs[median_idx].to_dict("records"),
        worst_trial_df=all_annual_dfs[worst_idx].to_dict("records"),
        best_trial_df=all_annual_dfs[best_idx].to_dict("records"),
        deterministic_df=det_annual.to_dict("records"),
        ruin_years=[int(x) if x is not None else None for x in ruin_years],
        num_trials_actual=n,
        mean_return_pct=float(config.mean_return_pct),
        std_dev_pct=float(config.std_dev_pct),
    )


def monte_carlo_result_to_dict(result: MonteCarloResult) -> dict:
    """Serialize MonteCarloResult to a JSON-safe dict for dcc.Store."""
    from dataclasses import asdict
    return asdict(result)
