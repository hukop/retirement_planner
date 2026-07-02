"""
Withdrawal ordering and RMD engine.

Withdrawal Strategy
-------------------
Implements the standard tax-efficient sequential withdrawal order:

  1. Satisfy RMDs (Required Minimum Distributions) first — forced from all
     tax-deferred accounts (401k, trad_ira) once the owner reaches their
     RMD start age (72, 73, or 75 depending on birth year — SECURE Act 2.0).
     RMD amounts are based on the IRS 2022 Uniform Lifetime Table.

  2. If the RMD amount already covers or exceeds the net withdrawal need,
     no further withdrawals are made.  Any RMD excess is deposited into the
     first available taxable account (brokerage/savings) as a cash deposit.

  3. Remaining withdrawal need is satisfied in this order:
       a. Taxable accounts (brokerage, savings)   — pays LTCG rates, basis depleted
       b. Tax-deferred accounts (401k, trad_ira)  — ordinary income
       c. Tax-free accounts (roth_ira, roth_401k, hsa) — no tax

  4. Within each tier, accounts are drawn down in the order they appear in
     the portfolio list (preserving user ordering).

Net Withdrawal Need
-------------------
The caller (projections.py) computes:

    net_need = monthly_expenses + monthly_taxes_estimate
               - monthly_income (SS + rental + other)

Only the amount needed *after* income sources are subtracted is withdrawn.
This module does not recalculate taxes — it accepts the pre-computed need.

RMD Rules (SECURE Act 2.0 — staggered by birth year)
-------------------------------------------------------
The RMD starting age depends on the account owner's birth year:

  Born 1950 or earlier : age 72  (pre-SECURE 2.0 / original SECURE Act)
  Born 1951 – 1959     : age 73  (SECURE 2.0, effective 2023)
  Born 1960 or later   : age 75  (SECURE 2.0, effective 2033)

- Annual RMD = prior year-end balance / distribution_period (Uniform Lifetime Table).
- Applies separately to each tax-deferred account.
- Roth IRAs have no RMDs during the owner's lifetime.
- Roth 401k accounts are also exempt from RMDs (post-2024 rule).
- If aggregated RMDs exceed what is needed, the surplus is treated as income
  and deposited into a taxable account (not re-contributed to retirement accounts).

Reference: IRS Publication 590-B, Appendix B — Uniform Lifetime Table (2022+)
SECURE 2.0 Act of 2022 (Pub. L. 117-328), §107 and §202
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.investments import AccountState, WithdrawalResult


# ---------------------------------------------------------------------------
# IRS 2022 Uniform Lifetime Table
# ---------------------------------------------------------------------------
# Maps age → distribution period (divisor for RMD calculation).
# Ages below 72 are not in the table (no RMD required).
# Age 120 and above use 2.0 (same as the table's floor).
_UNIFORM_LIFETIME_TABLE: dict[int, float] = {
    72:  27.4,
    73:  26.5,
    74:  25.5,
    75:  24.6,
    76:  23.7,
    77:  22.9,
    78:  22.0,
    79:  21.1,
    80:  20.2,
    81:  19.4,
    82:  18.5,
    83:  17.7,
    84:  16.8,
    85:  16.0,
    86:  15.2,
    87:  14.4,
    88:  13.7,
    89:  12.9,
    90:  12.2,
    91:  11.5,
    92:  10.8,
    93:  10.1,
    94:   9.5,
    95:   8.9,
    96:   8.4,
    97:   7.8,
    98:   7.3,
    99:   6.8,
    100:  6.4,
    101:  6.0,
    102:  5.6,
    103:  5.2,
    104:  4.9,
    105:  4.6,
    106:  4.3,
    107:  4.1,
    108:  3.9,
    109:  3.7,
    110:  3.5,
    111:  3.4,
    112:  3.3,
    113:  3.1,
    114:  3.0,
    115:  2.9,
}

# Account types subject to RMDs
RMD_ACCOUNT_TYPES = {"401k", "trad_ira"}
# Note: roth_401k is exempt post-2024; roth_ira has never had RMDs.


def rmd_start_age(birth_year: int) -> int:
    """
    Return the Required Minimum Distribution start age for an account owner
    based on their birth year, per the SECURE Act 2.0 (Pub. L. 117-328).

    Birth year  → RMD start age
    -----------   -------------
    ≤ 1950      → 72  (original SECURE Act / pre-SECURE 2.0)
    1951–1959   → 73  (SECURE 2.0, effective 2023)
    ≥ 1960      → 75  (SECURE 2.0, effective 2033)
    """
    if birth_year <= 1950:
        return 72
    elif birth_year <= 1959:
        return 73
    else:
        return 75


# ---------------------------------------------------------------------------
# Withdrawal tier ordering
# ---------------------------------------------------------------------------
WITHDRAWAL_TIERS: list[str] = [
    "taxable",       # brokerage, savings — first
    "tax_deferred",  # 401k, trad_ira   — second
    "tax_free",      # roth, hsa         — last (preserve longest)
]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass
class RMDResult:
    """Annual RMD summary for one account."""
    account_name:  str
    account_type:  str
    owner:         str
    age_at_rmd:    int
    distribution_period: float
    rmd_amount:    float          # required amount
    actual_withdrawn: float       # may equal rmd_amount (unless balance < rmd)
    withdrawal:    WithdrawalResult


@dataclass
class AnnualWithdrawalPlan:
    """
    Output of ``execute_annual_withdrawals()`` — full picture of
    withdrawals for one simulation year.
    """
    net_need:               float   # what the caller asked for (annual)
    total_withdrawn:        float   # total gross withdrawn
    total_ordinary_income:  float   # sum of ordinary income from withdrawals
    total_capital_gains:    float   # sum of LTCG from brokerage withdrawals
    total_rmd:              float   # total RMD satisfied
    rmd_excess:             float   # RMD surplus deposited to taxable account
    shortfall:              float   # unmet withdrawal need (accounts exhausted)
    withdrawals:            list[WithdrawalResult] = field(default_factory=list)
    rmd_results:            list[RMDResult]        = field(default_factory=list)


# ---------------------------------------------------------------------------
# RMD helpers
# ---------------------------------------------------------------------------
def distribution_period(age: int, birth_year: int) -> float | None:
    """
    Return the IRS Uniform Lifetime Table distribution period for ``age``.
    Returns None if age < rmd_start_age(birth_year) (no RMD required).
    For ages above 115, uses 2.9 (the table floor).

    Parameters
    ----------
    age        : owner's age in the distribution year
    birth_year : owner's birth year, used to determine the correct RMD
                 start age per SECURE Act 2.0 (72 / 73 / 75).
    """
    if age < rmd_start_age(birth_year):
        return None
    return _UNIFORM_LIFETIME_TABLE.get(age, 2.9)


def annual_rmd(account_balance: float, age: int, birth_year: int) -> float:
    """
    Compute the Required Minimum Distribution for a single tax-deferred account.

    Parameters
    ----------
    account_balance : prior year-end balance of the account
    age             : owner's age at the end of the distribution year
    birth_year      : owner's birth year — determines the RMD start age
                      per SECURE Act 2.0 (born ≤1950 → 72, 1951–1959 → 73,
                      ≥1960 → 75).

    Returns
    -------
    Annual RMD amount in dollars, or 0.0 if no RMD is required.
    """
    period = distribution_period(age, birth_year)
    if period is None or account_balance <= 0:
        return 0.0
    return account_balance / period


def compute_rmd_withdrawals(
    portfolio:        list[AccountState],
    owner_ages:       dict[str, int],          # {"self": 74, "spouse": 72}
    owner_birth_years: dict[str, int],         # {"self": 1958, "spouse": 1962}
    prior_balances:   dict[str, float] | None = None,
) -> list[RMDResult]:
    """
    Compute and execute mandatory RMD withdrawals for all eligible accounts.

    Parameters
    ----------
    portfolio          : list of AccountState objects
    owner_ages         : mapping from owner key → age *in the projection year*
    owner_birth_years  : mapping from owner key → birth year, used to determine
                         the correct RMD start age per SECURE Act 2.0
                         (born ≤1950 → 72, 1951–1959 → 73, ≥1960 → 75).
    prior_balances     : mapping from account *name* → balance at the prior
                         year-end.  The IRS requires RMDs to be calculated from
                         the December 31 balance of the *preceding* year.  If
                         None (or the account name is missing from the dict),
                         the current live balance is used as a fallback.

    Returns
    -------
    List of RMDResult, one per account that had a positive RMD.
    Accounts with no RMD requirement are skipped.
    """
    results: list[RMDResult] = []

    for state in portfolio:
        if state.account_type not in RMD_ACCOUNT_TYPES:
            continue

        owner      = state.account.owner
        age        = owner_ages.get(owner, 0)
        birth_year = owner_birth_years.get(owner, 1960)  # default: most restrictive start age

        # IRS rule: RMD = prior year-end balance / distribution period.
        # Fall back to live balance if prior_balances not supplied.
        balance_for_rmd = (
            prior_balances.get(state.name, state.balance)
            if prior_balances is not None
            else state.balance
        )
        rmd = annual_rmd(balance_for_rmd, age, birth_year)

        if rmd <= 0:
            continue

        period = distribution_period(age, birth_year)
        wr     = state.withdraw(rmd)

        results.append(RMDResult(
            account_name=state.name,
            account_type=state.account_type,
            owner=owner,
            age_at_rmd=age,
            distribution_period=period,
            rmd_amount=rmd,
            actual_withdrawn=wr.withdrawn,
            withdrawal=wr,
        ))

    return results


# ---------------------------------------------------------------------------
# Sequential withdrawal logic
# ---------------------------------------------------------------------------
def _accounts_in_order(portfolio: list[AccountState]) -> list[AccountState]:
    """
    Return portfolio accounts sorted into withdrawal tier order
    (taxable → tax_deferred → tax_free), preserving user ordering within each tier.
    """
    order = {tier: i for i, tier in enumerate(WITHDRAWAL_TIERS)}
    return sorted(portfolio, key=lambda s: order.get(s.tax_treatment, 99))


def _find_taxable_deposit_account(portfolio: list[AccountState]) -> AccountState | None:
    """Return the first brokerage/savings account for RMD overflow deposits."""
    for s in portfolio:
        if s.tax_treatment == "taxable":
            return s
    return None


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------
def execute_annual_withdrawals(
    portfolio:         list[AccountState],
    net_need:          float,                    # annual dollar need (after income sources)
    owner_ages:        dict[str, int],           # e.g. {"self": 74, "spouse": 72}
    owner_birth_years: dict[str, int],           # e.g. {"self": 1955, "spouse": 1962}
    prior_balances:    dict[str, float] | None = None,
) -> AnnualWithdrawalPlan:
    """
    Execute the full annual withdrawal strategy for one projection year.

    Steps
    -----
    1. Compute and force RMDs from all eligible tax-deferred accounts,
       using ``prior_balances`` for the IRS-mandated prior year-end balance.
    2. Accumulate RMD withdrawals as ordinary income.
    3. If RMD total >= net_need: deposit surplus into taxable; done.
    4. Otherwise satisfy remaining need via sequential tier withdrawal.
    5. Tally all income types for the tax engine.

    Parameters
    ----------
    portfolio          : list of AccountState (modified in place)
    net_need           : annual net withdrawal needed (expenses − income sources)
    owner_ages         : dict mapping owner key to integer age this year
    owner_birth_years  : dict mapping owner key to birth year; used to determine
                         each person's RMD start age per SECURE Act 2.0
                         (born ≤1950 → 72, 1951–1959 → 73, ≥1960 → 75).
    prior_balances     : dict mapping account *name* → December 31 balance of
                         the *prior* year, used for IRS-correct RMD amounts.
                         If None, the current live balance is used (less accurate).

    Returns
    -------
    AnnualWithdrawalPlan with complete withdrawal breakdown.
    """
    net_need = max(0.0, net_need)
    all_withdrawals:   list[WithdrawalResult] = []
    total_ordinary     = 0.0
    total_cap_gains    = 0.0
    rmd_excess         = 0.0
    shortfall          = 0.0

    # ── Step 1: RMDs ────────────────────────────────────────────────────
    rmd_results   = compute_rmd_withdrawals(portfolio, owner_ages, owner_birth_years, prior_balances)
    total_rmd     = sum(r.actual_withdrawn for r in rmd_results)
    rmd_withdrawals = [r.withdrawal for r in rmd_results]
    all_withdrawals.extend(rmd_withdrawals)
    total_ordinary += sum(w.ordinary_income for w in rmd_withdrawals)
    total_cap_gains += sum(w.capital_gain  for w in rmd_withdrawals)

    # ── Step 2: Check if RMDs already cover the need ─────────────────────
    remaining_need = max(0.0, net_need - total_rmd)

    if total_rmd > net_need:
        # Excess RMD: deposit surplus into a taxable account (not re-invested
        # in retirement accounts — IRS does not allow this)
        rmd_excess  = total_rmd - net_need
        deposit_acct = _find_taxable_deposit_account(portfolio)
        if deposit_acct:
            deposit_acct.deposit(rmd_excess)

    # ── Step 3: Sequential tier withdrawals for remaining need ───────────
    if remaining_need > 0:
        ordered = _accounts_in_order(portfolio)

        for state in ordered:
            if remaining_need <= 0:
                break
            # Skip accounts that already had RMD withdrawals this round
            # (they've already contributed to fulfilling the need via RMD path)
            # — but we can still draw additional amounts beyond the RMD if needed
            wr = state.withdraw(remaining_need)
            if wr.withdrawn <= 0:
                continue

            all_withdrawals.append(wr)
            total_ordinary  += wr.ordinary_income
            total_cap_gains += wr.capital_gain
            remaining_need  -= wr.withdrawn

        shortfall = max(0.0, remaining_need)

    total_withdrawn = sum(w.withdrawn for w in all_withdrawals)

    return AnnualWithdrawalPlan(
        net_need=net_need,
        total_withdrawn=total_withdrawn,
        total_ordinary_income=total_ordinary,
        total_capital_gains=total_cap_gains,
        total_rmd=total_rmd,
        rmd_excess=rmd_excess,
        shortfall=shortfall,
        withdrawals=all_withdrawals,
        rmd_results=rmd_results,
    )


# ---------------------------------------------------------------------------
# Monthly wrapper (for projection engine's monthly loop)
# ---------------------------------------------------------------------------
def execute_monthly_withdrawals(
    portfolio:         list[AccountState],
    monthly_need:      float,
    owner_ages:        dict[str, int],
    owner_birth_years: dict[str, int],           # {"self": 1955, "spouse": 1962}
    month_of_year:     int,                      # 1–12: RMDs are only forced in December (month 12)
    prior_balances:    dict[str, float] | None = None,
) -> AnnualWithdrawalPlan:
    """
    Monthly withdrawal wrapper — alternative entry point for callers that
    want month-by-month granularity instead of the annual aggregation used
    by ``ProjectionEngine``.

    Note
    ----
    The main projection engine does NOT call this function — it inlines
    the same logic for performance and to allow per-month bookkeeping.
    This function is provided for external callers (notebooks, unit tests,
    alternative front-ends) that want a clean monthly API.

    RMDs are lump-summed in December only.  ``prior_balances`` should be
    the prior year-end balances (required for IRS-correct RMD calculation;
    see ``execute_annual_withdrawals``).
    """
    if month_of_year == 12:
        return execute_annual_withdrawals(
            portfolio=portfolio,
            net_need=monthly_need * 12,
            owner_ages=owner_ages,
            owner_birth_years=owner_birth_years,
            prior_balances=prior_balances,
        )
    else:
        # Non-December months: simple sequential withdrawal, no RMD forcing
        all_withdrawals: list[WithdrawalResult] = []
        remaining = max(0.0, monthly_need)
        total_ordinary = 0.0
        total_cap_gains = 0.0

        for state in _accounts_in_order(portfolio):
            if remaining <= 0:
                break
            wr = state.withdraw(remaining)
            if wr.withdrawn <= 0:
                continue
            all_withdrawals.append(wr)
            total_ordinary  += wr.ordinary_income
            total_cap_gains += wr.capital_gain
            remaining       -= wr.withdrawn

        total_withdrawn = sum(w.withdrawn for w in all_withdrawals)
        shortfall       = max(0.0, remaining)

        return AnnualWithdrawalPlan(
            net_need=monthly_need,
            total_withdrawn=total_withdrawn,
            total_ordinary_income=total_ordinary,
            total_capital_gains=total_cap_gains,
            total_rmd=0.0,
            rmd_excess=0.0,
            shortfall=shortfall,
            withdrawals=all_withdrawals,
            rmd_results=[],
        )
