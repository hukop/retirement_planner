"""
Monthly projection engine — ties all sub-engines together.

Algorithm
---------
Runs a month-by-month simulation from the current date to the maximum life
expectancy of both people.  For each calendar month:

  1.  Determine ages and working/retirement/SS status for each person.
  2.  Compute monthly income from all active sources.
  3.  Compute monthly expenses (inflation-adjusted; retirement-scaled after
      each person stops working; one-time expenses in their target year).
  4.  Apply property appreciation and mortgage amortization.
  5a. If either person is still working: apply monthly contributions + match.
  5b. In all cases: grow all investment accounts (monthly compounding).
  6.  Estimate annual taxes (prior-year-effective-rate method) → monthly tax.
  7.  If in full retirement: compute net withdrawal need and execute the
      seasonal withdrawal strategy (annual RMDs spread monthly; sequential
      tier ordering: taxable → deferred → Roth).
  8.  Record a row for the month: ages, income, expenses, taxes, all account
      balances, real-estate equity, net worth, cash-flow metrics.

Outputs
-------
  run() → (monthly_df, annual_df)

  monthly_df  : pd.DataFrame, one row per month (~480 rows for 40-year plan)
  annual_df   : pd.DataFrame, one row per year (end-of-year balance snapshots,
                summed income/expense/tax for the year)

Tax Estimation
--------------
To avoid the circular dependency (withdrawals drive tax; tax drives withdrawal
need), the engine uses the *prior calendar year's* effective tax rate to
estimate this year's tax burden:

  monthly_tax_estimate = prior_year_effective_rate × (projected annual income) / 12

For year 1 a bootstrap estimate is made from the profile data alone.
The actual effective rate for each completed year is recorded in the DataFrame
so the caller can audit accuracy.

Design notes
------------
- Account names are slugified for DataFrame column names.
- One-time expenses hit in January of their target calendar year
  (inflated to that year's dollar value if inflation_adjusted=True).
- Mortgage payments are handled via the real-estate amortisation and are
  NOT double-counted in the Expense list (treat the Expense categories as
  non-mortgage recurring spend).
- Net rental income IS counted as monthly income (reducing withdrawal need).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from engine.models import PlanProfile, InvestmentAccount, IncomeSource
from engine.taxes import calculate_taxes, TaxResult
from engine.social_security import compute_ss_benefit, SSBenefit
from engine.investments import AccountState, build_portfolio, grow_all, contribute_all
from engine.real_estate import PropertyState, build_property_portfolio, step_all_month
from engine.withdrawal import execute_annual_withdrawals, AnnualWithdrawalPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert an account name to a safe DataFrame column name."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _inflation_factor(base_year: int, current_year: int, rate_pct: float) -> float:
    """Cumulative inflation multiplier from base_year to current_year."""
    return (1 + rate_pct / 100) ** max(0, current_year - base_year)


# ---------------------------------------------------------------------------
# Internal per-year state (reset / updated at January of each year)
# ---------------------------------------------------------------------------
@dataclass
class _YearState:
    """Bookkeeping for one calendar year's running totals."""
    year:             int
    # Accumulated totals (reset each January)
    income_ordinary:  float = 0.0   # salary + other income (for tax)
    income_ss:        float = 0.0   # SS received
    income_rental:    float = 0.0
    cap_gains:        float = 0.0   # realised LTCG from brokerage w/d
    rmd_total:        float = 0.0
    withdrawals_total:float = 0.0
    expense_total:    float = 0.0
    tax_paid:         float = 0.0
    # Prior-year effective rate (bootstrap from profile for year 1)
    prior_eff_rate:   float = 0.0
    # Computed at year-end for next year's estimate
    eff_rate_eoy:     float = 0.0


# ---------------------------------------------------------------------------
# Projection engine
# ---------------------------------------------------------------------------
class ProjectionEngine:
    """
    Run the full retirement projection for a ``PlanProfile``.

    Usage
    -----
    ::

        engine = ProjectionEngine(profile)
        monthly_df, annual_df = engine.run()
    """

    def __init__(self, profile: PlanProfile):
        self.profile    = profile
        self.start_year = date.today().year

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    def run(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Execute the projection and return (monthly_df, annual_df).

        Returns
        -------
        monthly_df : one row per calendar month
        annual_df  : one row per calendar year (aggregated / EOY snapshots)
        """
        p = self.profile

        # ── Initialise simulation state ──────────────────────────────────
        invest_portfolio  = build_portfolio(p.accounts)
        re_portfolio      = build_property_portfolio(p.properties)

        # Pre-compute SS benefit profiles (adj. for claiming age)
        ss_self   = compute_ss_benefit(p.self_person,  self.start_year)
        ss_spouse = compute_ss_benefit(p.spouse,        self.start_year)

        # Build account slug → display name map for column naming
        acct_cols = {_slugify(a.name): a.name for a in p.accounts}

        # Simulation range
        end_year = p.plan_end_year
        n_months = (end_year - self.start_year + 1) * 12

        # Bootstrap year state
        yr_state = self._bootstrap_year_state(self.start_year)

        rows: list[dict] = []

        # Prior year-end balances for IRS-correct RMD calculation.
        # Bootstrap from starting balances (used in year 1 before Jan capture runs).
        prior_balances: dict[str, float] = {
            s.name: s.balance for s in invest_portfolio
        }

        for m in range(n_months):
            year  = self.start_year + m // 12
            month = m % 12 + 1         # 1–12

            # ── Ages (decimal) ───────────────────────────────────────────
            self_age_dec   = p.self_person.current_age  + m / 12
            spouse_age_dec = p.spouse.current_age       + m / 12
            self_age_int   = p.self_person.current_age  + m // 12
            spouse_age_int = p.spouse.current_age       + m // 12

            # ── Working / retired ────────────────────────────────────────
            self_working   = self_age_int  < p.self_person.retirement_age
            spouse_working = spouse_age_int < p.spouse.retirement_age
            both_retired   = (not self_working) and (not spouse_working)
            is_working     = {"self": self_working, "spouse": spouse_working}

            # ── SS active ────────────────────────────────────────────────
            ss_self_active   = year >= ss_self.claim_year
            ss_spouse_active = year >= ss_spouse.claim_year

            # ── January bookkeeping ──────────────────────────────────────
            if month == 1:
                # Capture prior year end-of-year effective rate
                if m > 0:
                    yr_state.eff_rate_eoy = self._compute_annual_eff_rate(yr_state)
                # Start fresh year state
                yr_state = _YearState(
                    year=year,
                    prior_eff_rate=yr_state.eff_rate_eoy,
                )
                # Capture prior-year-end balances for IRS-correct RMD calculation.
                # At January 1 the portfolio has not yet grown this month, so
                # these represent the December 31 snapshot of the prior year.
                prior_balances = {
                    s.name: s.balance
                    for s in invest_portfolio
                }

            infl = _inflation_factor(self.start_year, year, p.inflation_rate_pct)

            # ── 1. Monthly income ────────────────────────────────────────
            inc = self._monthly_income(
                year=year, month=month,
                self_age_int=self_age_int, spouse_age_int=spouse_age_int,
                self_working=self_working, spouse_working=spouse_working,
                ss_self=ss_self, ss_spouse=ss_spouse,
                ss_self_active=ss_self_active, ss_spouse_active=ss_spouse_active,
                re_portfolio=re_portfolio,
                infl=infl,
            )

            # ── 2. Monthly expenses ──────────────────────────────────────
            exp = self._monthly_expenses(
                year=year, month=month,
                self_working=self_working, spouse_working=spouse_working,
                infl=infl,
            )

            # ── 3. Real estate: appreciate + amortize ────────────────────
            step_all_month(re_portfolio, current_year=year)

            # ── 4. Contributions (working owners only) ────────────────────
            if self_working or spouse_working:
                month_contrib = contribute_all(invest_portfolio, is_working)
            else:
                month_contrib = 0.0

            # ── 5. Grow all accounts ──────────────────────────────────────
            month_growth = grow_all(invest_portfolio)

            # ── 6. Tax estimate (monthly, based on prior-year eff rate) ────
            annual_income_est = (
                inc["income_salary_self"] + inc["income_salary_spouse"] +
                inc["income_other"] + inc["income_ss_self"] + inc["income_ss_spouse"]
            ) * 12
            monthly_tax_est = self._monthly_tax_estimate(
                yr_state=yr_state,
                annual_income_est=annual_income_est,
                month=month,
            )

            # ── 7. Withdrawals (retirement months) ───────────────────────
            monthly_need = max(0.0, exp["expense_total"] - inc["income_total"] + monthly_tax_est)
            wd_plan: Optional[AnnualWithdrawalPlan] = None
            wd_row: dict = {
                "withdrawal_ordinary": 0.0,
                "withdrawal_gains":    0.0,
                "withdrawal_rmd":      0.0,
                "withdrawal_total":    0.0,
                "withdrawal_shortfall":0.0,
                "rmd_excess":          0.0,
            }

            if both_retired and monthly_need > 0:
                # Annual RMD-aware withdrawal (called each month with monthly need;
                # RMD forcing only fires on specific months — see withdrawal.py)
                # For simplicity in the monthly loop: do sequential withdrawal each month;
                # run the full annual RMD check once in December.
                if month == 12:
                    # December: do full annual RMD check for any remaining RMD balance
                    annual_need = monthly_need * 12  # re-estimate for Dec settlement
                    owner_ages  = {"self": self_age_int, "spouse": spouse_age_int}
                    wd_plan = execute_annual_withdrawals(
                        invest_portfolio, annual_need, owner_ages,
                        prior_balances=prior_balances,  # IRS prior year-end balances
                    )
                    # Spread December result across 1 month view
                    wd_row = {
                        "withdrawal_ordinary": round(wd_plan.total_ordinary_income, 2),
                        "withdrawal_gains":    round(wd_plan.total_capital_gains,   2),
                        "withdrawal_rmd":      round(wd_plan.total_rmd,             2),
                        "withdrawal_total":    round(wd_plan.total_withdrawn,        2),
                        "withdrawal_shortfall":round(wd_plan.shortfall,             2),
                        "rmd_excess":          round(wd_plan.rmd_excess,            2),
                    }
                else:
                    # Non-December: simple sequential withdrawal for monthly need
                    from engine.withdrawal import _accounts_in_order
                    remaining = monthly_need
                    w_ordinary = w_gains = w_total = 0.0
                    for state in _accounts_in_order(invest_portfolio):
                        if remaining <= 0:
                            break
                        wr = state.withdraw(remaining)
                        w_ordinary += wr.ordinary_income
                        w_gains    += wr.capital_gain
                        w_total    += wr.withdrawn
                        remaining  -= wr.withdrawn
                    wd_row = {
                        "withdrawal_ordinary": round(w_ordinary, 2),
                        "withdrawal_gains":    round(w_gains,    2),
                        "withdrawal_rmd":      0.0,
                        "withdrawal_total":    round(w_total,    2),
                        "withdrawal_shortfall":round(max(0, remaining), 2),
                        "rmd_excess":          0.0,
                    }

            # ── 8. Accumulate year-to-date totals ────────────────────────
            yr_state.income_ordinary += (
                inc["income_salary_self"] + inc["income_salary_spouse"] + inc["income_other"]
            )
            yr_state.income_ss       += inc["income_ss_self"] + inc["income_ss_spouse"]
            yr_state.income_rental   += inc["income_rental_net"]
            yr_state.cap_gains       += wd_row["withdrawal_gains"]
            yr_state.rmd_total       += wd_row["withdrawal_rmd"]
            yr_state.withdrawals_total += wd_row["withdrawal_total"]
            yr_state.expense_total   += exp["expense_total"]
            yr_state.tax_paid        += monthly_tax_est

            # ── 9. Compute net worth ──────────────────────────────────────
            invest_total = sum(s.balance for s in invest_portfolio)
            re_equity    = sum(s.net_equity for s in re_portfolio)
            net_worth    = invest_total + re_equity

            # ── 10. Build row ─────────────────────────────────────────────
            row: dict = {
                # Time
                "year":            year,
                "month":           month,
                "years_elapsed":   round(m / 12, 3),
                # Ages
                "self_age":        round(self_age_dec,   2),
                "spouse_age":      round(spouse_age_dec, 2),
                "self_age_int":    self_age_int,
                "spouse_age_int":  spouse_age_int,
                "self_working":    int(self_working),
                "spouse_working":  int(spouse_working),
                # Income
                **{k: round(v, 2) for k, v in inc.items()},
                # Expenses
                **{k: round(v, 2) for k, v in exp.items()},
                # Contributions & Growth
                "contrib_total":   round(month_contrib, 2),
                "growth_total":    round(month_growth,  2),
                # Withdrawals
                **{k: round(v, 2) for k, v in wd_row.items()},
                # Taxes
                "tax_monthly_est": round(monthly_tax_est, 2),
                # Account balances (end-of-month snapshot)
                **{
                    f"bal_{_slugify(s.account.name)}": round(s.balance, 2)
                    for s in invest_portfolio
                },
                # Totals
                "balance_investment_total": round(invest_total, 2),
                "equity_re_total":          round(re_equity,    2),
                "net_worth":                round(net_worth,    2),
                # Cash flow
                "net_cash_flow": round(
                    inc["income_total"] + wd_row["withdrawal_total"]
                    - exp["expense_total"] - monthly_tax_est, 2
                ),
            }
            rows.append(row)

        monthly_df = pd.DataFrame(rows)
        annual_df  = self._build_annual_summary(monthly_df)
        return monthly_df, annual_df

    # ------------------------------------------------------------------ #
    # Income                                                              #
    # ------------------------------------------------------------------ #
    def _monthly_income(
        self, *,
        year: int, month: int,
        self_age_int: int, spouse_age_int: int,
        self_working: bool, spouse_working: bool,
        ss_self: SSBenefit, ss_spouse: SSBenefit,
        ss_self_active: bool, ss_spouse_active: bool,
        re_portfolio: list[PropertyState],
        infl: float,
    ) -> dict:
        p = self.profile

        # ── Salaries ─────────────────────────────────
        sal_self = sal_spouse = 0.0
        for src in p.incomes:
            if src.owner == "self" and self_working:
                sal_self += self._active_income_monthly(src, self_age_int, year)
            elif src.owner == "spouse" and spouse_working:
                sal_spouse += self._active_income_monthly(src, spouse_age_int, year)

        # ── Social Security ───────────────────────────
        cola = p.inflation_rate_pct / 100
        ss_self_mo   = ss_self.monthly_in_year(year, cola)   if ss_self_active   else 0.0
        ss_spouse_mo = ss_spouse.monthly_in_year(year, cola) if ss_spouse_active else 0.0

        # ── Net rental income ─────────────────────────
        rental_net = sum(s.net_monthly_rental_income for s in re_portfolio)

        # ── Other income sources (not salary, not SS) ─
        other = 0.0  # placeholder — advanced income types extend here

        total = sal_self + sal_spouse + ss_self_mo + ss_spouse_mo + rental_net + other

        return {
            "income_salary_self":   sal_self,
            "income_salary_spouse": sal_spouse,
            "income_ss_self":       round(ss_self_mo,   2),
            "income_ss_spouse":     round(ss_spouse_mo, 2),
            "income_rental_net":    round(rental_net,   2),
            "income_other":         round(other,        2),
            "income_total":         round(total,        2),
        }

    def _active_income_monthly(
        self,
        src: IncomeSource,
        owner_age: int,
        year: int,
    ) -> float:
        """
        Monthly income from one IncomeSource, accounting for active age range
        and annual raise.
        """
        p = self.profile
        # Resolve start/end ages (0 = now / retirement)
        owner_current_age = (
            p.self_person.current_age if src.owner == "self" else p.spouse.current_age
        )
        start_age = src.start_age if src.start_age > 0 else owner_current_age
        end_age   = src.end_age   if src.end_age   > 0 else 999   # never stops

        if owner_age < start_age or owner_age >= end_age:
            return 0.0

        years_of_raises = max(0, year - self.start_year)
        annual = src.annual_amount * (1 + src.annual_raise_pct / 100) ** years_of_raises
        return annual / 12

    # ------------------------------------------------------------------ #
    # Expenses                                                            #
    # ------------------------------------------------------------------ #
    def _monthly_expenses(
        self, *,
        year: int, month: int,
        self_working: bool, spouse_working: bool,
        infl: float,
    ) -> dict:
        p = self.profile
        both_retired = (not self_working) and (not spouse_working)
        cat_totals: dict[str, float] = {}

        for exp in p.expenses:
            base = exp.monthly_amount
            if both_retired:
                base = base * (exp.retirement_pct / 100)
            if exp.inflation_adjusted:
                base = base * infl
            cat_totals[exp.category] = cat_totals.get(exp.category, 0.0) + base

        # One-time expenses: hit in January of their target year
        one_time = 0.0
        if month == 1:
            for ote in p.one_time_expenses:
                if ote.year == year:
                    amt = ote.amount
                    if ote.inflation_adjusted:
                        amt *= infl
                    one_time += amt

        recurring_total = sum(cat_totals.values())
        grand_total     = recurring_total + one_time

        row = {f"expense_{cat}": round(v, 2) for cat, v in cat_totals.items()}
        row["expense_one_time"] = round(one_time, 2)
        row["expense_total"]    = round(grand_total, 2)
        return row

    # ------------------------------------------------------------------ #
    # Tax estimation                                                      #
    # ------------------------------------------------------------------ #
    def _bootstrap_year_state(self, year: int) -> _YearState:
        """
        Estimate the starting effective tax rate from the profile data for year 1.
        Uses total salary income with no SS / withdrawals.
        """
        p = self.profile
        annual_salary = sum(src.annual_amount for src in p.incomes)
        result = calculate_taxes(
            ordinary_income=annual_salary,
            filing_status=p.filing_status,
        )
        ys = _YearState(year=year, prior_eff_rate=result.effective_rate)
        ys.eff_rate_eoy = result.effective_rate
        return ys

    def _monthly_tax_estimate(
        self,
        yr_state: _YearState,
        annual_income_est: float,
        month: int,
    ) -> float:
        """
        Estimate the tax for this month using the prior year's effective rate.
        Returns a monthly dollar amount.
        """
        eff_rate = yr_state.prior_eff_rate
        if eff_rate <= 0 or annual_income_est <= 0:
            return 0.0
        return (annual_income_est * eff_rate) / 12

    def _compute_annual_eff_rate(self, yr_state: _YearState) -> float:
        """
        Compute the actual effective tax rate for the completed year.
        Used to update the prior-year rate for the next year's estimates.
        """
        p = self.profile
        result = calculate_taxes(
            ordinary_income=yr_state.income_ordinary + yr_state.rmd_total,
            long_term_gains=yr_state.cap_gains,
            ss_income=yr_state.income_ss,
            filing_status=p.filing_status,
        )
        return result.effective_rate

    # ------------------------------------------------------------------ #
    # Annual summary                                                      #
    # ------------------------------------------------------------------ #
    def _build_annual_summary(self, monthly_df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate the monthly DataFrame into one row per calendar year.

        Balances use end-of-year (December) snapshots.
        Income, expenses, taxes, and withdrawals are summed across 12 months.
        """
        # Columns to SUM (flows)
        flow_cols = [c for c in monthly_df.columns if c.startswith((
            "income_", "expense_", "withdrawal_", "contrib_", "growth_",
            "rmd_excess", "net_cash_flow",
        ))]
        # tax_monthly_est → annual sum
        flow_cols += ["tax_monthly_est"]

        # Columns to take as END-OF-YEAR SNAPSHOT (December row)
        snapshot_cols = [c for c in monthly_df.columns if c.startswith((
            "bal_", "balance_", "equity_", "net_worth",
        ))]
        # Also snapshot ages and flags
        snapshot_cols += ["self_age", "spouse_age", "self_age_int", "spouse_age_int",
                          "self_working", "spouse_working"]

        # Columns that are constant within a year
        constant_cols = ["year"]

        eoy = monthly_df[monthly_df["month"] == 12]

        agg_flows    = monthly_df.groupby("year")[flow_cols].sum()
        agg_snapshot = eoy.set_index("year")[snapshot_cols]

        annual = agg_flows.join(agg_snapshot)
        annual = annual.reset_index()

        # Rename for clarity
        annual = annual.rename(columns={
            "tax_monthly_est":  "tax_annual_est",
            "self_age":         "self_age_eoy",
            "spouse_age":       "spouse_age_eoy",
            "self_age_int":     "self_age_int_eoy",
            "spouse_age_int":   "spouse_age_int_eoy",
            "net_worth":        "net_worth_eoy",
        })

        # Convenience: surplus / deficit
        annual["surplus_deficit"] = (
            annual["income_total"] + annual["withdrawal_total"]
            - annual["expense_total"] - annual["tax_annual_est"]
        )

        return annual


# ---------------------------------------------------------------------------
# Convenience function (module-level entry point)
# ---------------------------------------------------------------------------
def run_projection(profile: PlanProfile) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Run the full projection for ``profile``.

    Returns
    -------
    monthly_df, annual_df — see ``ProjectionEngine.run()``
    """
    return ProjectionEngine(profile).run()
