"""
Shared helpers for Roth conversion income and tax estimation.

These functions are used by both ``engine.roth_conversion`` (for building the
``conversion_details`` summary) and ``engine.projections`` (for the in-loop
conversion hook inside ``ProjectionEngine``).

They are extracted here to avoid a circular import between those two modules.
"""

from __future__ import annotations

from engine.models import PlanProfile
from engine.taxes import calculate_taxes, marginal_rates


# ---------------------------------------------------------------------------
# Income estimators (used to determine the marginal tax bracket for a
# conversion event in a given year)
# ---------------------------------------------------------------------------

def estimate_annual_ordinary_income(
    profile: PlanProfile,
    year: int,
    start_year: int,
) -> float:
    """
    Estimate total ordinary income for *year* (salary only, pre-retirement).

    Parameters
    ----------
    profile    : the retirement plan
    year       : calendar year to estimate for
    start_year : base year (today) — used to compute cumulative raises
    """
    years_of_raises = max(0, year - start_year)
    total = 0.0
    for src in profile.incomes:
        owner = profile.self_person if src.owner == "self" else profile.spouse
        if year < owner.retirement_year:
            annual = src.annual_amount * (1 + src.annual_raise_pct / 100) ** years_of_raises
            total += annual
    return total


def estimate_annual_rental_income(
    profile: PlanProfile,
    year: int,
    start_year: int,
) -> float:
    """
    Estimate net rental income for *year* (income minus expenses, inflation-adjusted).
    """
    years_elapsed = max(0, year - start_year)

    total_rental_net = 0.0
    for prop in profile.properties:
        monthly_income = prop.monthly_rental_income * (
            (1 + prop.rental_inflation_rate_pct / 100) ** years_elapsed
        )
        monthly_expenses = prop.monthly_expenses * (
            (1 + prop.rental_inflation_rate_pct / 100) ** years_elapsed
        )
        annual_net = (monthly_income - monthly_expenses) * 12
        total_rental_net += annual_net

    return max(0.0, total_rental_net)


def estimate_annual_ss_income(
    profile: PlanProfile,
    year: int,
    start_year: int,
) -> float:
    """
    Estimate annual Social Security income for *year*.
    """
    from engine.social_security import compute_ss_benefit

    cola = profile.inflation_rate_pct / 100
    total_ss = 0.0

    for person in (profile.self_person, profile.spouse):
        ss_benefit = compute_ss_benefit(person, start_year)
        if year >= ss_benefit.claim_year:
            monthly_amount = ss_benefit.monthly_in_year(year, cola)
            total_ss += monthly_amount * 12

    return total_ss


# ---------------------------------------------------------------------------
# Incremental tax (the extra tax owed from adding a conversion amount)
# ---------------------------------------------------------------------------

def compute_incremental_tax(
    base_ordinary: float,
    conversion_amount: float,
    long_term_gains: float,
    ss_income: float,
    filing_status: str,
) -> float:
    """
    Compute the additional tax owed from converting *conversion_amount*.

    Returns
    -------
    Incremental tax in dollars (always >= 0).
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


def compute_marginal_rates_for_conversion(
    base_ordinary: float,
    conversion_amount: float,
    filing_status: str,
) -> dict:
    """
    Return federal/CA/combined marginal rates at the post-conversion income level.
    """
    return marginal_rates(
        ordinary_income=base_ordinary + conversion_amount,
        filing_status=filing_status,
    )
