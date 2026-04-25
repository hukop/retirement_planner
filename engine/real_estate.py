"""
Real estate simulation engine.

Responsibilities
----------------
- Maintain a mutable ``PropertyState`` for each ``Property`` during a projection.
- Apply monthly appreciation to property value.
- Execute monthly mortgage amortization (interest → principal split).
- Auto-detect mortgage payoff month; housing expense drops to zero after payoff.
- Compute net rental income (rent − expenses − mortgage payment).
- Expose equity for net worth calculation.

Mortgage Amortization Math
---------------------------
For a fixed-rate mortgage the payment is constant; each month:

    interest_due  = remaining_balance × monthly_rate
    principal_due = monthly_payment − interest_due
    new_balance   = remaining_balance − principal_due

When ``remaining_balance ≤ 0`` the mortgage is paid off and the monthly
payment drops to zero automatically.

If ``mortgage_rate_pct == 0`` (e.g. paid-off home entered with balance 0)
the engine treats it as already paid off from month 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.models import Property


# ---------------------------------------------------------------------------
# PropertyState
# ---------------------------------------------------------------------------
@dataclass
class PropertyState:
    """
    Mutable simulation state for a single property.

    Instantiate via ``PropertyState.from_property(prop)``.
    """

    # Reference to the immutable config
    prop: Property

    # Mutable simulation values
    value:             float   # current estimated market value
    mortgage_balance:  float   # remaining loan balance
    is_paid_off:       bool    # True once mortgage balance reaches 0
    payoff_year:       int | None  # calendar year the mortgage was paid off

    # ------------------------------------------------------------------ #
    # Factory                                                             #
    # ------------------------------------------------------------------ #
    @classmethod
    def from_property(cls, prop: Property) -> "PropertyState":
        paid_off = prop.mortgage_balance <= 0 or prop.years_remaining <= 0
        return cls(
            prop=prop,
            value=prop.current_value,
            mortgage_balance=prop.mortgage_balance,
            is_paid_off=paid_off,
            payoff_year=None,
        )

    # ------------------------------------------------------------------ #
    # Convenience properties                                              #
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self.prop.name

    @property
    def property_type(self) -> str:
        return self.prop.property_type

    @property
    def net_equity(self) -> float:
        """Current equity = market value − mortgage balance."""
        return max(0.0, self.value - self.mortgage_balance)

    @property
    def monthly_payment(self) -> float:
        """Monthly payment — $0 once the mortgage is fully paid off."""
        return 0.0 if self.is_paid_off else self.prop.monthly_payment

    @property
    def net_monthly_rental_income(self) -> float:
        """
        Net rental income for one month (rental properties only).
        = gross rent − operating expenses − mortgage payment

        Returns 0 for primary residences.
        """
        if self.prop.property_type != "rental":
            return 0.0
        return self.prop.monthly_rental_income - self.prop.monthly_expenses - self.monthly_payment

    # ------------------------------------------------------------------ #
    # Monthly operations                                                  #
    # ------------------------------------------------------------------ #
    def appreciate(self) -> float:
        """
        Apply one month of appreciation to the property value.

        Returns
        -------
        Dollar amount of appreciation gained this month.
        """
        rate = self.prop.monthly_appreciation_rate
        gain = self.value * rate
        self.value += gain
        return gain

    def amortize(self, current_year: int | None = None) -> dict:
        """
        Execute one month of mortgage amortization.

        Parameters
        ----------
        current_year : calendar year (used to record payoff_year)

        Returns
        -------
        Dict with keys: interest_paid, principal_paid, payment_made, is_paid_off
        """
        if self.is_paid_off or self.mortgage_balance <= 0:
            return {
                "interest_paid":  0.0,
                "principal_paid": 0.0,
                "payment_made":   0.0,
                "is_paid_off":    True,
            }

        monthly_rate = self.prop.monthly_mortgage_rate

        # Interest on remaining balance
        interest = self.mortgage_balance * monthly_rate
        payment  = self.prop.monthly_payment

        # Guard: if payment ≤ interest (e.g. interest-only or bad input),
        # treat the full payment as interest so balance doesn't grow.
        principal = max(0.0, payment - interest)

        # Don't overpay on the final month
        principal = min(principal, self.mortgage_balance)
        interest  = min(interest,  self.mortgage_balance - principal)
        actual_payment = interest + principal

        self.mortgage_balance -= principal

        # Detect payoff
        if self.mortgage_balance <= 0.01:   # penny threshold to avoid float drift
            self.mortgage_balance = 0.0
            self.is_paid_off      = True
            self.payoff_year      = current_year

        return {
            "interest_paid":  round(interest, 2),
            "principal_paid": round(principal, 2),
            "payment_made":   round(actual_payment, 2),
            "is_paid_off":    self.is_paid_off,
        }

    def step_month(self, current_year: int | None = None) -> dict:
        """
        Advance simulation by one month: appreciate + amortize.

        Returns a combined summary dict for the month.
        """
        appreciation = self.appreciate()
        amort        = self.amortize(current_year=current_year)
        return {
            "appreciation":     round(appreciation, 2),
            "net_equity":       round(self.net_equity, 2),
            "mortgage_balance": round(self.mortgage_balance, 2),
            **amort,
        }

    # ------------------------------------------------------------------ #
    # Snapshot                                                            #
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        """Return a dict of current state for DataFrame row assembly."""
        return {
            "name":                    self.name,
            "property_type":           self.property_type,
            "value":                   round(self.value, 2),
            "mortgage_balance":        round(self.mortgage_balance, 2),
            "net_equity":              round(self.net_equity, 2),
            "is_paid_off":             self.is_paid_off,
            "payoff_year":             self.payoff_year,
            "monthly_payment":         round(self.monthly_payment, 2),
            "net_monthly_rental":      round(self.net_monthly_rental_income, 2),
        }


# ---------------------------------------------------------------------------
# Portfolio-level helpers
# ---------------------------------------------------------------------------

def build_property_portfolio(properties: list[Property]) -> list[PropertyState]:
    """Initialise a PropertyState list from the user's Property list."""
    return [PropertyState.from_property(p) for p in properties]


def total_equity(portfolio: list[PropertyState]) -> float:
    """Sum of net equity across all properties."""
    return sum(s.net_equity for s in portfolio)


def total_monthly_mortgage_payments(portfolio: list[PropertyState]) -> float:
    """Total monthly mortgage outflow across all properties."""
    return sum(s.monthly_payment for s in portfolio)


def total_net_monthly_rental_income(portfolio: list[PropertyState]) -> float:
    """Total net monthly rental income across all rental properties."""
    return sum(s.net_monthly_rental_income for s in portfolio)


def step_all_month(
    portfolio: list[PropertyState],
    current_year: int | None = None,
) -> list[dict]:
    """
    Advance all properties by one month.
    Returns a list of per-property monthly summary dicts.
    """
    return [s.step_month(current_year=current_year) for s in portfolio]


# ---------------------------------------------------------------------------
# Amortisation schedule helper (for UI display)
# ---------------------------------------------------------------------------
def amortization_schedule(prop: Property, max_months: int | None = None) -> list[dict]:
    """
    Generate a full amortization schedule for ``prop`` without mutating
    a live PropertyState.

    Parameters
    ----------
    prop       : Property dataclass
    max_months : cap the schedule length (None = until payoff or 480 months)

    Returns
    -------
    List of dicts (one per month) with: month, year_offset, balance,
    interest_paid, principal_paid, cumulative_interest.
    """
    if prop.mortgage_balance <= 0 or prop.years_remaining <= 0:
        return []

    cap          = max_months or min(prop.years_remaining * 12 + 12, 480)
    balance      = prop.mortgage_balance
    monthly_rate = prop.monthly_mortgage_rate
    payment      = prop.monthly_payment
    cumulative_interest = 0.0
    rows: list[dict] = []

    for month in range(1, cap + 1):
        if balance <= 0:
            break
        interest  = balance * monthly_rate
        principal = max(0.0, min(payment - interest, balance))
        interest  = min(interest, balance - principal)
        balance  -= principal
        cumulative_interest += interest

        rows.append({
            "month":               month,
            "year_offset":         (month - 1) // 12 + 1,
            "balance":             round(max(0.0, balance), 2),
            "interest_paid":       round(interest, 2),
            "principal_paid":      round(principal, 2),
            "cumulative_interest": round(cumulative_interest, 2),
        })

    return rows
