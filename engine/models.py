"""
Data models for Finance Planner.

All inputs and configuration are represented as dataclasses here.
The engine layer operates on these models; the UI layer reads/writes them.
Models are JSON-serializable for profile persistence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Literal, Optional


# ---------------------------------------------------------------------------
from datetime import date


# Roth Conversion configuration
# ---------------------------------------------------------------------------
@dataclass
class RothConversionConfig:
    """
    Parameters for a Roth conversion analysis.

    Attributes
    ----------
    annual_amount : float
        Dollar amount to convert each year during the window.
    start_year : int
        First calendar year in which conversions occur.
    end_year : int
        Last calendar year in which conversions occur (inclusive).
    source_account_types : list[str]
        Account types eligible as conversion sources.
        Default: ["trad_ira"] (401k not supported in this version).
    """

    annual_amount: float = 50_000
    start_year: int = 0          # 0 = current year
    end_year: int = 0            # 0 = retirement year
    source_account_types: list[str] = field(
        default_factory=lambda: ["trad_ira"]
    )

    def __post_init__(self):
        current_year = date.today().year
        if self.start_year <= 0:
            self.start_year = current_year
        if self.end_year <= 0:
            self.end_year = current_year + 10  # default 10-year window

# Monte Carlo configuration
# ---------------------------------------------------------------------------
@dataclass
class MonteCarloConfig:
    """
    Configuration for Monte Carlo simulation runs.

    All return parameters are expressed as annualized percentages.
    The engine converts them to monthly figures internally.
    """

    # Number of independent simulation trials to run
    num_trials: int = 1000

    # Optional fixed seed for reproducibility (None = random each run)
    random_seed: Optional[int] = None

    # UI/run options
    adaptive_spending: bool = False
    live_updates: bool = True


# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------
@dataclass
class Person:
    """One individual (self or spouse)."""

    name: str = "Self"
    birth_year: int = 1975
    birth_month: int = 1
    retirement_year: int = 2040
    life_expectancy: int = 90

    # Social Security
    ss_monthly_benefit: float = 0.0  # estimated benefit at Full Retirement Age
    ss_claiming_age: int = 67        # 62–70

    def __init__(
        self,
        name: str = "Self",
        birth_year: int = 1975,
        birth_month: int = 1,
        retirement_year: int = 2040,
        life_expectancy: int = 90,
        ss_monthly_benefit: float = 0.0,
        ss_claiming_age: int = 67,
    ):
        self.name = name
        self.birth_year = birth_year
        self.birth_month = birth_month
        self.retirement_year = retirement_year
        self.life_expectancy = life_expectancy
        self.ss_monthly_benefit = ss_monthly_benefit
        self.ss_claiming_age = ss_claiming_age

    @property
    def retirement_age(self) -> int:
        """Calculate age at retirement."""
        return self.retirement_year - self.birth_year

    @property
    def current_age(self) -> int:
        """Calculate current age based on birth year and month."""
        from datetime import date
        today = date.today()
        age = today.year - self.birth_year
        if today.month < self.birth_month:
            age -= 1
        return age


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
    retirement_factor: float = 1.0    # factor: 0.5 = half, 1.0 = same, 2.0 = double
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
    volatility_pct: float = 15.0      # expected annual volatility (std dev)
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
    rental_inflation_rate_pct: float = 3.0

    @property
    def monthly_appreciation_rate(self) -> float:
        """Monthly compound rate from annual appreciation."""
        return (1 + self.appreciation_rate_pct / 100) ** (1 / 12) - 1

    @property
    def monthly_mortgage_rate(self) -> float:
        """Monthly interest rate from annual mortgage rate."""
        return self.mortgage_rate_pct / 100 / 12

    @property
    def monthly_rental_inflation_rate(self) -> float:
        """Monthly compound rate from annual rental inflation."""
        return (1 + self.rental_inflation_rate_pct / 100) ** (1 / 12) - 1

    @property
    def net_equity(self) -> float:
        """Current equity = value − mortgage balance."""
        return float(self.current_value or 0) - float(self.mortgage_balance or 0)

    @property
    def net_monthly_rental_income(self) -> float:
        """Net rental income = rent − expenses − mortgage payment.

        Returns 0 for primary residences — only rental properties generate
        income.  Use ``PropertyState.net_monthly_rental_income`` during a
        simulation run (it also accounts for the live mortgage payment).
        """
        if self.property_type != "rental":
            return 0.0
        return (float(self.monthly_rental_income or 0) -
                float(self.monthly_expenses or 0) -
                float(self.monthly_payment or 0))


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


    # Monte Carlo configuration
    monte_carlo: MonteCarloConfig = field(default_factory=MonteCarloConfig)

    # Roth Conversion configuration
    roth_conversion: RothConversionConfig = field(default_factory=RothConversionConfig)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def retirement_year_self(self) -> int:
        """Calendar year when self retires."""
        return self.self_person.retirement_year

    @property
    def retirement_year_spouse(self) -> int:
        """Calendar year when spouse retires."""
        return self.spouse.retirement_year

    @property
    def plan_end_year(self) -> int:
        """Last year of the plan = max life expectancy of both people."""
        end_self = self.self_person.birth_year + self.self_person.life_expectancy
        end_spouse = self.spouse.birth_year + self.spouse.life_expectancy
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
    def from_dict(cls, data: dict | None) -> PlanProfile:
        """Reconstruct a PlanProfile from a plain dict, discarding unknown keys."""
        if not isinstance(data, dict):
            # Fallback to sample if data is corrupted or None
            return cls.sample()

        def safe_load(dataclass_type, item_dict):
            if not isinstance(item_dict, dict):
                return dataclass_type()
            # Migration: convert old age/retirement fields to new birth_year/retirement_year
            if dataclass_type is Person:
                _date = date.today()
                if "current_age" in item_dict and "birth_year" not in item_dict:
                    item_dict["birth_year"] = _date.year - item_dict["current_age"]
                if "retirement_age" in item_dict and "retirement_year" not in item_dict:
                    if "birth_year" in item_dict:
                        item_dict["retirement_year"] = item_dict["birth_year"] + item_dict["retirement_age"]
                    elif "current_age" in item_dict:
                        item_dict["retirement_year"] = _date.year - item_dict["current_age"] + item_dict["retirement_age"]
            valid_keys = {f.name for f in fields(dataclass_type)}
            filtered = {k: v for k, v in item_dict.items() if k in valid_keys and v is not None}
            return dataclass_type(**filtered)

        return cls(
            self_person=safe_load(Person, data.get("self_person", {})),
            spouse=safe_load(Person, data.get("spouse", {})),
            plan_name=data.get("plan_name", "My Retirement Plan"),
            filing_status=data.get("filing_status", "married_jointly"),
            inflation_rate_pct=data.get("inflation_rate_pct", 3.0),
            incomes=[safe_load(IncomeSource, i) for i in data.get("incomes", []) if isinstance(i, dict)],
            expenses=[safe_load(Expense, e) for e in data.get("expenses", []) if isinstance(e, dict)],
            one_time_expenses=[safe_load(OneTimeExpense, o) for o in data.get("one_time_expenses", []) if isinstance(o, dict)],
            accounts=[safe_load(InvestmentAccount, a) for a in data.get("accounts", []) if isinstance(a, dict)],
            properties=[safe_load(Property, p) for p in data.get("properties", []) if isinstance(p, dict)],
            monte_carlo=safe_load(MonteCarloConfig, data.get("monte_carlo", {})),
            roth_conversion=safe_load(RothConversionConfig, data.get("roth_conversion", {})),
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
                birth_year=1974,
                birth_month=5,
                retirement_year=2036,
                life_expectancy=90,
                ss_monthly_benefit=2800,
                ss_claiming_age=67,
            ),
            spouse=Person(
                name="Spouse",
                birth_year=1976,
                birth_month=8,
                retirement_year=2038,
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
                Expense(name="Housing", monthly_amount=3_000, category="housing", retirement_factor=0.8),
                Expense(name="Healthcare", monthly_amount=500, category="healthcare", retirement_factor=1.5),
                Expense(name="Other Expenses", monthly_amount=3_583, category="other", retirement_factor=1.0),
            ],
            one_time_expenses=[
                OneTimeExpense(name="Kitchen Remodel", amount=50_000, year=2028, inflation_adjusted=True),
            ],
            accounts=[
                InvestmentAccount(name="401(k)", account_type="401k", balance=500_000,
                                  annual_contribution=23_000, employer_match=11_500, annual_return_pct=7.0, volatility_pct=15.0, owner="self"),
                InvestmentAccount(name="Spouse 401(k)", account_type="401k", balance=350_000,
                                  annual_contribution=23_000, employer_match=8_000, annual_return_pct=7.0, volatility_pct=15.0, owner="spouse"),
                InvestmentAccount(name="Traditional IRA", account_type="trad_ira", balance=150_000,
                                  annual_contribution=7_000, annual_return_pct=7.0, volatility_pct=15.0, owner="self"),
                InvestmentAccount(name="Roth IRA", account_type="roth_ira", balance=100_000,
                                  annual_contribution=7_000, annual_return_pct=7.0, volatility_pct=15.0, owner="self"),
                InvestmentAccount(name="Spouse Roth IRA", account_type="roth_ira", balance=80_000,
                                  annual_contribution=7_000, annual_return_pct=7.0, volatility_pct=15.0, owner="spouse"),
                InvestmentAccount(name="Brokerage", account_type="brokerage", balance=200_000, cost_basis=150_000,
                                  annual_contribution=24_000, annual_return_pct=7.0, volatility_pct=15.0, owner="self"),
                InvestmentAccount(name="HSA", account_type="hsa", balance=30_000,
                                  annual_contribution=8_300, annual_return_pct=4.0, volatility_pct=5.0, owner="self"),
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
            monte_carlo=MonteCarloConfig(),
            roth_conversion=RothConversionConfig(),
        )
