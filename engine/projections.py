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
from engine.withdrawal import execute_annual_withdrawals, AnnualWithdrawalPlan, _accounts_in_order
from engine.roth_helpers import (
    estimate_annual_ordinary_income,
    estimate_annual_rental_income,
    estimate_annual_ss_income,
    compute_incremental_tax,
    compute_marginal_rates_for_conversion,
)


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
    # Detailed taxes (recorded at year-end true-up)
    tax_federal_ordinary: float = 0.0
    tax_federal_ltcg:     float = 0.0
    tax_ca:               float = 0.0
    # Per-account withdrawal tracking
    withdrawals_by_account: dict[str, float] = field(default_factory=dict)
    # Cumulative monthly net need (sum of monthly_need across the year)
    annual_net_need:  float = 0.0
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

        # Deterministic (default)
        engine = ProjectionEngine(profile)
        monthly_df, annual_df = engine.run()

        # Monte Carlo trial — inject a pre-generated return sequence
        engine = ProjectionEngine(profile, return_overrides=monthly_rates)
        monthly_df, annual_df = engine.run()
    """

    def __init__(
        self,
        profile: PlanProfile,
        return_overrides: Optional[np.ndarray] = None,
        expense_multipliers: Optional[np.ndarray] = None,
        det_annual_nw: Optional[dict[int, float]] = None,
        adaptive_spending: bool = False,
        precomputed_salary_self: Optional[np.ndarray] = None,
        precomputed_salary_spouse: Optional[np.ndarray] = None,
        precomputed_ss_self: Optional[np.ndarray] = None,
        precomputed_ss_spouse: Optional[np.ndarray] = None,
        precomputed_expenses: Optional[list[dict]] = None,
        precomputed_expense_totals: Optional[np.ndarray] = None,
        precomputed_re_equity: Optional[np.ndarray] = None,
        precomputed_rental_net: Optional[np.ndarray] = None,
        precomputed_income_totals: Optional[np.ndarray] = None,
        conversion_schedule: Optional[dict[int, float]] = None,
        conversion_source_types: Optional[list[str]] = None,
    ):
        """
        Parameters
        ----------
        profile         : the retirement plan to simulate
        return_overrides: optional 1-D NumPy array of monthly return rates,
                          length == total simulation months.  When provided,
                          all equity accounts grow at this rate each month
                          instead of using their own configured return rate.
                          When None (default), each account uses its own rate
                          (deterministic projection).
        expense_multipliers: optional 1-D NumPy array of monthly expense
                          scaling factors.  A value of 0.85 means expenses
                          are reduced by 15% that month; 1.07 means +7%.
                          When None (default), expenses are unscaled.
        det_annual_nw   : optional dict mapping year -> EOY net worth of baseline
                          deterministic projection, used for adaptive spending safeguard.
        adaptive_spending: if True, enables Net Worth Safeguarded adaptive spending in-loop.
        conversion_schedule : optional dict mapping {calendar_year: annual_amount_to_convert}.
                          When provided, the engine executes an IRA->Roth transfer in January
                          of each scheduled year, at the live account balances of that moment
                          (i.e. after all prior years of compounding).  The incremental tax is
                          deducted from the first available brokerage account.
        conversion_source_types : account types eligible as conversion sources.
                          Defaults to ["trad_ira"].
        """
        self.profile              = profile
        self.start_year           = date.today().year
        self.return_overrides     = return_overrides
        self.expense_multipliers  = expense_multipliers
        self.det_annual_nw        = det_annual_nw
        self.adaptive_spending    = adaptive_spending
        self.conversion_schedule  = conversion_schedule or {}
        self.conversion_source_types = conversion_source_types or ["trad_ira"]
        # Populated during run() — one dict per conversion event
        self.conversion_events: list[dict] = []

        # Tax shortfall carried over from working years — unpaid tax delta
        # from prior year-end true-up, spread across next year's monthly estimates.
        self._tax_shortfall_carryover: float = 0.0

        # Injected precomputed arrays
        self.precomputed_salary_self   = precomputed_salary_self
        self.precomputed_salary_spouse = precomputed_salary_spouse
        self.precomputed_ss_self       = precomputed_ss_self
        self.precomputed_ss_spouse     = precomputed_ss_spouse
        self.precomputed_expenses       = precomputed_expenses
        self.precomputed_expense_totals = precomputed_expense_totals
        self.precomputed_re_equity     = precomputed_re_equity
        self.precomputed_rental_net    = precomputed_rental_net
        self.precomputed_income_totals = precomputed_income_totals

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    def run(self, fast_path: bool = False) -> tuple[Optional[pd.DataFrame], pd.DataFrame | tuple[list[float], Optional[int]]]:
        """
        Execute the projection and return (monthly_df, annual_df).
        If fast_path is True, returns (None, (eoy_net_worths, ruin_year)).

        Returns
        -------
        monthly_df : one row per calendar month
        annual_df  : one row per calendar year (aggregated / EOY snapshots)
        """
        p = self.profile

        # ── Initialise simulation state ──────────────────────────────────
        invest_portfolio  = build_portfolio(p.accounts)
        re_portfolio      = build_property_portfolio(p.properties) if self.precomputed_expenses is None else None

        # Pre-compute SS benefit profiles (adj. for claiming age)
        ss_self   = compute_ss_benefit(p.self_person,  self.start_year)
        ss_spouse = compute_ss_benefit(p.spouse,        self.start_year)

        # Build account slug → display name map for column naming
        acct_cols = {_slugify(a.name): a.name for a in p.accounts}

        # Simulation range
        end_year = p.plan_end_year
        n_months = (end_year - self.start_year + 1) * 12

        # ── Pre-compute salaries, SS, base expenses, and Real Estate for all months ───
        if self.precomputed_expenses is not None:
            precomputed_salary_self = self.precomputed_salary_self
            precomputed_salary_spouse = self.precomputed_salary_spouse
            precomputed_ss_self = self.precomputed_ss_self
            precomputed_ss_spouse = self.precomputed_ss_spouse
            precomputed_expenses = self.precomputed_expenses
            precomputed_expense_totals = self.precomputed_expense_totals
            precomputed_re_equity = self.precomputed_re_equity
            precomputed_rental_net = self.precomputed_rental_net
            precomputed_income_totals = self.precomputed_income_totals

            self_start_age = p.self_person.current_age
            spouse_start_age = p.spouse.current_age
            self_retire_age = p.self_person.retirement_age
            spouse_retire_age = p.spouse.retirement_age
        else:
            precomputed_salary_self = np.zeros(n_months)
            precomputed_salary_spouse = np.zeros(n_months)
            precomputed_ss_self = np.zeros(n_months)
            precomputed_ss_spouse = np.zeros(n_months)
            precomputed_expenses = []
            precomputed_re_equity = np.zeros(n_months)
            precomputed_rental_net = np.zeros(n_months)

            self_start_age = p.self_person.current_age
            spouse_start_age = p.spouse.current_age
            self_retire_age = p.self_person.retirement_age
            spouse_retire_age = p.spouse.retirement_age

            cola = p.inflation_rate_pct / 100

            # Deterministic property portfolio for precomputation
            re_portfolio_det = build_property_portfolio(p.properties)

            for m in range(n_months):
                year_det  = self.start_year + m // 12
                month_det = m % 12 + 1
                self_age_int_det   = self_start_age + m // 12
                spouse_age_int_det = spouse_start_age + m // 12

                self_working_det   = self_age_int_det  < self_retire_age
                spouse_working_det = spouse_age_int_det < spouse_retire_age
                both_retired_det   = (not self_working_det) and (not spouse_working_det)

                # Real Estate Step (Appreciate + Amortize)
                precomputed_re_equity[m] = sum(s.net_equity for s in re_portfolio_det)
                precomputed_rental_net[m] = sum(s.net_monthly_rental_income for s in re_portfolio_det)
                step_all_month(re_portfolio_det, current_year=year_det)

                # Social Security
                if year_det >= ss_self.claim_year:
                    precomputed_ss_self[m] = ss_self.monthly_in_year(year_det, cola)
                if year_det >= ss_spouse.claim_year:
                    precomputed_ss_spouse[m] = ss_spouse.monthly_in_year(year_det, cola)

                # Salaries
                for src in p.incomes:
                    if src.owner == "self" and self_working_det:
                        start_age = src.start_age or 0
                        end_age   = src.end_age or 100
                        if self_age_int_det >= start_age and self_age_int_det < end_age:
                            years_of_raises = max(0, year_det - self.start_year)
                            annual = src.annual_amount * (1 + src.annual_raise_pct / 100) ** years_of_raises
                            precomputed_salary_self[m] += annual / 12
                    elif src.owner == "spouse" and spouse_working_det:
                        start_age = src.start_age or 0
                        end_age   = src.end_age or 100
                        if spouse_age_int_det >= start_age and spouse_age_int_det < end_age:
                            years_of_raises = max(0, year_det - self.start_year)
                            annual = src.annual_amount * (1 + src.annual_raise_pct / 100) ** years_of_raises
                            precomputed_salary_spouse[m] += annual / 12

                # Expenses
                infl_det = (1 + p.inflation_rate_pct / 100) ** (year_det - self.start_year)

                cat_totals: dict[str, float] = {}
                for exp_item in p.expenses:
                    base = exp_item.monthly_amount
                    if both_retired_det:
                        base = base * exp_item.retirement_factor
                    if exp_item.inflation_adjusted:
                        base = base * infl_det
                    cat_totals[exp_item.category] = cat_totals.get(exp_item.category, 0.0) + base

                one_time_det = 0.0
                if month_det == 1:
                    for ote in p.one_time_expenses:
                        if ote.year == year_det:
                            amt = ote.amount
                            if ote.inflation_adjusted:
                                amt *= infl_det
                            one_time_det += amt

                recurring_total = sum(cat_totals.values())
                grand_total     = recurring_total + one_time_det

                exp_dict = {f"expense_{cat}": round(v, 2) for cat, v in cat_totals.items()}
                exp_dict["expense_one_time"] = round(one_time_det, 2)
                exp_dict["expense_total"]    = round(grand_total, 2)
                precomputed_expenses.append(exp_dict)

            precomputed_expense_totals = np.array([e["expense_total"] for e in precomputed_expenses])
            precomputed_income_totals = (precomputed_salary_self + precomputed_salary_spouse +
                                       precomputed_ss_self + precomputed_ss_spouse +
                                       precomputed_rental_net)

            # Cache precomputed arrays on self for other engines to reuse
            self.precomputed_salary_self   = precomputed_salary_self
            self.precomputed_salary_spouse = precomputed_salary_spouse
            self.precomputed_ss_self       = precomputed_ss_self
            self.precomputed_ss_spouse     = precomputed_ss_spouse
            self.precomputed_expenses      = precomputed_expenses
            self.precomputed_expense_totals= precomputed_expense_totals
            self.precomputed_re_equity     = precomputed_re_equity
            self.precomputed_rental_net    = precomputed_rental_net
            self.precomputed_income_totals = precomputed_income_totals

        # Bootstrap year state
        yr_state = self._bootstrap_year_state(self.start_year)

        rows: list[dict] = []
        eoy_net_worths: list[float] = []
        ruin_year: Optional[int] = None

        trial_eoy_net_worths: dict[int, float] = {}

        # Cache portfolio ordering and start net worth for the loop
        ordered_portfolio = _accounts_in_order(invest_portfolio)
        start_net_worth = sum(a.balance for a in p.accounts) + sum(pr.net_equity for pr in p.properties)

        # Prior year-end balances for IRS-correct RMD calculation.
        # Bootstrap from starting balances (used in year 1 before Jan capture runs).
        prior_balances: dict[str, float] = {
            s.name: s.balance for s in invest_portfolio
        }

        for m in range(n_months):
            year  = self.start_year + m // 12
            month = m % 12 + 1         # 1–12

            # ── Ages (decimal) ───────────────────────────────────────────
            self_age_int   = self_start_age  + m // 12
            spouse_age_int = spouse_start_age       + m // 12

            # ── Working / retired ────────────────────────────────────────
            self_working   = self_age_int  < self_retire_age
            spouse_working = spouse_age_int < spouse_retire_age
            both_retired   = (not self_working) and (not spouse_working)

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
                prior_balances = {
                    s.name: s.balance
                    for s in invest_portfolio
                }

                # ── Roth conversion hook ─────────────────────────────────
                # Apply any scheduled conversion in January of the target year,
                # after accounts have grown through the prior December.
                if year in self.conversion_schedule:
                    self._apply_conversion(
                        invest_portfolio=invest_portfolio,
                        year=year,
                        amount=self.conversion_schedule[year],
                    )

            # ── 1. & 2. Monthly income & expenses ────────────────────────
            if fast_path:
                income_total = precomputed_income_totals[m]
                base_expense_total = precomputed_expense_totals[m]
            else:
                rental_net = precomputed_rental_net[m]
                sal_self = precomputed_salary_self[m]
                sal_spouse = precomputed_salary_spouse[m]
                ss_self_mo = precomputed_ss_self[m]
                ss_spouse_mo = precomputed_ss_spouse[m]

                inc = {
                    "income_salary_self": sal_self,
                    "income_salary_spouse": sal_spouse,
                    "income_ss_self": ss_self_mo,
                    "income_ss_spouse": ss_spouse_mo,
                    "income_rental_net": rental_net,
                    "income_other": 0.0,
                    "income_total": sal_self + sal_spouse + ss_self_mo + ss_spouse_mo + rental_net
                }
                income_total = inc["income_total"]

                exp = precomputed_expenses[m].copy()
                base_expense_total = exp["expense_total"]

            # Apply adaptive spending multiplier
            mult = 1.0
            if self.expense_multipliers is not None and m < len(self.expense_multipliers):
                mult = float(self.expense_multipliers[m])
            elif self.adaptive_spending and self.return_overrides is not None:
                if m >= 12:
                    # Trailing 12 months returns
                    lookback_returns = self.return_overrides[m - 12:m]
                    cum_log = np.sum(np.log1p(lookback_returns))
                    annual_return = np.exp(cum_log) - 1.0

                    if annual_return <= 0:
                        norm = max(-1.0, annual_return / 0.20)
                        mult = 1.0 + norm * 0.15   # cuts up to 15%
                    else:
                        prior_year = year - 1
                        trial_prior_nw = trial_eoy_net_worths.get(prior_year, start_net_worth)

                        baseline_prior_nw = start_net_worth
                        if self.det_annual_nw is not None:
                            baseline_prior_nw = self.det_annual_nw.get(prior_year, start_net_worth)

                        if trial_prior_nw >= baseline_prior_nw:
                            norm = min(1.0, annual_return / 0.20)
                            mult = 1.0 + norm * 0.07   # boosts up to 7%

            if mult != 1.0:
                if fast_path:
                    expense_total = base_expense_total * mult
                else:
                    for k in exp:
                        exp[k] = round(exp[k] * mult, 2)
                    expense_total = exp["expense_total"]
            else:
                expense_total = base_expense_total

            # ── 3. Real estate: appreciate + amortize ────────────────────
            if re_portfolio is not None:
                step_all_month(re_portfolio, current_year=year)

            # ── 4. Contributions (working owners only) ────────────────────
            month_contrib = 0.0
            month_employee_contrib = 0.0
            month_pretax_deduction = 0.0
            month_employer_match = 0.0
            
            if self_working or spouse_working:
                for state in invest_portfolio:
                    owner = state.account.owner
                    if (owner == "self" and self_working) or (owner == "spouse" and spouse_working):
                        dep = state.contribute(include_match=True)
                        month_contrib += dep
                        emp_dep = state.account.annual_contribution / 12
                        match_dep = state.account.employer_match / 12
                        month_employee_contrib += emp_dep
                        month_employer_match += match_dep
                        if state.tax_treatment == "tax_deferred":
                            month_pretax_deduction += emp_dep

            # ── 5. Grow all accounts ──────────────────────────────────────
            month_growth = 0.0
            if self.return_overrides is not None:
                monthly_rate = self.return_overrides[m]
                if fast_path:
                    for idx, s in enumerate(invest_portfolio):
                        rate = float(monthly_rate[idx])
                        growth = s.balance * rate
                        s.balance += growth
                        s.total_growth += growth
                        month_growth += growth
                else:
                    month_growth = grow_all(invest_portfolio, monthly_rate_override=monthly_rate)
            else:
                month_growth = grow_all(invest_portfolio)

            # ── 6. Tax estimate (monthly, based on prior-year eff rate) ────
            if fast_path:
                # Pre-tax deductions lower the annualized estimate
                annual_income_est = (income_total - month_pretax_deduction) * 12
            else:
                annual_income_est = (
                    inc["income_salary_self"] + inc["income_salary_spouse"] +
                    inc["income_other"] + inc["income_ss_self"] + inc["income_ss_spouse"] +
                    inc["income_rental_net"] - month_pretax_deduction
                ) * 12

            monthly_tax_est = self._monthly_tax_estimate(
                yr_state=yr_state,
                annual_income_est=annual_income_est,
                month=month,
            )

            # ── 7. Withdrawals and Surplus ───────────────────────────────────
            # Deduct employee contributions from available cashflow income
            cashflow_income = max(0.0, income_total - month_employee_contrib)
            monthly_need = max(0.0, expense_total - cashflow_income + monthly_tax_est)
            monthly_surplus = max(0.0, cashflow_income - expense_total - monthly_tax_est)

            # Deposit surplus into the first taxable account (brokerage/savings)
            if monthly_surplus > 0:
                for s in ordered_portfolio:
                    if s.tax_treatment == "taxable":
                        s.balance += monthly_surplus
                        s.cost_basis += monthly_surplus
                        s.total_contributed += monthly_surplus
                        month_contrib += monthly_surplus
                        break

            if not fast_path:
                wd_row: dict = {
                    "withdrawal_ordinary": 0.0,
                    "withdrawal_gains":    0.0,
                    "withdrawal_rmd":      0.0,
                    "withdrawal_total":    0.0,
                    "withdrawal_shortfall":0.0,
                    "rmd_excess":          0.0,
                }
            else:
                wd_gains = wd_rmd = wd_total = 0.0

            # Accumulate cumulative net need for December's annual withdrawal
            if both_retired:
                yr_state.annual_net_need += monthly_need

            if both_retired and monthly_need > 0:
                if month == 12:
                    # Withdraw only the remaining annual need not already
                    # taken via monthly withdrawals in months 1-11.
                    annual_need = max(0.0, yr_state.annual_net_need - yr_state.withdrawals_total)
                    owner_ages  = {"self": self_age_int, "spouse": spouse_age_int}
                    wd_plan = execute_annual_withdrawals(
                        invest_portfolio, annual_need, owner_ages,
                        prior_balances=prior_balances,
                    )
                    if fast_path:
                        wd_gains = wd_plan.total_capital_gains
                        wd_rmd = wd_plan.total_rmd
                        wd_total = wd_plan.total_withdrawn
                    else:
                        wd_row = {
                            "withdrawal_ordinary": round(wd_plan.total_ordinary_income, 2),
                            "withdrawal_gains":    round(wd_plan.total_capital_gains,   2),
                            "withdrawal_rmd":      round(wd_plan.total_rmd,             2),
                            "withdrawal_total":    round(wd_plan.total_withdrawn,        2),
                            "withdrawal_shortfall":round(wd_plan.shortfall,             2),
                            "rmd_excess":          round(wd_plan.rmd_excess,            2),
                        }
                        for wr in wd_plan.withdrawals:
                            k = f"withdrawal_{_slugify(wr.account_name)}"
                            wd_row[k] = wd_row.get(k, 0.0) + round(wr.withdrawn, 2)
                else:
                    remaining = monthly_need
                    w_ordinary = w_gains = w_total = 0.0
                    wd_by_acct = {}
                    for state in ordered_portfolio:
                        if remaining <= 0:
                            break
                        wr = state.withdraw(remaining)
                        w_ordinary += wr.ordinary_income
                        w_gains    += wr.capital_gain
                        w_total    += wr.withdrawn
                        k = f"withdrawal_{_slugify(wr.account_name)}"
                        wd_by_acct[k] = wd_by_acct.get(k, 0.0) + wr.withdrawn
                        remaining  -= wr.withdrawn

                    if fast_path:
                        wd_gains = w_gains
                        wd_rmd = 0.0
                        wd_total = w_total
                    else:
                        wd_row = {
                            "withdrawal_ordinary": round(w_ordinary, 2),
                            "withdrawal_gains":    round(w_gains,    2),
                            "withdrawal_rmd":      0.0,
                            "withdrawal_total":    round(w_total,    2),
                            "withdrawal_shortfall":round(max(0, remaining), 2),
                            "rmd_excess":          0.0,
                        }
                        for k, v in wd_by_acct.items():
                            wd_row[k] = round(v, 2)

            # ── 8. Accumulate year-to-date totals ────────────────────────
            if fast_path:
                ss_sum = precomputed_ss_self[m] + precomputed_ss_spouse[m]
                yr_state.income_ordinary += (income_total - precomputed_rental_net[m] - ss_sum - month_pretax_deduction)
                yr_state.income_ss       += ss_sum
                yr_state.income_rental   += precomputed_rental_net[m]
                yr_state.cap_gains       += wd_gains
                yr_state.rmd_total       += wd_rmd
                yr_state.withdrawals_total += wd_total
                yr_state.expense_total   += expense_total
                yr_state.tax_paid        += monthly_tax_est
            else:
                yr_state.income_ordinary += (
                    inc["income_salary_self"] + inc["income_salary_spouse"] + inc["income_other"] - month_pretax_deduction
                )
                yr_state.income_ss       += inc["income_ss_self"] + inc["income_ss_spouse"]
                yr_state.income_rental   += inc["income_rental_net"]
                yr_state.cap_gains       += wd_row["withdrawal_gains"]
                yr_state.rmd_total       += wd_row["withdrawal_rmd"]
                yr_state.withdrawals_total += wd_row["withdrawal_total"]
                yr_state.expense_total   += exp["expense_total"]
                yr_state.tax_paid        += monthly_tax_est

            # ── 8.5. End of Year Tax True-Up ──────────────────────────────
            if month == 12:
                actual_tax_res = calculate_taxes(
                    ordinary_income=yr_state.income_ordinary + yr_state.rmd_total + yr_state.income_rental,
                    long_term_gains=yr_state.cap_gains,
                    ss_income=yr_state.income_ss,
                    filing_status=self.profile.filing_status,
                )
                yr_state.tax_federal_ordinary = actual_tax_res.federal_ordinary_tax
                yr_state.tax_federal_ltcg     = actual_tax_res.federal_ltcg_tax
                yr_state.tax_ca               = actual_tax_res.ca_tax
                
                shortfall = actual_tax_res.total_tax - yr_state.tax_paid

                if shortfall > 0:
                    if both_retired:
                        # Retirement: withdraw the tax shortfall from accounts.
                        remaining_shortfall = shortfall
                        w_ord = w_gains = w_total = 0.0
                        wd_by_acct_shortfall = {}
                        for state in ordered_portfolio:
                            if remaining_shortfall <= 0:
                                break
                            wr = state.withdraw(remaining_shortfall)
                            w_ord += wr.ordinary_income
                            w_gains += wr.capital_gain
                            w_total += wr.withdrawn
                            k = f"withdrawal_{_slugify(wr.account_name)}"
                            wd_by_acct_shortfall[k] = wd_by_acct_shortfall.get(k, 0.0) + wr.withdrawn
                            remaining_shortfall -= wr.withdrawn

                        paid_trueup = shortfall - remaining_shortfall
                        yr_state.tax_paid += paid_trueup
                        yr_state.cap_gains += w_gains
                        yr_state.withdrawals_total += w_total
                        self._tax_shortfall_carryover = 0.0
                        if not fast_path:
                            monthly_tax_est += paid_trueup
                            wd_row["withdrawal_gains"] += w_gains
                            wd_row["withdrawal_ordinary"] += w_ord
                            wd_row["withdrawal_total"] += w_total
                            # Add shortfall withdrawals to the per-account totals
                            for k, v in wd_by_acct_shortfall.items():
                                wd_row[k] = wd_row.get(k, 0.0) + round(v, 2)
                    else:
                        # Working years: carry the shortfall to next year's
                        # monthly tax estimates (deducted from income, not accounts).
                        self._tax_shortfall_carryover = shortfall
                        yr_state.tax_paid += shortfall
                        if not fast_path:
                            monthly_tax_est += shortfall

            # ── 9. Compute net worth ──────────────────────────────────────
            invest_total = 0.0
            for s in invest_portfolio:
                invest_total += s.balance

            re_equity    = precomputed_re_equity[m]
            net_worth    = invest_total + re_equity

            if month == 12:
                trial_eoy_net_worths[year] = net_worth
                if fast_path:
                    eoy_net_worths.append(net_worth)
                    if net_worth <= 0 and ruin_year is None:
                        ruin_year = year

            if fast_path:
                continue
            row: dict = {
                # Time
                "year":            year,
                "month":           month,
                "years_elapsed":   round(m / 12, 3),
                # Ages
                "self_age":        round(self_age_int + (m % 12) / 12,   2),
                "spouse_age":      round(spouse_age_int + (m % 12) / 12, 2),
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
                "contrib_employer_match": round(month_employer_match, 2),
                "growth_total":    round(month_growth,  2),
                # Withdrawals
                **{k: round(v, 2) for k, v in wd_row.items()},
                # Taxes
                "tax_monthly_est": round(monthly_tax_est, 2),
                "tax_federal_ordinary": round(yr_state.tax_federal_ordinary, 2) if month == 12 else 0.0,
                "tax_federal_ltcg": round(yr_state.tax_federal_ltcg, 2) if month == 12 else 0.0,
                "tax_ca": round(yr_state.tax_ca, 2) if month == 12 else 0.0,
                "tax_total_actual": round(yr_state.tax_federal_ordinary + yr_state.tax_federal_ltcg + yr_state.tax_ca, 2) if month == 12 else 0.0,
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
                    cashflow_income + wd_row["withdrawal_total"]
                    - exp["expense_total"] - monthly_tax_est, 2
                ),
            }
            rows.append(row)

        if fast_path:
            return None, (eoy_net_worths, ruin_year)

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
                base = base * exp.retirement_factor
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
    # Roth conversion hook                                                #
    # ------------------------------------------------------------------ #
    def _apply_conversion(
        self,
        invest_portfolio: list,
        year: int,
        amount: float,
    ) -> None:
        """
        Execute a Roth conversion in-place on *invest_portfolio*.

        Steps
        -----
        1.  Withdraw up to *amount* from source accounts (trad_ira by default),
            in order, capped at available balance.
        2.  Deposit the transferred amount into the first Roth IRA account
            (creates a synthetic Roth state if none exists).
        3.  Compute the incremental tax on the conversion and deduct it from
            the first brokerage account (or skip if no brokerage).
        4.  Append a record to ``self.conversion_events`` for reporting.
        """
        from engine.investments import AccountState

        # ── 1. Collect source and destination states ──────────────────────
        source_states = [
            s for s in invest_portfolio
            if s.account.account_type in self.conversion_source_types
        ]
        roth_states = [
            s for s in invest_portfolio
            if s.account.account_type == "roth_ira"
        ]
        brokerage_states = [
            s for s in invest_portfolio
            if s.account.account_type == "brokerage"
        ]

        if not source_states:
            return  # nothing to convert

        # ── 2. Transfer from source → Roth ────────────────────────────────
        remaining = amount
        year_converted = 0.0

        for src in source_states:
            if remaining <= 0:
                break
            take = min(remaining, src.balance)
            src.balance -= take
            year_converted += take
            remaining -= take

        if year_converted <= 0:
            return  # source was exhausted already

        if roth_states:
            roth_states[0].balance += year_converted
        # If no Roth account exists in the portfolio we still record the
        # conversion — the balance just isn't tracked (edge-case; the caller
        # should ensure a Roth account exists when scheduling conversions).

        # ── 3. Deduct incremental tax from brokerage ───────────────────────
        p = self.profile
        base_ordinary = estimate_annual_ordinary_income(p, year, self.start_year)
        base_ordinary += estimate_annual_rental_income(p, year, self.start_year)
        base_ss       = estimate_annual_ss_income(p, year, self.start_year)

        incr_tax = compute_incremental_tax(
            base_ordinary=base_ordinary,
            conversion_amount=year_converted,
            long_term_gains=0.0,
            ss_income=base_ss,
            filing_status=p.filing_status,
        )

        tax_paid = 0.0
        if brokerage_states and incr_tax > 0:
            brok = brokerage_states[0]
            tax_paid = min(incr_tax, brok.balance)
            brok.balance -= tax_paid

        # ── 4. Compute marginal rates for reporting ────────────────────────
        rates = compute_marginal_rates_for_conversion(
            base_ordinary=base_ordinary,
            conversion_amount=year_converted,
            filing_status=p.filing_status,
        )

        self.conversion_events.append({
            "year": year,
            "conversion_amount": round(year_converted, 2),
            "incremental_tax": round(incr_tax, 2),
            "tax_paid_from_brokerage": round(tax_paid, 2),
            "marginal_rate_federal": round(rates["federal_marginal"] * 100, 1),
            "marginal_rate_ca": round(rates["ca_marginal"] * 100, 1),
            "marginal_rate_combined": round(rates["combined_marginal"] * 100, 1),
            "roth_balance_after": round(roth_states[0].balance if roth_states else 0.0, 2),
            "source_balance_after": round(
                sum(s.balance for s in source_states), 2
            ),
        })

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

        Includes any tax shortfall carried over from the previous year's
        working-year true-up, spread evenly across 12 months.
        """
        eff_rate = yr_state.prior_eff_rate
        base = 0.0
        if eff_rate > 0 and annual_income_est > 0:
            base = (annual_income_est * eff_rate) / 12
        # Add prior-year tax shortfall carryover (working years only)
        carryover_monthly = self._tax_shortfall_carryover / 12
        return base + carryover_monthly

    def _compute_annual_eff_rate(self, yr_state: _YearState) -> float:
        """
        Compute the actual effective tax rate for the completed year.
        Used to update the prior-year rate for the next year's estimates.
        """
        p = self.profile
        result = calculate_taxes(
            ordinary_income=yr_state.income_ordinary + yr_state.rmd_total + yr_state.income_rental,
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
        for tax_col in ["tax_federal_ordinary", "tax_federal_ltcg", "tax_ca", "tax_total_actual"]:
            if tax_col in monthly_df.columns:
                flow_cols.append(tax_col)

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

        # Cashflow sanity check (should sum to 0)
        # In = income_total + withdrawal_total + contrib_employer_match
        # Out = expense_total + tax_annual_est + contrib_total
        annual["cashflow_check"] = round(
            annual["income_total"] + annual.get("contrib_employer_match", 0.0) + annual["withdrawal_total"]
            - annual["expense_total"] - annual["tax_annual_est"] - annual["contrib_total"], 2
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
