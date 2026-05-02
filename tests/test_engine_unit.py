"""
Comprehensive engine unit tests.

Covers every sub-engine in isolation:
- models (dataclass behaviour, round-trip JSON, edge cases)
- projections (staggered retirement, rental income, one-time expenses, tax estimates)
- taxes (bracket math, LTCG stacking, SS taxability, all filing statuses)
- social_security (FRA lookup, claiming factors, COLA, spousal, break-even)
- real_estate (appreciation, amortization, payoff detection, rental net income)
- investments (monthly growth, contributions, proportional cost-basis withdrawals)
- withdrawal (RMD computation, tier ordering, shortfall, overflow deposit)
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date

import numpy as np

from engine.models import (
    Person, IncomeSource, Expense, OneTimeExpense,
    InvestmentAccount, Property, PlanProfile,
    ACCOUNT_TAX_TREATMENT,
)
from engine.projections import ProjectionEngine, run_projection
from engine.taxes import calculate_taxes, _bracket_tax, ss_taxable_federal
from engine.social_security import (
    full_retirement_age, fra_in_years, claiming_factor,
    adjusted_monthly_benefit, spousal_benefit,
    benefit_in_year, compute_ss_benefit, benefit_curve, break_even_age,
)
from engine.real_estate import (
    PropertyState, build_property_portfolio,
    total_equity, total_net_monthly_rental_income,
    amortization_schedule,
)
from engine.investments import (
    AccountState, build_portfolio, grow_all, contribute_all,
    total_balance, total_by_tax_treatment,
)
from engine.withdrawal import (
    distribution_period, annual_rmd, compute_rmd_withdrawals,
    execute_annual_withdrawals, _accounts_in_order,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> PlanProfile:
    """Build a minimal valid PlanProfile with override kwargs."""
    defaults = dict(
        self_person=Person(name="Self", current_age=50, retirement_age=65,
                           life_expectancy=90, ss_monthly_benefit=2000, ss_claiming_age=67),
        spouse=Person(name="Spouse", current_age=48, retirement_age=65,
                      life_expectancy=92, ss_monthly_benefit=1500, ss_claiming_age=67),
        plan_name="Test Plan",
        filing_status="married_jointly",
        inflation_rate_pct=3.0,
        incomes=[],
        expenses=[],
        one_time_expenses=[],
        accounts=[],
        properties=[],
    )
    defaults.update(overrides)
    return PlanProfile(**defaults)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestPerson(unittest.TestCase):
    def test_defaults(self):
        p = Person()
        self.assertEqual(p.name, "Self")
        self.assertEqual(p.retirement_age, 65)

    def test_custom_values(self):
        p = Person(name="Alice", current_age=30, retirement_age=45)
        self.assertEqual(p.retirement_age, 45)


class TestIncomeSource(unittest.TestCase):
    def test_owner_default(self):
        inc = IncomeSource()
        self.assertEqual(inc.owner, "self")

    def test_start_age_zero_means_now(self):
        inc = IncomeSource(start_age=0, end_age=0)
        self.assertEqual(inc.start_age, 0)
        self.assertEqual(inc.end_age, 0)


class TestInvestmentAccount(unittest.TestCase):
    def test_tax_treatment_mapping(self):
        for acct_type, expected in ACCOUNT_TAX_TREATMENT.items():
            a = InvestmentAccount(account_type=acct_type)
            self.assertEqual(a.tax_treatment, expected)

    def test_monthly_return_rate(self):
        a = InvestmentAccount(annual_return_pct=7.0)
        expected = (1 + 0.07) ** (1 / 12) - 1
        self.assertAlmostEqual(a.monthly_return_rate, expected, places=6)

    def test_cost_basis_fallback_50_pct(self):
        a = InvestmentAccount(balance=100_000, cost_basis=None)
        state = AccountState.from_account(a)
        self.assertEqual(state.cost_basis, 50_000)


class TestProperty(unittest.TestCase):
    def test_net_equity(self):
        prop = Property(current_value=500_000, mortgage_balance=200_000)
        self.assertEqual(prop.net_equity, 300_000)

    def test_net_rental_income(self):
        prop = Property(
            property_type="rental",
            monthly_rental_income=3_000,
            monthly_expenses=800,
            monthly_payment=1_200,
        )
        self.assertEqual(prop.net_monthly_rental_income, 1_000)

    def test_primary_has_zero_rental_income(self):
        """Primary residences must return 0 from the model property (gate added)."""
        prop = Property(
            property_type="primary",
            monthly_rental_income=3_000,
            monthly_expenses=800,
            monthly_payment=1_200,
        )
        self.assertEqual(prop.net_monthly_rental_income, 0)

    def test_appreciation_rate_monthly(self):
        prop = Property(appreciation_rate_pct=3.0)
        expected = (1 + 0.03) ** (1 / 12) - 1
        self.assertAlmostEqual(prop.monthly_appreciation_rate, expected, places=6)


class TestPlanProfileSerialization(unittest.TestCase):
    def test_round_trip_dict(self):
        original = PlanProfile.sample()
        d = original.to_dict()
        restored = PlanProfile.from_dict(d)
        self.assertEqual(restored.plan_name, original.plan_name)
        self.assertEqual(restored.self_person.name, original.self_person.name)
        self.assertEqual(restored.spouse.retirement_age, original.spouse.retirement_age)
        self.assertEqual(len(restored.incomes), len(original.incomes))
        self.assertEqual(len(restored.accounts), len(original.accounts))
        self.assertEqual(len(restored.properties), len(original.properties))

    def test_from_dict_ignores_unknown_keys(self):
        d = {"self_person": {"name": "Alice", "bogus_key": 123}}
        p = PlanProfile.from_dict(d)
        self.assertEqual(p.self_person.name, "Alice")
        self.assertEqual(p.self_person.current_age, 50)  # default

    def test_from_dict_none_fallback(self):
        p = PlanProfile.from_dict(None)
        # from_dict(None) returns the sample plan for safety
        self.assertEqual(p.plan_name, "Sample Plan")

    def test_json_round_trip(self):
        original = _make_profile(
            incomes=[IncomeSource(name="Side Gig", annual_amount=10_000, owner="spouse")],
            properties=[Property(name="Rental", property_type="rental", current_value=400_000)],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            original.to_json(path)
            restored = PlanProfile.from_json(path)
            self.assertEqual(restored.plan_name, original.plan_name)
            self.assertEqual(restored.incomes[0].name, "Side Gig")
            self.assertEqual(restored.properties[0].property_type, "rental")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_derived_years(self):
        today = date.today().year
        p = _make_profile(
            self_person=Person(current_age=50, retirement_age=60, life_expectancy=90),
            spouse=Person(current_age=45, retirement_age=55, life_expectancy=85),
        )
        self.assertEqual(p.retirement_year_self, today + 10)
        self.assertEqual(p.retirement_year_spouse, today + 10)
        self.assertEqual(p.plan_end_year, today + 40)


# ---------------------------------------------------------------------------
# Taxes
# ---------------------------------------------------------------------------

class TestBracketTax(unittest.TestCase):
    def test_zero_income(self):
        self.assertEqual(_bracket_tax(0, [(10_000, 0.10), (float("inf"), 0.20)]), 0.0)

    def test_simple_progressive(self):
        brackets = [(10_000, 0.10), (20_000, 0.20), (float("inf"), 0.30)]
        # $15,000 => $10k @ 10% + $5k @ 20% = $2,000
        self.assertEqual(_bracket_tax(15_000, brackets), 2_000.0)

    def test_top_bracket(self):
        brackets = [(10_000, 0.10), (float("inf"), 0.20)]
        # $25,000 => $10k @ 10% + $15k @ 20% = $4,000
        self.assertEqual(_bracket_tax(25_000, brackets), 4_000.0)


class TestSocialSecurityTaxability(unittest.TestCase):
    def test_no_ss_no_tax(self):
        self.assertEqual(ss_taxable_federal(0, 100_000), 0.0)

    def test_below_first_threshold(self):
        # MFJ: combined = 20k + 0.5*10k = 25k < 32k threshold → 0 taxable
        self.assertEqual(ss_taxable_federal(10_000, 20_000), 0.0)

    def test_between_thresholds(self):
        # combined = 28k + 0.5*10k = 33k → between 32k and 44k
        taxable = ss_taxable_federal(10_000, 28_000)
        self.assertGreater(taxable, 0)
        self.assertLess(taxable, 0.50 * 10_000)

    def test_above_high_threshold(self):
        taxable = ss_taxable_federal(20_000, 100_000)
        self.assertEqual(taxable, 0.85 * 20_000)

    def test_single_filer_lower_threshold(self):
        """Single filers hit the 50% zone at $25k combined (not $32k as MFJ)."""
        # combined = 24k + 0.5*10k = 29k → above single lower threshold 25k
        taxable_single = ss_taxable_federal(10_000, 24_000, filing_status="single")
        self.assertGreater(taxable_single, 0,
                           "Single filer at combined $29k should owe SS tax")

        # Same income MFJ → combined 29k < 32k → should be 0
        taxable_mfj = ss_taxable_federal(10_000, 24_000, filing_status="married_jointly")
        self.assertEqual(taxable_mfj, 0.0,
                         "MFJ at combined $29k should owe no SS tax")

    def test_married_separately_fully_exposed(self):
        """MFS filers have $0 threshold — up to 85% of SS is immediately taxable."""
        taxable = ss_taxable_federal(10_000, 0, filing_status="married_separately")
        self.assertEqual(taxable, 0.85 * 10_000)


class TestCalculateTaxes(unittest.TestCase):
    def test_mfj_200k_ordinary(self):
        r = calculate_taxes(200_000, filing_status="married_jointly")
        self.assertGreater(r.federal_tax, 20_000)
        self.assertLess(r.federal_tax, 80_000)
        self.assertGreater(r.ca_tax, 5_000)
        self.assertEqual(r.total_tax, r.federal_tax + r.ca_tax)
        self.assertGreater(r.effective_rate, 0)
        self.assertLess(r.effective_rate, 0.50)

    def test_ltcg_preferential_rate(self):
        # Large LTCG should trigger some LTCG tax but less than ordinary rate
        r = calculate_taxes(50_000, long_term_gains=500_000, filing_status="married_jointly")
        self.assertGreater(r.federal_ltcg_tax, 0)
        # LTCG top rate is 20 %; ordinary top rate is 37 %, so LTCG tax should
        # be materially lower than what the same gains would owe at ordinary rates.
        hypothetical_ordinary = _bracket_tax(500_000, [(float("inf"), 0.37)])
        self.assertLess(r.federal_ltcg_tax, hypothetical_ordinary * 0.6)

    def test_ss_not_taxed_in_ca(self):
        r = calculate_taxes(100_000, ss_income=50_000, filing_status="married_jointly")
        # CA does not tax SS; federal does partially
        self.assertEqual(r.ca_agi, 100_000)  # SS excluded from CA AGI

    def test_single_filing_status(self):
        r = calculate_taxes(100_000, filing_status="single")
        self.assertGreater(r.federal_tax, 0)
        self.assertGreater(r.ca_tax, 0)

    def test_married_separately(self):
        r = calculate_taxes(100_000, filing_status="married_separately")
        self.assertGreater(r.federal_tax, 0)

    def test_zero_income(self):
        r = calculate_taxes(0)
        self.assertEqual(r.federal_tax, 0)
        self.assertEqual(r.ca_tax, 0)
        self.assertEqual(r.effective_rate, 0)

    def test_marginal_rate_monotonicity(self):
        """Higher income should not decrease marginal rate."""
        r1 = calculate_taxes(50_000)
        r2 = calculate_taxes(500_000)
        self.assertGreaterEqual(r2.marginal_federal_rate, r1.marginal_federal_rate)


# ---------------------------------------------------------------------------
# Social Security
# ---------------------------------------------------------------------------

class TestFullRetirementAge(unittest.TestCase):
    def test_1937_born(self):
        self.assertEqual(full_retirement_age(1937), (65, 0))

    def test_1954_born(self):
        self.assertEqual(full_retirement_age(1954), (66, 0))

    def test_1960_born(self):
        self.assertEqual(full_retirement_age(1960), (67, 0))

    def test_1990_born(self):
        self.assertEqual(full_retirement_age(1990), (67, 0))

    def test_fra_decimal(self):
        self.assertAlmostEqual(fra_in_years(1955), 66 + 2 / 12, places=3)


class TestClaimingFactor(unittest.TestCase):
    def test_at_fra(self):
        f = claiming_factor(67, 1960)
        self.assertEqual(f, 1.0)

    def test_early_62(self):
        f = claiming_factor(62, 1960)
        self.assertAlmostEqual(f, 0.70, places=2)

    def test_delayed_70(self):
        f = claiming_factor(70, 1960)
        self.assertAlmostEqual(f, 1.24, places=2)

    def test_clamped_to_62_70(self):
        f1 = claiming_factor(60, 1960)
        f2 = claiming_factor(62, 1960)
        self.assertEqual(f1, f2)

        f3 = claiming_factor(75, 1960)
        f4 = claiming_factor(70, 1960)
        self.assertEqual(f3, f4)


class TestAdjustedMonthlyBenefit(unittest.TestCase):
    def test_zero_pia(self):
        self.assertEqual(adjusted_monthly_benefit(0, 67, 1960), 0.0)

    def test_at_fra_2000_pia(self):
        self.assertEqual(adjusted_monthly_benefit(2_000, 67, 1960), 2_000.0)

    def test_early_reduction(self):
        early = adjusted_monthly_benefit(2_000, 62, 1960)
        fra = adjusted_monthly_benefit(2_000, 67, 1960)
        self.assertLess(early, fra)

    def test_delayed_increase(self):
        delayed = adjusted_monthly_benefit(2_000, 70, 1960)
        fra = adjusted_monthly_benefit(2_000, 67, 1960)
        self.assertGreater(delayed, fra)


class TestSpousalBenefit(unittest.TestCase):
    def test_own_benefit_higher(self):
        own = spousal_benefit(own_pia=2_000, spouse_pia=1_000,
                              own_claiming_age=67, own_birth_year=1960, spouse_birth_year=1960)
        self.assertEqual(own, 2_000)

    def test_spousal_benefit_higher(self):
        sb = spousal_benefit(own_pia=500, spouse_pia=3_000,
                             own_claiming_age=67, own_birth_year=1960, spouse_birth_year=1960)
        self.assertEqual(sb, 1_500)

    def test_early_claiming_reduces_both(self):
        sb_62 = spousal_benefit(500, 3_000, 62, 1960, 1960)
        sb_67 = spousal_benefit(500, 3_000, 67, 1960, 1960)
        self.assertLess(sb_62, sb_67)


class TestBenefitInYear(unittest.TestCase):
    def test_before_claim_year(self):
        self.assertEqual(benefit_in_year(2_000, 2030, 2025, 0.03), 0.0)

    def test_cola_applied(self):
        base = benefit_in_year(2_000, 2025, 2025, 0.03)
        future = benefit_in_year(2_000, 2025, 2026, 0.03)
        self.assertAlmostEqual(future, base * 1.03, places=2)

    def test_zero_base(self):
        self.assertEqual(benefit_in_year(0, 2025, 2030, 0.03), 0.0)


class TestComputeSSBenefit(unittest.TestCase):
    def test_benefit_structure(self):
        p = Person(current_age=60, ss_monthly_benefit=2_500, ss_claiming_age=67)
        b = compute_ss_benefit(p, current_year=2025)
        self.assertEqual(b.pia_monthly, 2_500)
        self.assertEqual(b.adjusted_monthly, 2_500)
        self.assertEqual(b.claim_year, 2032)

    def test_early_claim(self):
        p = Person(current_age=60, ss_monthly_benefit=2_500, ss_claiming_age=62)
        b = compute_ss_benefit(p, current_year=2025)
        self.assertLess(b.adjusted_monthly, 2_500)
        self.assertEqual(b.claim_year, 2027)

    def test_benefit_curve(self):
        rows = benefit_curve(2_000, birth_year=1960)
        self.assertEqual(len(rows), 9)  # 62-70 inclusive
        self.assertTrue(any(r["is_fra"] for r in rows))

    def test_break_even_exists(self):
        be = break_even_age(2_000, birth_year=1960)
        self.assertIsNotNone(be)
        self.assertGreater(be, 70)


# ---------------------------------------------------------------------------
# Real Estate
# ---------------------------------------------------------------------------

class TestPropertyState(unittest.TestCase):
    def test_from_property_paid_off(self):
        prop = Property(mortgage_balance=0)
        state = PropertyState.from_property(prop)
        self.assertTrue(state.is_paid_off)

    def test_appreciation(self):
        prop = Property(current_value=100_000, appreciation_rate_pct=12.0)
        state = PropertyState.from_property(prop)
        gain = state.appreciate()
        self.assertGreater(gain, 0)
        expected_rate = (1 + 0.12) ** (1 / 12) - 1
        self.assertAlmostEqual(gain, 100_000 * expected_rate, places=2)

    def test_amortization_reduces_balance(self):
        prop = Property(mortgage_balance=100_000, mortgage_rate_pct=6.0,
                        monthly_payment=599.55, years_remaining=30)
        state = PropertyState.from_property(prop)
        initial = state.mortgage_balance
        state.amortize(current_year=2025)
        self.assertLess(state.mortgage_balance, initial)

    def test_payoff_detection(self):
        prop = Property(mortgage_balance=1_000, mortgage_rate_pct=0,
                        monthly_payment=1_000, years_remaining=1)
        state = PropertyState.from_property(prop)
        state.amortize(current_year=2025)
        self.assertTrue(state.is_paid_off)
        self.assertEqual(state.mortgage_balance, 0)

    def test_rental_income_gated_by_type(self):
        # Must set non-zero mortgage/years so it is NOT auto-paid-off
        rental = Property(property_type="rental", monthly_rental_income=2_000,
                          monthly_expenses=500, monthly_payment=1_000,
                          mortgage_balance=200_000, years_remaining=30)
        primary = Property(property_type="primary", monthly_rental_income=2_000,
                           monthly_expenses=500, monthly_payment=1_000,
                           mortgage_balance=200_000, years_remaining=30)
        self.assertEqual(PropertyState.from_property(rental).net_monthly_rental_income, 500)
        self.assertEqual(PropertyState.from_property(primary).net_monthly_rental_income, 0)

    def test_total_rental_income(self):
        props = [
            Property(property_type="rental", monthly_rental_income=2_000,
                     monthly_expenses=500, monthly_payment=1_000,
                     mortgage_balance=200_000, years_remaining=30),
            Property(property_type="rental", monthly_rental_income=3_000,
                     monthly_expenses=800, monthly_payment=1_500,
                     mortgage_balance=200_000, years_remaining=30),
        ]
        portfolio = build_property_portfolio(props)
        total = total_net_monthly_rental_income(portfolio)
        self.assertEqual(total, 500 + 700)

    def test_rental_inflation(self):
        prop = Property(property_type="rental", monthly_rental_income=2_000,
                        monthly_expenses=500, monthly_payment=1_000,
                        mortgage_balance=200_000, years_remaining=30,
                        rental_inflation_rate_pct=3.0)
        state = PropertyState.from_property(prop)
        
        # Initial state
        self.assertEqual(state.current_monthly_rent, 2_000)
        self.assertEqual(state.current_monthly_expenses, 500)
        self.assertEqual(state.net_monthly_rental_income, 500)
        
        # Step one month
        state.step_month()
        
        infl = (1 + 0.03)**(1/12)
        expected_rent = 2_000 * infl
        expected_expenses = 500 * infl
        
        self.assertAlmostEqual(state.current_monthly_rent, expected_rent, places=5)
        self.assertAlmostEqual(state.current_monthly_expenses, expected_expenses, places=5)
        self.assertAlmostEqual(state.net_monthly_rental_income, expected_rent - expected_expenses - 1_000, places=5)

    def test_amortization_schedule_length(self):
        prop = Property(mortgage_balance=300_000, mortgage_rate_pct=6.0,
                        monthly_payment=1_798.65, years_remaining=30)
        sched = amortization_schedule(prop)
        self.assertGreater(len(sched), 0)
        # Cap is years_remaining*12 + 12 (372 for 30yr), or 480
        self.assertLessEqual(len(sched), 372)

    def test_amortization_schedule_paid_off_empty(self):
        prop = Property(mortgage_balance=0)
        self.assertEqual(amortization_schedule(prop), [])


# ---------------------------------------------------------------------------
# Investments
# ---------------------------------------------------------------------------

class TestAccountState(unittest.TestCase):
    def test_growth_increases_balance(self):
        a = InvestmentAccount(balance=100_000, annual_return_pct=12.0)
        state = AccountState.from_account(a)
        gain = state.grow()
        self.assertGreater(gain, 0)
        self.assertGreater(state.balance, 100_000)

    def test_contribution_increases_basis(self):
        a = InvestmentAccount(balance=0, annual_contribution=12_000)
        state = AccountState.from_account(a)
        deposited = state.contribute()
        self.assertEqual(deposited, 1_000)
        self.assertEqual(state.balance, 1_000)
        self.assertEqual(state.cost_basis, 1_000)

    def test_employer_match(self):
        a = InvestmentAccount(balance=0, annual_contribution=12_000, employer_match=6_000)
        state = AccountState.from_account(a)
        deposited = state.contribute(include_match=True)
        self.assertEqual(deposited, 1_500)

    def test_withdraw_capped_at_balance(self):
        a = InvestmentAccount(balance=10_000)
        state = AccountState.from_account(a)
        result = state.withdraw(20_000)
        self.assertEqual(result.withdrawn, 10_000)
        self.assertEqual(result.shortfall, 10_000)
        self.assertEqual(state.balance, 0)

    def test_brokerage_capital_gain(self):
        a = InvestmentAccount(account_type="brokerage", balance=10_000, cost_basis=6_000)
        state = AccountState.from_account(a)
        result = state.withdraw(5_000)
        # gain fraction = (10k - 6k) / 10k = 0.4
        expected_gain = 5_000 * 0.4
        self.assertAlmostEqual(result.capital_gain, expected_gain, places=2)
        self.assertEqual(result.ordinary_income, 0)

    def test_401k_withdraw_is_ordinary_income(self):
        a = InvestmentAccount(account_type="401k", balance=10_000)
        state = AccountState.from_account(a)
        result = state.withdraw(5_000)
        self.assertEqual(result.ordinary_income, 5_000)
        self.assertEqual(result.capital_gain, 0)

    def test_roth_withdraw_tax_free(self):
        a = InvestmentAccount(account_type="roth_ira", balance=10_000)
        state = AccountState.from_account(a)
        result = state.withdraw(5_000)
        self.assertEqual(result.ordinary_income, 0)
        self.assertEqual(result.capital_gain, 0)

    def test_deposit_increases_basis(self):
        a = InvestmentAccount(account_type="brokerage", balance=10_000, cost_basis=6_000)
        state = AccountState.from_account(a)
        state.deposit(5_000)
        self.assertEqual(state.balance, 15_000)
        self.assertEqual(state.cost_basis, 11_000)

    def test_total_by_tax_treatment(self):
        accounts = [
            InvestmentAccount(account_type="401k", balance=100_000),
            InvestmentAccount(account_type="roth_ira", balance=50_000),
            InvestmentAccount(account_type="brokerage", balance=30_000),
        ]
        portfolio = build_portfolio(accounts)
        totals = total_by_tax_treatment(portfolio)
        self.assertEqual(totals["tax_deferred"], 100_000)
        self.assertEqual(totals["tax_free"], 50_000)
        self.assertEqual(totals["taxable"], 30_000)

    def test_contribute_all_respects_working_status(self):
        a = InvestmentAccount(account_type="brokerage", balance=0,
                              annual_contribution=12_000, owner="spouse")
        portfolio = build_portfolio([a])
        total = contribute_all(portfolio, {"self": True, "spouse": False})
        self.assertEqual(total, 0.0)
        total = contribute_all(portfolio, {"self": False, "spouse": True})
        self.assertEqual(total, 1_000)


# ---------------------------------------------------------------------------
# Withdrawal / RMD
# ---------------------------------------------------------------------------

class TestRMDHelpers(unittest.TestCase):
    def test_distribution_period_before_age(self):
        self.assertIsNone(distribution_period(72))

    def test_distribution_period_73(self):
        self.assertEqual(distribution_period(73), 26.5)

    def test_distribution_period_100(self):
        self.assertEqual(distribution_period(100), 6.4)

    def test_annual_rmd_before_start_age(self):
        self.assertEqual(annual_rmd(1_000_000, 70), 0.0)

    def test_annual_rmd_typical(self):
        rmd = annual_rmd(265_000, 73)
        self.assertEqual(rmd, 265_000 / 26.5)


class TestWithdrawalTiers(unittest.TestCase):
    def test_accounts_in_order(self):
        accounts = [
            InvestmentAccount(account_type="401k", balance=10_000),
            InvestmentAccount(account_type="brokerage", balance=5_000),
            InvestmentAccount(account_type="roth_ira", balance=3_000),
        ]
        portfolio = build_portfolio(accounts)
        ordered = _accounts_in_order(portfolio)
        types = [s.account_type for s in ordered]
        self.assertEqual(types, ["brokerage", "401k", "roth_ira"])


class TestExecuteAnnualWithdrawals(unittest.TestCase):
    def test_simple_full_withdrawal_taxable_first(self):
        accounts = [
            InvestmentAccount(account_type="brokerage", balance=10_000, cost_basis=5_000),
            InvestmentAccount(account_type="401k", balance=10_000),
        ]
        portfolio = build_portfolio(accounts)
        plan = execute_annual_withdrawals(portfolio, net_need=5_000, owner_ages={"self": 50})
        self.assertEqual(plan.shortfall, 0)
        self.assertEqual(plan.total_withdrawn, 5_000)
        # Should have drawn from brokerage first
        brokerage = [s for s in portfolio if s.account_type == "brokerage"][0]
        self.assertEqual(brokerage.balance, 5_000)

    def test_shortfall_when_exhausted(self):
        accounts = [
            InvestmentAccount(account_type="brokerage", balance=1_000),
        ]
        portfolio = build_portfolio(accounts)
        plan = execute_annual_withdrawals(portfolio, net_need=5_000, owner_ages={"self": 50})
        self.assertEqual(plan.shortfall, 4_000)

    def test_rmd_forces_withdrawal(self):
        accounts = [
            InvestmentAccount(account_type="401k", balance=100_000),
        ]
        portfolio = build_portfolio(accounts)
        plan = execute_annual_withdrawals(portfolio, net_need=0, owner_ages={"self": 73})
        self.assertGreater(plan.total_rmd, 0)
        # RMD is forced even if net_need is zero

    def test_rmd_excess_deposited_to_taxable(self):
        accounts = [
            InvestmentAccount(account_type="401k", balance=100_000),
            InvestmentAccount(account_type="brokerage", balance=5_000),
        ]
        portfolio = build_portfolio(accounts)
        plan = execute_annual_withdrawals(portfolio, net_need=0, owner_ages={"self": 73})
        if plan.rmd_excess > 0:
            brokerage = [s for s in portfolio if s.account_type == "brokerage"][0]
            self.assertGreater(brokerage.balance, 5_000)

    def test_rmd_uses_prior_year_balance_not_live_balance(self):
        """
        IRS RMD = prior year-end balance / distribution period.
        If we grow the account 50% during the year and then compute the RMD,
        the prior_balances dict should override the live balance so the RMD
        is based on the smaller, pre-growth number.
        """
        starting_balance = 265_000
        accounts = [InvestmentAccount(account_type="401k", balance=starting_balance)]
        portfolio = build_portfolio(accounts)

        # Simulate 50 % growth (e.g. very good year)
        state = portfolio[0]
        state.balance = starting_balance * 1.50   # live balance is now 397,500

        # Prior year-end balance (what the IRS uses)
        prior_balances = {state.name: starting_balance}

        plan_with_prior = execute_annual_withdrawals(
            portfolio, net_need=0, owner_ages={"self": 73},
            prior_balances=prior_balances,
        )

        # Expected RMD = 265,000 / 26.5 = 10,000 (not 397,500 / 26.5 = 15,000)
        expected_rmd = starting_balance / 26.5
        self.assertAlmostEqual(plan_with_prior.total_rmd, expected_rmd, places=0)

        # Without prior_balances, RMD would be based on the higher live balance
        accounts2 = [InvestmentAccount(account_type="401k", balance=starting_balance)]
        portfolio2 = build_portfolio(accounts2)
        portfolio2[0].balance = starting_balance * 1.50
        plan_without_prior = execute_annual_withdrawals(
            portfolio2, net_need=0, owner_ages={"self": 73},
        )
        # Live-balance RMD should be larger
        self.assertGreater(plan_without_prior.total_rmd, plan_with_prior.total_rmd)


# ---------------------------------------------------------------------------
# Projection Engine — regression & scenario tests
# ---------------------------------------------------------------------------

class TestProjectionEngineScenarios(unittest.TestCase):
    def test_empty_profile_runs_without_error(self):
        p = _make_profile()
        monthly, annual = run_projection(p)
        self.assertFalse(monthly.empty)
        self.assertFalse(annual.empty)

    def test_rental_income_present(self):
        p = _make_profile(
            properties=[
                Property(name="Rental", property_type="rental", current_value=400_000,
                         mortgage_balance=200_000, monthly_payment=1_000,
                         mortgage_rate_pct=4.0, years_remaining=30,
                         monthly_rental_income=2_500, monthly_expenses=500),
            ],
        )
        monthly, _ = run_projection(p)
        rental_months = monthly[monthly["income_rental_net"] > 0]
        self.assertGreater(len(rental_months), 0)
        # Net rental = 2500 - 500 - 1000 = 1000
        first_rental = rental_months.iloc[0]["income_rental_net"]
        self.assertEqual(first_rental, 1_000.0)

    def test_staggered_retirement_spouse_income(self):
        """
        Regression: spouse income must appear during years when self is retired
        but spouse is still working.
        """
        p = _make_profile(
            self_person=Person(name="Self", current_age=55, retirement_age=60,
                               life_expectancy=90, ss_monthly_benefit=0, ss_claiming_age=67),
            spouse=Person(name="Spouse", current_age=50, retirement_age=65,
                          life_expectancy=90, ss_monthly_benefit=0, ss_claiming_age=67),
            incomes=[
                IncomeSource(name="Self Salary", annual_amount=100_000, owner="self"),
                IncomeSource(name="Spouse Salary", annual_amount=80_000, owner="spouse"),
            ],
            expenses=[Expense(name="Living", monthly_amount=5_000, category="other")],
        )
        monthly, annual = run_projection(p)
        # Find years where self is retired (age >= 60) but spouse is working (age < 65)
        # These would be years 5-9 after start (self age 60-64, spouse age 55-59)
        staggered = monthly[(monthly["self_working"] == 0) & (monthly["spouse_working"] == 1)]
        self.assertGreater(len(staggered), 0)
        # Spouse salary should be > 0 in those months
        spouse_income_months = staggered[staggered["income_salary_spouse"] > 0]
        self.assertGreater(len(spouse_income_months), 12,
                             "Spouse should have salary income for at least a full year")

    def test_one_time_expense_hits_once(self):
        this_year = date.today().year
        p = _make_profile(
            one_time_expenses=[OneTimeExpense(name="Trip", amount=20_000, year=this_year)],
        )
        monthly, annual = run_projection(p)
        # Only January of this_year should have the one-time expense
        jan_this_year = monthly[(monthly["year"] == this_year) & (monthly["month"] == 1)]
        self.assertEqual(len(jan_this_year), 1)
        self.assertEqual(jan_this_year.iloc[0]["expense_one_time"], 20_000.0)

        # Other months should have zero one-time expense
        other_months = monthly[(monthly["year"] == this_year) & (monthly["month"] != 1)]
        self.assertTrue((other_months["expense_one_time"] == 0).all())

    def test_expenses_drop_in_retirement(self):
        p = _make_profile(
            self_person=Person(current_age=50, retirement_age=55, life_expectancy=80),
            spouse=Person(current_age=50, retirement_age=55, life_expectancy=80),
            expenses=[
                Expense(name="Housing", monthly_amount=2_000, category="housing",
                        retirement_pct=80, inflation_adjusted=False),
            ],
        )
        monthly, _ = run_projection(p)
        working = monthly[monthly["self_working"] == 1]
        retired = monthly[monthly["self_working"] == 0]
        self.assertGreater(working.iloc[0]["expense_housing"], 0)
        # In retirement, housing drops to 80%
        self.assertAlmostEqual(
            retired.iloc[0]["expense_housing"],
            2_000 * 0.80,
            places=0,
        )

    def test_accounts_grow_over_time(self):
        p = _make_profile(
            self_person=Person(current_age=50, retirement_age=90, life_expectancy=95),
            spouse=Person(current_age=50, retirement_age=90, life_expectancy=95),
            accounts=[
                InvestmentAccount(name="Brokerage", account_type="brokerage",
                                  balance=100_000, annual_return_pct=12.0),
            ],
        )
        monthly, annual = run_projection(p)
        first_nw = monthly.iloc[0]["balance_investment_total"]
        last_nw = monthly.iloc[-1]["balance_investment_total"]
        self.assertGreater(last_nw, first_nw)

    def test_net_worth_tracked(self):
        p = _make_profile(
            accounts=[
                InvestmentAccount(name="Brokerage", account_type="brokerage", balance=100_000),
            ],
            properties=[
                Property(name="Home", current_value=500_000, mortgage_balance=200_000),
            ],
        )
        monthly, annual = run_projection(p)
        self.assertIn("net_worth", monthly.columns)
        self.assertIn("net_worth_eoy", annual.columns)
        self.assertFalse(monthly["net_worth"].isna().any())

    def test_contributions_stop_at_retirement(self):
        p = _make_profile(
            self_person=Person(current_age=50, retirement_age=55, life_expectancy=80),
            spouse=Person(current_age=50, retirement_age=55, life_expectancy=80),
            accounts=[
                InvestmentAccount(name="401k", account_type="401k", balance=10_000,
                                  annual_contribution=12_000, owner="self"),
            ],
        )
        monthly, _ = run_projection(p)
        working = monthly[monthly["self_working"] == 1]
        retired = monthly[monthly["self_working"] == 0]
        self.assertGreater(working["contrib_total"].sum(), 0)
        self.assertEqual(retired["contrib_total"].sum(), 0)

    def test_ss_claiming_age_affects_timing(self):
        p = _make_profile(
            self_person=Person(current_age=62, retirement_age=65, life_expectancy=85,
                               ss_monthly_benefit=2_000, ss_claiming_age=67),
        )
        monthly, _ = run_projection(p)
        # Before claim year, SS should be 0
        this_year = date.today().year
        claim_year = this_year + (67 - 62)
        pre_claim = monthly[monthly["year"] < claim_year]
        self.assertEqual(pre_claim["income_ss_self"].sum(), 0.0)
        # At or after claim year, SS should be > 0
        post_claim = monthly[monthly["year"] >= claim_year]
        self.assertGreater(post_claim["income_ss_self"].sum(), 0)

    def test_projection_surplus_deficit_in_annual(self):
        p = PlanProfile.sample()
        monthly, annual = run_projection(p)
        self.assertIn("surplus_deficit", annual.columns)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
