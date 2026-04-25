"""
Data models for the retirement planner.

All inputs and configuration are represented as dataclasses here.
The engine layer operates on these models; the UI layer reads/writes them.
Models are JSON-serializable for profile persistence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------
@dataclass
class Person:
    """One individual (self or spouse)."""

    name: str = "Self"
    current_age: int = 50
    retirement_age: int = 65
    life_expectancy: int = 90

    # Social Security
    ss_monthly_benefit: float = 0.0  # estimated benefit at Full Retirement Age
    ss_claiming_age: int = 67        # 62–70


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------
@dataclass
class IncomeSource:
    """A stream of income (salary, side gig, consulting, etc.)."""

    name: str = "Salary"
    annual_amount: float = 0.0
    annual_raise_pct: float = 2.0    # annual raise as %
    start_age: int = 0               # 0 = "from now"
    end_age: int = 0                 # 0 = "until retirement"
    owner: Literal["self", "spouse"] = "self"


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
EXPENSE_CATEGORIES = [
    "housing",
    "healthcare",
    "other",
]


@dataclass
class Expense:
    """A recurring expense category."""

    name: str = ""
    monthly_amount: float = 0.0
    category: str = "other"
    retirement_pct: float = 100.0     # % of this expense that continues in retirement
    inflation_adjusted: bool = True


@dataclass
class OneTimeExpense:
    """A single large expense at a specific year."""

    name: str = ""
    amount: float = 0.0
    year: int = 2030
    inflation_adjusted: bool = True


# ---------------------------------------------------------------------------
# Investment Accounts
# ---------------------------------------------------------------------------
ACCOUNT_TYPES = [
    "401k",
    "trad_ira",
    "roth_ira",
    "roth_401k",
    "brokerage",
    "hsa",
    "savings",
]

# Tax treatment by account type
ACCOUNT_TAX_TREATMENT = {
    "401k": "tax_deferred",       # taxed on withdrawal as ordinary income
    "trad_ira": "tax_deferred",
    "roth_ira": "tax_free",       # qualified withdrawals are tax-free
    "roth_401k": "tax_free",
    "brokerage": "taxable",       # capital gains on withdrawal
    "hsa": "tax_free",            # MVP: treat as tax-free (upgrade later for medical specifics)
    "savings": "taxable",         # interest is taxable (negligible for planning)
}


@dataclass
class InvestmentAccount:
    """A single investment account."""

    name: str = ""
    account_type: str = "brokerage"
    balance: float = 0.0
    cost_basis: Optional[float] = None  # If None, engine assumes 50% of starting balance
    annual_contribution: float = 0.0
    employer_match: float = 0.0       # annual employer match (401k only)
    annual_return_pct: float = 7.0    # nominal expected annual return
    owner: Literal["self", "spouse"] = "self"

    @property
    def tax_treatment(self) -> str:
        """Return 'tax_deferred', 'tax_free', or 'taxable'."""
        return ACCOUNT_TAX_TREATMENT.get(self.account_type, "taxable")

    @property
    def monthly_return_rate(self) -> float:
        """Monthly compound rate from annual return percentage."""
        return (1 + self.annual_return_pct / 100) ** (1 / 12) - 1


# ---------------------------------------------------------------------------
# Real Estate
# ---------------------------------------------------------------------------
@dataclass
class Property:
    """A real estate property (primary home or rental)."""

    name: str = ""
    property_type: Literal["primary", "rental"] = "primary"
    current_value: float = 0.0
    appreciation_rate_pct: float = 3.0    # annual appreciation %
    mortgage_balance: float = 0.0
    monthly_payment: float = 0.0
    mortgage_rate_pct: float = 0.0        # annual interest rate %
    years_remaining: int = 0              # years left on mortgage

    # Rental-specific
    monthly_rental_income: float = 0.0
    monthly_expenses: float = 0.0         # maintenance, insurance, property tax, etc.

    @property
    def monthly_appreciation_rate(self) -> float:
        """Monthly compound rate from annual appreciation."""
        return (1 + self.appreciation_rate_pct / 100) ** (1 / 12) - 1

    @property
    def monthly_mortgage_rate(self) -> float:
        """Monthly interest rate from annual mortgage rate."""
        return self.mortgage_rate_pct / 100 / 12

    @property
    def net_equity(self) -> float:
        """Current equity = value − mortgage balance."""
        return self.current_value - self.mortgage_balance

    @property
    def net_monthly_rental_income(self) -> float:
        """Net rental income = rent − expenses − mortgage payment."""
        return self.monthly_rental_income - self.monthly_expenses - self.monthly_payment


# ---------------------------------------------------------------------------
# Plan Profile (top-level container)
# ---------------------------------------------------------------------------
@dataclass
class PlanProfile:
    """
    Top-level container for all user inputs.
    One PlanProfile = one retirement scenario.
    """

    # People
    self_person: Person = field(default_factory=Person)
    spouse: Person = field(default_factory=lambda: Person(name="Spouse"))

    # Globals
    plan_name: str = "My Retirement Plan"
    filing_status: Literal["married_jointly", "married_separately", "single"] = "married_jointly"
    inflation_rate_pct: float = 3.0

    # Income, expenses, accounts, properties
    incomes: list[IncomeSource] = field(default_factory=list)
    expenses: list[Expense] = field(default_factory=list)
    one_time_expenses: list[OneTimeExpense] = field(default_factory=list)
    accounts: list[InvestmentAccount] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def retirement_year_self(self) -> int:
        """Calendar year when self retires (approximate — assumes today's year)."""
        from datetime import date
        return date.today().year + (self.self_person.retirement_age - self.self_person.current_age)

    @property
    def retirement_year_spouse(self) -> int:
        """Calendar year when spouse retires."""
        from datetime import date
        return date.today().year + (self.spouse.retirement_age - self.spouse.current_age)

    @property
    def plan_end_year(self) -> int:
        """Last year of the plan = max life expectancy of both people."""
        from datetime import date
        end_self = date.today().year + (self.self_person.life_expectancy - self.self_person.current_age)
        end_spouse = date.today().year + (self.spouse.life_expectancy - self.spouse.current_age)
        return max(end_self, end_spouse)

    @property
    def total_account_balance(self) -> float:
        """Sum of all investment account balances."""
        return sum(a.balance for a in self.accounts)

    @property
    def total_annual_contributions(self) -> float:
        """Sum of all annual contributions + employer matches."""
        return sum(a.annual_contribution + a.employer_match for a in self.accounts)

    @property
    def total_real_estate_equity(self) -> float:
        """Sum of equity across all properties."""
        return sum(p.net_equity for p in self.properties)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Convert to a plain dict for JSON serialization."""
        return asdict(self)

    def to_json(self, path: str | Path) -> None:
        """Save profile to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> PlanProfile:
        """Reconstruct a PlanProfile from a plain dict."""
        return cls(
            self_person=Person(**data.get("self_person", {})),
            spouse=Person(**data.get("spouse", {})),
            plan_name=data.get("plan_name", "My Retirement Plan"),
            filing_status=data.get("filing_status", "married_jointly"),
            inflation_rate_pct=data.get("inflation_rate_pct", 3.0),
            incomes=[IncomeSource(**i) for i in data.get("incomes", [])],
            expenses=[Expense(**e) for e in data.get("expenses", [])],
            one_time_expenses=[OneTimeExpense(**o) for o in data.get("one_time_expenses", [])],
            accounts=[InvestmentAccount(**a) for a in data.get("accounts", [])],
            properties=[Property(**p) for p in data.get("properties", [])],
        )

    @classmethod
    def from_json(cls, path: str | Path) -> PlanProfile:
        """Load a profile from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def sample(cls) -> PlanProfile:
        """Create a sample profile with realistic placeholder data for testing."""
        return cls(
            self_person=Person(
                name="You",
                current_age=50,
                retirement_age=62,
                life_expectancy=90,
                ss_monthly_benefit=2800,
                ss_claiming_age=67,
            ),
            spouse=Person(
                name="Spouse",
                current_age=48,
                retirement_age=62,
                life_expectancy=92,
                ss_monthly_benefit=2200,
                ss_claiming_age=67,
            ),
            plan_name="Sample Plan",
            filing_status="married_jointly",
            inflation_rate_pct=3.0,
            incomes=[
                IncomeSource(name="Your Salary", annual_amount=180_000, annual_raise_pct=3.0, owner="self"),
                IncomeSource(name="Spouse Salary", annual_amount=120_000, annual_raise_pct=2.5, owner="spouse"),
            ],
            expenses=[
                Expense(name="Housing", monthly_amount=3_000, category="housing", retirement_pct=80),
                Expense(name="Healthcare", monthly_amount=500, category="healthcare", retirement_pct=150),
                Expense(name="Other Expenses", monthly_amount=3_583, category="other", retirement_pct=100),
            ],
            one_time_expenses=[
                OneTimeExpense(name="Kitchen Remodel", amount=50_000, year=2028, inflation_adjusted=True),
            ],
            accounts=[
                InvestmentAccount(name="401(k)", account_type="401k", balance=500_000,
                                  annual_contribution=23_000, employer_match=11_500, annual_return_pct=7.0, owner="self"),
                InvestmentAccount(name="Spouse 401(k)", account_type="401k", balance=350_000,
                                  annual_contribution=23_000, employer_match=8_000, annual_return_pct=7.0, owner="spouse"),
                InvestmentAccount(name="Traditional IRA", account_type="trad_ira", balance=150_000,
                                  annual_contribution=7_000, annual_return_pct=7.0, owner="self"),
                InvestmentAccount(name="Roth IRA", account_type="roth_ira", balance=100_000,
                                  annual_contribution=7_000, annual_return_pct=7.0, owner="self"),
                InvestmentAccount(name="Spouse Roth IRA", account_type="roth_ira", balance=80_000,
                                  annual_contribution=7_000, annual_return_pct=7.0, owner="spouse"),
                InvestmentAccount(name="Brokerage", account_type="brokerage", balance=200_000, cost_basis=150_000,
                                  annual_contribution=24_000, annual_return_pct=7.0, owner="self"),
                InvestmentAccount(name="HSA", account_type="hsa", balance=30_000,
                                  annual_contribution=8_300, annual_return_pct=7.0, owner="self"),
            ],
            properties=[
                Property(name="Primary Home", property_type="primary", current_value=950_000,
                         appreciation_rate_pct=3.5, mortgage_balance=400_000, monthly_payment=2_800,
                         mortgage_rate_pct=6.5, years_remaining=22),
                Property(name="Rental Condo", property_type="rental", current_value=550_000,
                         appreciation_rate_pct=3.0, mortgage_balance=300_000, monthly_payment=2_100,
                         mortgage_rate_pct=5.5, years_remaining=25,
                         monthly_rental_income=3_200, monthly_expenses=800),
            ],
        )
