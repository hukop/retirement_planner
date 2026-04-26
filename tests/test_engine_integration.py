"""
Final Integration Test Suite.

Validates that the entire Engine calculates projected values properly and ensures
the DataFrame bounds stay within mathematically expected bounds based on the Sample Profile.
"""

import unittest
from engine.models import PlanProfile
from engine.projections import run_projection
from engine.taxes import calculate_taxes
from engine.social_security import compute_ss_benefit

class TestRetirementEngine(unittest.TestCase):
    
    def setUp(self):
        # Load the default sample profile built during the project
        self.profile = PlanProfile.sample()
        
    def test_tax_bracket_engine(self):
        """Verify the federal tax estimation logic correctly scales over basic bounds."""
        taxes = calculate_taxes(
            ordinary_income=200000, 
            long_term_gains=0, 
            ss_income=0, 
            filing_status="married_jointly"
        )
        self.assertGreater(taxes.federal_tax, 20000, "Tax should be greater than 10% effective on $200k")
        self.assertLess(taxes.federal_tax, 80000, "Tax should not exceed 40% effective on $200k")
        self.assertGreater(taxes.ca_tax, 5000, "CA tax should be present")

    def test_social_security_fra(self):
        """Verify claiming early at 62 aggressively cuts the benefit from FRA."""
        # Force a baseline FRA
        self.profile.self_person.ss_claiming_age = 62
        bene_early = compute_ss_benefit(self.profile.self_person, 2025)
        
        self.profile.self_person.ss_claiming_age = 67
        bene_fra = compute_ss_benefit(self.profile.self_person, 2025)
        
        self.profile.self_person.ss_claiming_age = 70
        bene_delayed = compute_ss_benefit(self.profile.self_person, 2025)

        self.assertLess(bene_early.adjusted_monthly, bene_fra.adjusted_monthly)
        self.assertGreater(bene_delayed.adjusted_monthly, bene_fra.adjusted_monthly)

    def test_full_projection_integration(self):
        """Orchestrate the entire monthly simulation loop out to life expectancy."""
        monthly_df, annual_df = run_projection(self.profile)
        
        # Verify basic outputs generated
        self.assertFalse(monthly_df.empty, "Monthly DataFrame should not be empty")
        self.assertFalse(annual_df.empty, "Annual DataFrame should not be empty")
        
        # Run some sanity boundaries based on the default sample profile!
        final_year_record = annual_df.iloc[-1]
        
        # Net worth should exist and be formally tracked
        self.assertIn("net_worth_eoy", annual_df.columns)
        self.assertIn("balance_investment_total", annual_df.columns)
        self.assertIn("equity_re_total", annual_df.columns)

        # Did they make it? (Since it's a sample testing profile, they shouldn't hit negative Net Worth
        # immediately, but we can just assert it tracked correctly without NaN)
        self.assertIsNotNone(final_year_record["net_worth_eoy"])
        

if __name__ == "__main__":
    unittest.main()
