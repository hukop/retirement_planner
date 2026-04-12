# Retirement Planner — Implementation Plan (MVP)

> Python + Dash · California · Married · 5–10 years to retirement · Real estate · No kids/pension/debt

## Goal

Build a personal retirement planning web app that runs locally in the browser. The MVP covers: profile setup (dual-person), income modeling, expense tracking, investment account projections, basic real estate, Social Security claiming strategy, federal + CA tax estimation, withdrawal ordering with RMDs, and interactive projection charts — all wrapped in a polished, modern dashboard.

---

> [!NOTE]
> **Project location:** `c:\Users\huber\proj\finance` ✅ Confirmed
> **Local-only:** All data stays on your machine as JSON files. No server, no cloud, no accounts.
> **GitHub:** User will set up the repo themselves.

> [!WARNING]
> **Tax calculations are estimates.** The app will use 2024/2025 federal and CA brackets as a baseline. Customizable brackets for future years will be added as an advanced feature. It is not a substitute for professional tax advice.

---

## Architecture Overview

```
finance/
├── app.py                    # Entry point — run this to start the app
├── requirements.txt          # Python dependencies
├── assets/                   # Dash static assets (CSS, fonts)
│   └── styles.css            # Global stylesheet (modern dark theme)
│
├── engine/                   # Pure Python calculation engine (no UI)
│   ├── __init__.py
│   ├── models.py             # Dataclasses for all inputs
│   ├── social_security.py    # SS benefit calculations
│   ├── taxes.py              # Federal + CA tax estimation
│   ├── investments.py        # Account growth, contributions, returns
│   ├── real_estate.py        # Property value, equity, mortgage amortization
│   ├── withdrawal.py         # Withdrawal ordering + RMDs
│   └── projections.py        # Year-by-year projection engine (ties everything together)
│
├── ui/                       # Dash UI layer
│   ├── __init__.py
│   ├── layout.py             # Top-level layout, sidebar navigation
│   ├── components.py         # Reusable UI components (cards, inputs, section headers)
│   ├── callbacks/            # Dash callbacks (interactivity)
│   │   ├── __init__.py
│   │   ├── profile_cb.py     # Profile tab callbacks
│   │   ├── income_cb.py      # Income tab callbacks
│   │   ├── expenses_cb.py    # Expenses tab callbacks
│   │   ├── investments_cb.py # Investments tab callbacks
│   │   ├── real_estate_cb.py # Real estate tab callbacks
│   │   ├── projections_cb.py # Projections tab callbacks
│   │   └── persistence_cb.py # Save/load profile callbacks
│   └── pages/                # Page layouts (one per tab)
│       ├── __init__.py
│       ├── dashboard.py      # Summary dashboard (landing page)
│       ├── profile.py        # Profile & settings inputs
│       ├── income.py         # Income sources
│       ├── expenses.py       # Expense categories
│       ├── investments.py    # Investment accounts
│       ├── real_estate.py    # Properties
│       └── projections.py    # Charts & visualizations
│
└── data/                     # User data (gitignored)
    └── profiles/             # Saved profiles as JSON
```

---

## Proposed Changes

### Dependencies

#### [NEW] requirements.txt

```
dash>=2.17
dash-bootstrap-components>=1.6
plotly>=5.22
pandas>=2.2
numpy>=1.26
```

- **dash** — Core web framework
- **dash-bootstrap-components** — Professional component library (Bootstrap 5 themes)
- **plotly** — Interactive charting
- **pandas** — Tabular projection data
- **numpy** — Financial math (NPV, growth calculations)

---

### Engine Layer (Pure Python — no Dash dependency)

The engine is intentionally separated from the UI so it can be tested independently and potentially reused (e.g., CLI, Jupyter notebook).

#### [NEW] engine/models.py

Central data model using Python dataclasses. Everything the app needs to know:

```python
@dataclass
class Person:
    name: str
    current_age: int
    retirement_age: int
    life_expectancy: int = 90
    ss_monthly_benefit: float = 0      # estimated SS benefit at full retirement age
    ss_claiming_age: int = 67          # when to claim (62-70)

@dataclass
class IncomeSource:
    name: str
    annual_amount: float
    annual_raise_pct: float = 0.0
    start_age: int = 0                 # age when income starts (0 = now)
    end_age: int = 0                   # age when income stops (0 = retirement)
    owner: str = "self"                # "self" or "spouse"

@dataclass
class Expense:
    name: str
    annual_amount: float
    category: str                      # housing, food, transport, healthcare, discretionary
    retirement_pct: float = 100.0      # % of this expense that continues in retirement
    inflation_adjusted: bool = True

@dataclass
class InvestmentAccount:
    name: str
    account_type: str                  # "401k", "trad_ira", "roth_ira", "brokerage", "hsa", "savings"
    balance: float
    annual_contribution: float = 0
    employer_match: float = 0          # annual employer match amount
    annual_return_pct: float = 7.0
    owner: str = "self"

@dataclass
class Property:
    name: str
    property_type: str                 # "primary" or "rental"
    current_value: float
    appreciation_rate: float = 3.0
    mortgage_balance: float = 0
    monthly_payment: float = 0
    mortgage_rate: float = 0
    years_remaining: int = 0
    monthly_rental_income: float = 0   # for rental properties
    monthly_expenses: float = 0        # maintenance, insurance, tax, etc.

@dataclass
class PlanProfile:
    """Top-level container for all user inputs."""
    self_person: Person
    spouse: Person
    filing_status: str = "married_jointly"
    inflation_rate: float = 3.0
    incomes: list[IncomeSource]
    expenses: list[Expense]
    accounts: list[InvestmentAccount]
    properties: list[Property]
```

#### [NEW] engine/social_security.py

- Calculate adjusted SS benefit based on claiming age (62–70)
- Early claiming reduction (before FRA): ~6.67%/yr for first 3 years, ~5%/yr beyond
- Delayed credits (after FRA): 8%/yr up to age 70
- Apply to both self and spouse independently
- Inflation-adjust benefits in projection years

#### [NEW] engine/taxes.py

- **2024/2025 Federal brackets** for married filing jointly
- **2024/2025 California brackets** for married filing jointly
- Standard deduction (federal + CA)
- Long-term capital gains rates (federal); CA taxes as ordinary income
- Combined effective tax rate calculation
- Inputs: taxable income by source type → outputs: total federal + state tax
- Handle tax-deferred withdrawals as ordinary income
- Handle Roth withdrawals as tax-free
- Handle brokerage withdrawals with cost-basis assumptions

#### [NEW] engine/investments.py

- Compound growth per account per year
- Add contributions during working years
- Add employer match for 401k
- Stop contributions at retirement
- Track cost basis for brokerage (simplified: assume basis = 50% of balance at retirement)
- Support different return rates per account

#### [NEW] engine/real_estate.py

- Property appreciation modeling
- Mortgage amortization (monthly → annual summary)
- Net equity = value − mortgage balance
- Rental property: net income = rental income − expenses − mortgage
- Auto-detect mortgage payoff year → housing expense drops
- Include net equity in net worth

#### [NEW] engine/withdrawal.py

- **Sequential withdrawal order:** taxable (brokerage) → tax-deferred (401k/IRA) → Roth
- **RMD calculation:** using IRS Uniform Lifetime Table, starting at age 73
- Force RMD withdrawals from tax-deferred accounts even if not "needed"
- If RMD > needed withdrawal, excess goes to brokerage/savings
- Calculate annual withdrawal needs: expenses − income sources (SS, rental, other)

#### [NEW] engine/projections.py

The core engine. Runs a **month-by-month** simulation from current age to death:

```
For each month (current_age → life_expectancy):
  1. Calculate monthly income (salary/12 if working, SS if claiming age reached, rental net, other/12)
  2. Calculate monthly expenses (inflation-adjusted, reduced housing if mortgage paid off)
  3. If working: add monthly contributions + match to accounts, grow all accounts (monthly compounding)
  4. If retired:
     a. Determine monthly withdrawal need = expenses − income
     b. Calculate annual RMDs (spread across 12 months from tax-deferred accounts)
     c. Execute withdrawal strategy (taxable → deferred → Roth)
     d. Grow remaining balances (monthly compounding)
  5. Estimate monthly tax withholding (annual bracket calculation / 12)
  6. Record: year, month, age, income, expenses, taxes, each account balance, net worth, withdrawals
```

Output:
- **Monthly DataFrame** — `pd.DataFrame` with one row per month (~360 rows for 30 years). Stored as the source of truth.
- **Annual summary** — helper method aggregates monthly data to annual totals/snapshots for chart display. Balances use end-of-year snapshot; income, expenses, taxes, and withdrawals are summed across 12 months.

---

### UI Layer (Dash + Bootstrap)

#### [NEW] assets/styles.css

Modern design system:
- **Dark theme** base (`#0f1117` background, `#1a1d27` cards)
- **Accent colors**: blue (`#4a7af7`) for primary, green (`#34d399`) for positive, red (`#f87171`) for negative
- Glassmorphism cards with subtle borders
- Smooth transitions on all interactive elements
- Google Font: **Inter** for clean typography
- Responsive layout (sidebar collapses on mobile)
- Custom Plotly chart theme to match dark UI

#### [NEW] ui/layout.py

Top-level app layout:
- **Left sidebar** — navigation with icons (Dashboard, Profile, Income, Expenses, Investments, Real Estate, Projections)
- **Top bar** — app title, save/load profile buttons
- **Content area** — renders active page
- Uses `dcc.Location` for URL-based routing between tabs

#### [NEW] ui/components.py

Reusable building blocks:
- `metric_card(title, value, subtitle, trend)` — summary stat card with large number
- `section_card(title, children)` — bordered card for grouping inputs
- `input_row(label, id, type, default, tooltip)` — labeled input with help text
- `add_remove_list(id, item_template)` — dynamic list for income sources, accounts, etc.

#### [NEW] ui/pages/dashboard.py

Landing page — at-a-glance summary:
- **4 metric cards**: Years to retirement, Nest egg at retirement, Monthly retirement income, Plan success (money lasts until age X)
- **Mini net worth chart** — small projection sparkline
- **Income breakdown donut** — retirement income sources
- **Quick actions** — "Edit Profile", "Run Projections", "Save Plan"

#### [NEW] ui/pages/profile.py

Two-column form:
- Left: Your info (name, age, retirement age, life expectancy)
- Right: Spouse info (same fields)
- Bottom: Filing status dropdown, inflation rate slider

#### [NEW] ui/pages/income.py

- Salary inputs for self + spouse (amount, annual raise %)
- Social Security section: estimated monthly benefit + claiming age slider (62–70) for each person, with a live chart showing benefit at different claiming ages
- Dynamic list: add/remove other income sources (name, amount, start age, end age)

#### [NEW] ui/pages/expenses.py

- Category-based input: housing, food, transport, healthcare, discretionary, other
- Each category: current annual amount + retirement % slider
- One-time expenses list: name, amount, target year
- Summary: total current expenses, estimated retirement expenses

#### [NEW] ui/pages/investments.py

- Table/cards for each account: name, type (dropdown), balance, annual contribution, employer match (401k only), expected return %
- Add/remove accounts dynamically
- Summary: total portfolio balance, total annual contributions, weighted avg return

#### [NEW] ui/pages/real_estate.py

- Primary home card: value, appreciation, mortgage balance, monthly payment, rate, years remaining
- Rental properties (add/remove): same fields + monthly rental income + monthly expenses
- Summary: total real estate equity, net rental income

#### [NEW] ui/pages/projections.py

The main output page — 4 interactive Plotly charts:

1. **Net Worth Over Time** — stacked area chart by account type + real estate equity
2. **Annual Income vs. Expenses** — grouped bar chart with surplus/deficit line
3. **Portfolio Balance** — line chart showing total investment balance drawdown
4. **Retirement Income Breakdown** — stacked bar showing where income comes from each year (SS, withdrawals by account type, rental, other)

All charts:
- **Default to annual view** (aggregated from monthly data) for clean readability
- Option to toggle to monthly view for a selected year range (drill-down)
- Dark theme matching the app
- Hover tooltips with formatted dollar values
- Range slider for zooming
- Vertical line marking retirement year

#### [NEW] ui/callbacks/ (all files)

Callbacks handle:
- Collecting all form inputs → building `PlanProfile` dataclass
- Running `projections.run()` → getting the results DataFrame
- Updating all charts and summary cards reactively
- Save/load profile to/from JSON files in `data/profiles/`
- Dynamic add/remove for list items (income sources, accounts, properties)

---

### Entry Point

#### [NEW] app.py

```python
import dash
from dash import Dash
import dash_bootstrap_components as dbc
from ui.layout import create_layout

app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], suppress_callback_exceptions=True)
app.layout = create_layout()

# Import all callbacks to register them
from ui.callbacks import profile_cb, income_cb, expenses_cb, investments_cb, real_estate_cb, projections_cb, persistence_cb

if __name__ == "__main__":
    app.run(debug=True, port=8050)
```

---

## Resolved Questions

- **HSA modeling**: MVP treats HSA as a simple tax-free investment account (contribute, grow, withdraw). Advanced version will add medical-expense tracking and triple-tax-advantage specifics. ✅
- **Data persistence**: JSON files for profile storage. Each scenario = separate JSON file. ✅
- **Tax brackets**: MVP uses current 2024/2025 brackets. Advanced feature will allow customizing brackets for future years (e.g., modeling bracket sunsets or increases). ✅

---

## Verification Plan

### Automated Tests

1. **Engine unit tests** — validate tax calculations against known brackets, SS adjustments, mortgage amortization, RMD tables
2. **Projection smoke test** — run a sample profile through the projection engine, verify output DataFrame has expected columns and reasonable values
3. Run with: `python -m pytest tests/`

### Manual Verification

1. **Launch the app** (`python app.py`) and walk through each tab
2. **Enter sample data** and verify charts update correctly
3. **Compare a simple scenario** against a manual spreadsheet calculation to validate projections
4. **Save/load** a profile and verify data roundtrips correctly
5. **Cross-check tax estimates** against a simple tax calculator for a known income

---

## Implementation Order

Broken into smaller phases to keep each step manageable:

| Phase | What | Est. Files |
|-------|------|------------|
| **1** | Project setup, `requirements.txt`, `app.py` skeleton, `.gitignore` | 3 |
| **2** | `engine/models.py` — all dataclasses | 1 |
| **3** | `engine/taxes.py` — federal + CA brackets | 1 |
| **4** | `engine/social_security.py` — SS benefit calculations | 1 |
| **5** | `engine/investments.py` — account growth + contributions | 1 |
| **6** | `engine/real_estate.py` + `engine/withdrawal.py` | 2 |
| **7** | `engine/projections.py` — monthly projection engine | 1 |
| **8** | `assets/styles.css` + `ui/components.py` — design system | 2 |
| **9** | `ui/layout.py` + `ui/pages/dashboard.py` + `ui/pages/profile.py` | 3 |
| **10** | `ui/pages/income.py` + `ui/pages/expenses.py` | 2 |
| **11** | `ui/pages/investments.py` + `ui/pages/real_estate.py` | 2 |
| **12** | `ui/pages/projections.py` — charts page | 1 |
| **13** | `ui/callbacks/` — wire up all interactivity | 6-7 |
| **14** | Save/load persistence, polish, integration test | 2-3 |
