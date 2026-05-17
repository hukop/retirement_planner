"""
Social Security benefit calculations.

Covers
------
- Full Retirement Age (FRA) by birth year (1943–1960+)
- Early claiming reductions (age 62–FRA):
    First 36 months early: 5/9 of 1 % per month (~6.667 %/yr)
    Beyond 36 months      : 5/12 of 1 % per month (~5.000 %/yr)
- Delayed retirement credits (FRA → age 70): 8 %/yr (2/3 of 1 % per month)
- Spousal benefit (up to 50 % of primary's PIA if larger than own)
- Survivor benefit hook (simplified — not wired to projection engine in MVP)
- Inflation adjustment: COLA applied year-over-year in projection

Terminology
-----------
PIA  — Primary Insurance Amount: the monthly benefit payable at FRA.
       In this app the user enters their estimated PIA directly (SSA online
       statement value is the PIA at FRA).
COLA — Cost-of-Living Adjustment: applied annually after benefits begin.
       MVP uses the plan's general inflation rate as a COLA proxy.

Reference
---------
- SSA Publication 05-10147 "When to Start Receiving Retirement Benefits"
- SSA POMS RS 00615 (FRA table, reduction factors)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from engine.models import Person


# ---------------------------------------------------------------------------
# Full Retirement Age table
# ---------------------------------------------------------------------------
# Maps birth year → FRA expressed as (years, months).
# Anyone born 1960 or later has FRA = 67-0.
_FRA_TABLE: list[tuple[int, int, int]] = [
    # (birth_year_upper_inclusive, fra_years, fra_months)
    (1937, 65, 0),
    (1938, 65, 2),
    (1939, 65, 4),
    (1940, 65, 6),
    (1941, 65, 8),
    (1942, 65, 10),
    (1954, 66, 0),   # 1943–1954
    (1955, 66, 2),
    (1956, 66, 4),
    (1957, 66, 6),
    (1958, 66, 8),
    (1959, 66, 10),
]
_FRA_DEFAULT = (67, 0)  # born 1960+


def full_retirement_age(birth_year: int) -> tuple[int, int]:
    """
    Return the Full Retirement Age as ``(years, months)`` for the given birth year.
    """
    try:
        birth_year = int(birth_year)
    except (TypeError, ValueError):
        return _FRA_DEFAULT
    for upper, fra_y, fra_m in _FRA_TABLE:
        if birth_year <= upper:
            return fra_y, fra_m
    return _FRA_DEFAULT


def fra_in_years(birth_year: int) -> float:
    """Return FRA as a decimal age (e.g., 66.167 for 66 years 2 months)."""
    y, m = full_retirement_age(birth_year)
    return y + m / 12


# ---------------------------------------------------------------------------
# Benefit adjustment factor
# ---------------------------------------------------------------------------
def claiming_factor(claiming_age: float, birth_year: int) -> float:
    """
    Return the multiplier applied to PIA based on claiming age vs. FRA.

    Parameters
    ----------
    claiming_age : decimal age at which benefits begin (e.g., 62.0, 67.5)
    birth_year   : person's year of birth (used to look up FRA)

    Returns
    -------
    Multiplier in range [0.70, 1.32]:
      < FRA  →  reduced (permanent, penalty for early claiming)
      = FRA  →  1.00
      > FRA  →  increased (delayed retirement credits, max at 70)

    Notes
    -----
    - Minimum claiming age is 62; maximum credit age is 70.
    - Fractions of a month are supported (we work in decimal years).
    """
    fra = fra_in_years(birth_year)
    claiming_age = max(62.0, min(claiming_age, 70.0))

    if claiming_age >= fra:
        # Delayed credits: 8 % per year (2/3 of 1 % per month), capped at 70
        years_delayed = min(claiming_age, 70.0) - fra
        return 1.0 + 0.08 * years_delayed
    else:
        # Early claiming: two-tier reduction
        months_early = (fra - claiming_age) * 12  # total months before FRA

        # Tier 1: first 36 months at 5/9 of 1 % per month
        tier1_months = min(months_early, 36)
        tier1_reduction = tier1_months * (5 / 9 / 100)

        # Tier 2: any months beyond 36 at 5/12 of 1 % per month
        tier2_months = max(0.0, months_early - 36)
        tier2_reduction = tier2_months * (5 / 12 / 100)

        return 1.0 - tier1_reduction - tier2_reduction


# ---------------------------------------------------------------------------
# Individual benefit calculation
# ---------------------------------------------------------------------------
def adjusted_monthly_benefit(
    pia_monthly: float,
    claiming_age: float,
    birth_year: int,
) -> float:
    """
    Return the inflation-base monthly benefit after applying early/delayed adjustments.

    Parameters
    ----------
    pia_monthly  : estimated monthly benefit at FRA (as shown on SSA statement)
    claiming_age : age at which the person will claim (62–70)
    birth_year   : person's birth year

    Returns
    -------
    Adjusted monthly benefit in today's dollars (before future COLA).
    """
    try:
        val = float(pia_monthly or 0)
        if val <= 0:
            return 0.0
        factor = claiming_factor(float(claiming_age), int(birth_year))
        return round(val * factor, 2)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Spousal benefit
# ---------------------------------------------------------------------------
def spousal_benefit(
    own_pia: float,
    spouse_pia: float,
    own_claiming_age: float,
    own_birth_year: int,
    spouse_birth_year: int,
) -> float:
    """
    Return the monthly spousal benefit if it exceeds the own benefit.

    The spousal benefit equals up to 50 % of the primary spouse's PIA
    (at *their* FRA, regardless of when the primary actually claims).
    The secondary spouse's own benefit is reduced for early claiming in the
    same way as a regular benefit.

    Parameters
    ----------
    own_pia          : secondary spouse's own PIA at FRA
    spouse_pia       : primary spouse's PIA at FRA
    own_claiming_age : age at which the secondary spouse claims
    own_birth_year   : secondary spouse's birth year
    spouse_birth_year: primary spouse's birth year (used for FRA context)

    Returns
    -------
    Monthly benefit (the higher of own adjusted benefit or 50 % of spouse PIA,
    reduced if claimed early).

    Note: The spousal benefit maximum is 50 % of the primary's PIA and is
    reduced if the secondary spouse claims before their own FRA.  We apply
    the same early-claiming factor to the spousal portion.  This is a
    simplification — the exact SSA formula has a separate reduction schedule
    for spousal benefits, but the result is very close.
    """
    own_adjusted  = adjusted_monthly_benefit(own_pia, own_claiming_age, own_birth_year)
    spousal_max   = spouse_pia * 0.50
    factor        = claiming_factor(own_claiming_age, own_birth_year)
    spousal_adj   = spousal_max * factor
    return max(own_adjusted, spousal_adj)


# ---------------------------------------------------------------------------
# Projection helper: COLA-adjusted benefit for a given projection year
# ---------------------------------------------------------------------------
def benefit_in_year(
    base_monthly_benefit: float,
    claim_year: int,
    projection_year: int,
    cola_rate: float,
) -> float:
    """
    Return the monthly benefit for ``projection_year`` after annual COLA.

    Parameters
    ----------
    base_monthly_benefit : benefit in the year the person first claimed
                           (already adjusted for early/delayed claiming)
    claim_year           : calendar year when SS benefits started
    projection_year      : calendar year being projected
    cola_rate            : annual COLA as a fraction (e.g., 0.03 for 3 %)

    Returns
    -------
    Inflation-adjusted monthly benefit for that projection year.
    Returns 0 if projection_year < claim_year (benefits not yet started).
    """
    if projection_year < claim_year or base_monthly_benefit <= 0:
        return 0.0
    years_of_cola = projection_year - claim_year
    return base_monthly_benefit * (1 + cola_rate) ** years_of_cola


# ---------------------------------------------------------------------------
# Convenience dataclass for a single person's SS profile
# ---------------------------------------------------------------------------
@dataclass
class SSBenefit:
    """
    Computed Social Security profile for one person.

    Attributes
    ----------
    pia_monthly       : PIA at FRA (user-entered)
    fra               : Full Retirement Age as decimal years
    claiming_age      : age at which benefits begin
    factor            : benefit adjustment multiplier vs PIA
    adjusted_monthly  : monthly benefit in today's dollars at claim time
    claim_year        : calendar year benefits start
    """
    pia_monthly:      float
    fra:              float
    claiming_age:     float
    factor:           float
    adjusted_monthly: float
    claim_year:       int

    def monthly_in_year(self, projection_year: int, cola_rate: float) -> float:
        """COLA-adjusted monthly benefit for the given projection year."""
        return benefit_in_year(
            self.adjusted_monthly, self.claim_year, projection_year, cola_rate
        )

    def annual_in_year(self, projection_year: int, cola_rate: float) -> float:
        """COLA-adjusted annual benefit for the given projection year."""
        return self.monthly_in_year(projection_year, cola_rate) * 12


# ---------------------------------------------------------------------------
# Factory: build SSBenefit from a Person dataclass
# ---------------------------------------------------------------------------
def compute_ss_benefit(person: Person, current_year: int | None = None) -> SSBenefit:
    """
    Compute the full SS benefit profile for a person.

    Parameters
    ----------
    person       : Person dataclass (name, current_age, ss_monthly_benefit,
                   ss_claiming_age)
    current_year : calendar year used as "now" (defaults to today's year)

    Returns
    -------
    SSBenefit dataclass with all derived fields populated.

    Notes
    -----
    Birth year is estimated as ``current_year - current_age``.  For planning
    purposes this is accurate to within one year (we don't know the month of
    birth).
    """
    if current_year is None:
        current_year = date.today().year

    pia = float(person.ss_monthly_benefit or 0)
    claim_age = float(person.ss_claiming_age or 67)
    curr_age = float(person.current_age or 50)
    
    birth_year = person.birth_year
    fra        = fra_in_years(birth_year)
    factor     = claiming_factor(claim_age, birth_year)
    adjusted   = round(pia * factor, 2)

    # Claim year = calendar year the person reaches their claiming age
    claim_year = person.birth_year + int(claim_age)
    # If the birth month is late in the year, they might reach the age late in that year.
    # We keep it simple (year granularity) but this is now accurate to the year.

    return SSBenefit(
        pia_monthly=pia,
        fra=fra,
        claiming_age=claim_age,
        factor=factor,
        adjusted_monthly=adjusted,
        claim_year=claim_year,
    )


# ---------------------------------------------------------------------------
# Chart data: benefit vs. claiming age curve (for UI visualization)
# ---------------------------------------------------------------------------
def benefit_curve(
    pia_monthly: float,
    birth_year: int,
    ages: list[float] | None = None,
) -> list[dict]:
    """
    Return a list of ``{age, monthly_benefit, factor, vs_fra_pct}`` dicts
    for every integer claiming age 62–70 (or a custom ``ages`` list).

    Used by the Income page to render the "SS benefit at different claiming
    ages" interactive chart.
    """
    if ages is None:
        ages = [float(a) for a in range(62, 71)]

    fra = fra_in_years(birth_year)
    fra_benefit = pia_monthly  # benefit at exactly FRA = PIA

    rows = []
    for age in ages:
        f = claiming_factor(age, birth_year)
        monthly = round(pia_monthly * f, 2)
        rows.append({
            "age":           age,
            "monthly":       monthly,
            "annual":        monthly * 12,
            "factor":        round(f, 4),
            "vs_fra_pct":    round((f - 1.0) * 100, 2),   # e.g., -30.0 or +24.0
            "is_fra":        abs(age - fra) < 0.01,
        })
    return rows


# ---------------------------------------------------------------------------
# Break-even analysis
# ---------------------------------------------------------------------------
def break_even_age(
    pia_monthly: float,
    birth_year: int,
    early_age: float = 62.0,
    late_age: float = 70.0,
    cola_rate: float = 0.03,
) -> float | None:
    """
    Estimate the age at which cumulative lifetime benefits from claiming early
    equal those from claiming late.

    Parameters
    ----------
    pia_monthly : PIA at FRA
    birth_year  : person's birth year
    early_age   : the "claim early" age (default 62)
    late_age    : the "claim late" age  (default 70)
    cola_rate   : annual COLA (fraction)

    Returns
    -------
    Break-even age as a float, or None if break-even never occurs before 100.

    Notes
    -----
    This is a simplified cumulative-sum analysis:
    - Counts months of payments from each strategy
    - Both streams get the same COLA applied
    - Ignores time-value of money (NPV break-even is a post-MVP feature)
    """
    early_monthly = adjusted_monthly_benefit(pia_monthly, early_age, birth_year)
    late_monthly  = adjusted_monthly_benefit(pia_monthly, late_age,  birth_year)

    cumulative_early = 0.0
    cumulative_late  = 0.0

    # Simulate month by month from age 62 to 100
    for month in range(int((100 - 62) * 12)):
        age = 62.0 + month / 12.0

        if age >= early_age:
            cola_years = (age - early_age)
            cumulative_early += early_monthly * (1 + cola_rate) ** cola_years

        if age >= late_age:
            cola_years = (age - late_age)
            cumulative_late += late_monthly * (1 + cola_rate) ** cola_years

        if cumulative_late > cumulative_early and age >= late_age:
            return round(age, 1)

    return None  # break-even beyond age 100
