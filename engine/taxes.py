"""
Tax estimation engine — federal (2025) + California (2024/2025).

Design
------
- All inputs/outputs are in nominal dollars (inflation-adjusted by caller if needed).
- Income is split by *type* so the engine can apply the correct rate:

    ordinary_income    — salary, pension, tax-deferred withdrawals, RMDs,
                         net rental income, Social Security (taxable portion),
                         short-term capital gains, interest
    long_term_gains    — qualified dividends + LTCG from brokerage withdrawals
                         (federal uses preferential LTCG brackets; CA taxes as
                         ordinary income — no preferential LTCG rate in CA)
    roth_withdrawals   — always $0 tax (not passed in; excluded by caller)

Key outputs
-----------
  federal_tax, ca_tax, total_tax, effective_rate
  (all as dollar amounts for the year)

Limitations / assumptions
-------------------------
- Married Filing Jointly only in this MVP version.
  (Single / MFS brackets are included but not yet wired to the UI.)
- AMT, NIIT (3.8 % Net Investment Income Tax), and self-employment tax
  are NOT modeled in the MVP.
- Social Security taxability: up to 85 % of SS benefits are taxable
  at the federal level; CA does NOT tax SS at all.
- Standard deduction is always taken (itemizing is a post-MVP feature).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
FilingStatus = Literal["married_jointly", "married_separately", "single"]


# ---------------------------------------------------------------------------
# 2025 Federal tax brackets  (ordinary income)
# Source: IRS Rev. Proc. 2024-40
# ---------------------------------------------------------------------------
FEDERAL_BRACKETS: dict[FilingStatus, list[tuple[float, float]]] = {
    # (upper_limit, marginal_rate)  — last entry has upper_limit = inf
    "married_jointly": [
        (23_850,    0.10),
        (96_950,    0.12),
        (206_700,   0.22),
        (394_600,   0.24),
        (501_050,   0.32),
        (751_600,   0.35),
        (float("inf"), 0.37),
    ],
    "married_separately": [
        (11_925,    0.10),
        (48_475,    0.12),
        (103_350,   0.22),
        (197_300,   0.24),
        (250_525,   0.32),
        (375_800,   0.35),
        (float("inf"), 0.37),
    ],
    "single": [
        (11_925,    0.10),
        (48_475,    0.12),
        (103_350,   0.22),
        (197_300,   0.24),
        (250_525,   0.32),
        (626_350,   0.35),
        (float("inf"), 0.37),
    ],
}

# 2025 Federal standard deductions
FEDERAL_STANDARD_DEDUCTION: dict[FilingStatus, float] = {
    "married_jointly":    30_000,
    "married_separately": 15_000,
    "single":             15_000,
}

# 2025 Federal long-term capital gains brackets
# (lower_threshold, rate) — income above first threshold taxed at 15 %; above second at 20 %
FEDERAL_LTCG_BRACKETS: dict[FilingStatus, list[tuple[float, float]]] = {
    "married_jointly": [
        (96_700,   0.00),
        (600_050,  0.15),
        (float("inf"), 0.20),
    ],
    "married_separately": [
        (48_350,   0.00),
        (300_000,  0.15),
        (float("inf"), 0.20),
    ],
    "single": [
        (48_350,   0.00),
        (533_400,  0.15),
        (float("inf"), 0.20),
    ],
}

# ---------------------------------------------------------------------------
# 2025 California tax brackets  (ordinary income, MFJ)
# Source: FTB Publication 1005 / 2024 540 instructions
# CA has no preferential LTCG rate — all capital gains taxed as ordinary income.
# ---------------------------------------------------------------------------
CA_BRACKETS: dict[FilingStatus, list[tuple[float, float]]] = {
    "married_jointly": [
        (20_824,   0.01),
        (49_368,   0.02),
        (77_918,   0.04),
        (108_162,  0.06),
        (136_700,  0.08),
        (698_274,  0.093),
        (837_922,  0.103),
        (1_000_000, 0.113),
        (float("inf"), 0.123),
    ],
    "married_separately": [
        (10_412,   0.01),
        (24_684,   0.02),
        (38_959,   0.04),
        (54_081,   0.06),
        (68_350,   0.08),
        (349_137,  0.093),
        (418_961,  0.103),
        (1_000_000, 0.113),
        (float("inf"), 0.123),
    ],
    "single": [
        (10_412,   0.01),
        (24_684,   0.02),
        (38_959,   0.04),
        (54_081,   0.06),
        (68_350,   0.08),
        (349_137,  0.093),
        (418_961,  0.103),
        (1_000_000, 0.113),
        (float("inf"), 0.123),
    ],
}

# 2025 California standard deductions (CA has very low standard deductions;
# most people itemize at the state level, but we use standard for MVP simplicity)
CA_STANDARD_DEDUCTION: dict[FilingStatus, float] = {
    "married_jointly":    11_080,
    "married_separately":  5_540,
    "single":              5_540,
}

# CA SDI (State Disability Insurance) — 1.1 % on all wages, no wage cap (2025)
# Not modeled in MVP (applies only to employment wages).

# ---------------------------------------------------------------------------
# Social Security taxability (federal only; CA does not tax SS)
# ---------------------------------------------------------------------------
# IRS combined-income thresholds: (lower, upper) → 0 %, 50 %, 85 % taxable.
# Married Filing Separately (living with spouse): $0/$0 means up to 85% is
# immediately taxable (IRS rules — most unfavorable treatment).
SS_TAXABLE_THRESHOLDS: dict[str, tuple[float, float]] = {
    "married_jointly":    (32_000, 44_000),
    "married_separately": (0,      0),       # MFS with spouse: all SS exposed
    "single":             (25_000, 34_000),
}


# ---------------------------------------------------------------------------
# Helper: bracket-based tax calculator
# ---------------------------------------------------------------------------
def _bracket_tax(income: float, brackets: list[tuple[float, float]]) -> float:
    """
    Compute tax from a progressive bracket schedule.

    Parameters
    ----------
    income   : taxable income (after deductions), must be >= 0
    brackets : list of (upper_limit, rate) pairs in ascending order;
               last entry should have upper_limit = float('inf')

    Returns
    -------
    Total tax as a float.
    """
    if income <= 0:
        return 0.0

    tax = 0.0
    prev_limit = 0.0
    for upper, rate in brackets:
        if income <= prev_limit:
            break
        taxable_in_bracket = min(income, upper) - prev_limit
        tax += taxable_in_bracket * rate
        prev_limit = upper

    return tax


# ---------------------------------------------------------------------------
# Social Security: taxable portion (federal only)
# ---------------------------------------------------------------------------
def ss_taxable_federal(
    ss_income: float,
    other_income: float,
    filing_status: FilingStatus = "married_jointly",
) -> float:
    """
    Return the taxable portion of Social Security benefits.

    Uses the IRS "combined income" formula:
        combined = other_income + 0.5 * ss_income

    Thresholds by filing status:
      MFJ:  combined < $32k → 0%; $32k–$44k → 50%; > $44k → up to 85%
      Single: $25k / $34k thresholds (same tier structure)
      MFS (with spouse): $0 threshold — up to 85% immediately taxable
    """
    if ss_income <= 0:
        return 0.0

    low, high = SS_TAXABLE_THRESHOLDS.get(filing_status, SS_TAXABLE_THRESHOLDS["married_jointly"])

    # Special case: MFS living with spouse — up to 85 % is taxable with no
    # income-based phase-in (IRS: combined income threshold is $0).
    if low == 0 and high == 0:
        return min(0.85 * ss_income, ss_income)

    combined = other_income + 0.5 * ss_income

    if combined <= low:
        return 0.0
    elif combined <= high:
        return min(0.50 * (combined - low), 0.50 * ss_income)
    else:
        # Up to 85 % of SS is taxable
        taxable = min(0.50 * (high - low) + 0.85 * (combined - high), 0.85 * ss_income)
        return taxable


# ---------------------------------------------------------------------------
# Main result dataclass
# ---------------------------------------------------------------------------
@dataclass
class TaxResult:
    """Detailed tax breakdown for a single year."""

    # Inputs (stored for transparency/debugging)
    ordinary_income: float          # wages, deferred withdrawals, rental net, taxable SS
    long_term_gains: float          # LTCG / qualified dividends
    ss_income: float                # total SS received (pre-taxability calc)
    filing_status: FilingStatus

    # Intermediate
    federal_agi: float              # approximate AGI (ordinary + gains + taxable SS)
    federal_taxable_income: float   # after standard deduction
    ca_agi: float                   # CA: ordinary + gains (CA taxes LTCG as ordinary)
    ca_taxable_income: float        # after CA standard deduction

    # Outputs
    federal_ordinary_tax: float     # tax on ordinary income portion
    federal_ltcg_tax: float         # tax on long-term gains portion
    federal_tax: float              # total federal tax
    ca_tax: float                   # total CA income tax
    total_tax: float                # federal + CA

    @property
    def effective_rate(self) -> float:
        """Total tax ÷ gross income (ordinary + gains + SS)."""
        gross = self.ordinary_income + self.long_term_gains + self.ss_income
        return self.total_tax / gross if gross > 0 else 0.0

    @property
    def marginal_federal_rate(self) -> float:
        """Top marginal federal bracket rate (ordinary income)."""
        return _top_bracket_rate(
            self.federal_taxable_income,
            FEDERAL_BRACKETS[self.filing_status],
        )

    @property
    def marginal_ca_rate(self) -> float:
        """Top marginal CA bracket rate."""
        return _top_bracket_rate(
            self.ca_taxable_income,
            CA_BRACKETS[self.filing_status],
        )


def _top_bracket_rate(income: float, brackets: list[tuple[float, float]]) -> float:
    """Return the marginal rate that applies to the top dollar of income."""
    if income <= 0:
        return 0.0
    prev = 0.0
    for upper, rate in brackets:
        if income <= upper:
            return rate
        prev = upper
    return brackets[-1][1]


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------
def calculate_taxes(
    ordinary_income: float,
    long_term_gains: float = 0.0,
    ss_income: float = 0.0,
    filing_status: FilingStatus = "married_jointly",
) -> TaxResult:
    """
    Estimate annual federal + California income taxes.

    Parameters
    ----------
    ordinary_income
        All income taxed at ordinary rates:
          - Employment wages
          - Net self-employment income
          - Tax-deferred account withdrawals (401k, trad IRA, RMDs)
          - Net rental income
          - Interest income
          - Short-term capital gains
        Do NOT include Roth withdrawals (they are always tax-free).
        Do NOT include SS here — pass it via ``ss_income`` so taxability
        rules are applied correctly.

    long_term_gains
        Net realized long-term capital gains + qualified dividends.
        Federal: taxed at preferential LTCG rates (0/15/20 %).
        California: taxed as ordinary income (no preferential rate).

    ss_income
        Total Social Security benefits received (both spouses combined).
        The engine calculates the taxable portion automatically.

    filing_status
        "married_jointly" | "married_separately" | "single"

    Returns
    -------
    TaxResult with full breakdown.
    """
    ordinary_income = max(0.0, ordinary_income)
    long_term_gains = max(0.0, long_term_gains)
    ss_income       = max(0.0, ss_income)

    # ------------------------------------------------------------------
    # 1. Social Security taxability (federal only)
    # ------------------------------------------------------------------
    taxable_ss = ss_taxable_federal(
        ss_income=ss_income,
        other_income=ordinary_income + long_term_gains,
        filing_status=filing_status,
    )

    # ------------------------------------------------------------------
    # 2. Federal AGI (simplified — no pre-tax adjustments in MVP)
    # ------------------------------------------------------------------
    federal_agi = ordinary_income + long_term_gains + taxable_ss

    fed_std_ded = FEDERAL_STANDARD_DEDUCTION[filing_status]
    federal_taxable = max(0.0, federal_agi - fed_std_ded)

    # ------------------------------------------------------------------
    # 3. Federal tax on ordinary income
    #
    # The "stacking" rule: ordinary income fills the lower brackets first,
    # then LTCG sit on top and are taxed at the preferential LTCG rate
    # that corresponds to that level of income.
    # ------------------------------------------------------------------
    ordinary_taxable = max(0.0, federal_taxable - long_term_gains)

    federal_ordinary_tax = _bracket_tax(ordinary_taxable, FEDERAL_BRACKETS[filing_status])

    # ------------------------------------------------------------------
    # 4. Federal tax on long-term capital gains  (stacking method)
    # ------------------------------------------------------------------
    federal_ltcg_tax = _ltcg_tax(
        ordinary_taxable_income=ordinary_taxable,
        ltcg_income=long_term_gains,
        filing_status=filing_status,
    )

    federal_tax = federal_ordinary_tax + federal_ltcg_tax

    # ------------------------------------------------------------------
    # 5. California income tax
    #
    # CA taxes LTCG as ordinary income — no preferential rate.
    # CA does NOT tax Social Security benefits.
    # ------------------------------------------------------------------
    ca_agi = ordinary_income + long_term_gains  # no taxable-SS adjustment for CA
    ca_std_ded = CA_STANDARD_DEDUCTION[filing_status]
    ca_taxable = max(0.0, ca_agi - ca_std_ded)
    ca_tax = _bracket_tax(ca_taxable, CA_BRACKETS[filing_status])

    # ------------------------------------------------------------------
    # 6. Assemble result
    # ------------------------------------------------------------------
    return TaxResult(
        ordinary_income=ordinary_income,
        long_term_gains=long_term_gains,
        ss_income=ss_income,
        filing_status=filing_status,
        federal_agi=federal_agi,
        federal_taxable_income=federal_taxable,
        ca_agi=ca_agi,
        ca_taxable_income=ca_taxable,
        federal_ordinary_tax=federal_ordinary_tax,
        federal_ltcg_tax=federal_ltcg_tax,
        federal_tax=federal_tax,
        ca_tax=ca_tax,
        total_tax=federal_tax + ca_tax,
    )


def _ltcg_tax(
    ordinary_taxable_income: float,
    ltcg_income: float,
    filing_status: FilingStatus,
) -> float:
    """
    Compute federal LTCG tax using the income-stacking method.

    LTCG income "sits on top" of ordinary income; the 0 %/15 %/20 %
    LTCG bracket boundaries are applied to the combined amount, and we
    take the marginal LTCG tax owed on just the gains portion.
    """
    if ltcg_income <= 0:
        return 0.0

    brackets = FEDERAL_LTCG_BRACKETS[filing_status]

    # Compute LTCG tax as if only gains existed, but starting from the
    # point where ordinary income leaves off.
    total_with_gains = ordinary_taxable_income + ltcg_income
    tax_on_total     = _bracket_tax(total_with_gains, brackets)
    tax_on_ordinary  = _bracket_tax(ordinary_taxable_income, brackets)
    return max(0.0, tax_on_total - tax_on_ordinary)


# ---------------------------------------------------------------------------
# Convenience: quick effective rate (used by projection engine)
# ---------------------------------------------------------------------------
def effective_tax_rate(
    ordinary_income: float,
    long_term_gains: float = 0.0,
    ss_income: float = 0.0,
    filing_status: FilingStatus = "married_jointly",
) -> float:
    """
    Return the combined (federal + CA) effective tax rate as a fraction (0–1).
    Shorthand for calculate_taxes(...).effective_rate.
    """
    return calculate_taxes(
        ordinary_income=ordinary_income,
        long_term_gains=long_term_gains,
        ss_income=ss_income,
        filing_status=filing_status,
    ).effective_rate


# ---------------------------------------------------------------------------
# Convenience: marginal rates for Roth conversion planning (post-MVP hook)
# ---------------------------------------------------------------------------
def marginal_rates(
    ordinary_income: float,
    long_term_gains: float = 0.0,
    filing_status: FilingStatus = "married_jointly",
) -> dict[str, float]:
    """
    Return marginal federal and CA ordinary income rates at the given income.
    Useful for Roth conversion analysis (post-MVP).
    """
    result = calculate_taxes(
        ordinary_income=ordinary_income,
        long_term_gains=long_term_gains,
        filing_status=filing_status,
    )
    return {
        "federal_marginal": result.marginal_federal_rate,
        "ca_marginal":      result.marginal_ca_rate,
        "combined_marginal": result.marginal_federal_rate + result.marginal_ca_rate,
    }


# ---------------------------------------------------------------------------
# Convenience: incremental tax for Roth conversion planning
# ---------------------------------------------------------------------------
def incremental_tax(
    base_ordinary_income: float,
    conversion_amount: float,
    long_term_gains: float = 0.0,
    ss_income: float = 0.0,
    filing_status: FilingStatus = "married_jointly",
) -> float:
    """
    Compute the additional tax owed from converting ``conversion_amount``
    from a Traditional IRA to a Roth IRA, given existing ordinary income.

    The conversion amount is added to ordinary income (IRS treats Roth
    conversions as ordinary income in the year of conversion).

    Returns
    -------
    Incremental tax in dollars (always >= 0).
    """
    tax_without = calculate_taxes(
        ordinary_income=base_ordinary_income,
        long_term_gains=long_term_gains,
        ss_income=ss_income,
        filing_status=filing_status,
    ).total_tax

    tax_with = calculate_taxes(
        ordinary_income=base_ordinary_income + conversion_amount,
        long_term_gains=long_term_gains,
        ss_income=ss_income,
        filing_status=filing_status,
    ).total_tax

    return max(0.0, tax_with - tax_without)
