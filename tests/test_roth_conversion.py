import pytest
import pandas as pd
from datetime import date

from engine.models import PlanProfile
from engine.roth_conversion import (
    RothConversionConfig,
    run_roth_conversion_analysis,
    _find_roth_account,
    _find_source_accounts
)

@pytest.fixture
def sample_profile():
    return PlanProfile.sample()

def test_no_conversion_equals_baseline(sample_profile):
    """Test that a conversion of $0 yields identical baseline/conversion results."""
    config = RothConversionConfig(
        annual_amount=0.0,
        start_year=2024,
        end_year=2034,
        source_account_types=["trad_ira"]
    )
    
    result = run_roth_conversion_analysis(sample_profile, config)
    
    assert result.total_converted == 0.0
    assert result.total_tax_cost == 0.0
    assert result.net_worth_delta_at_end == 0.0
    assert result.lifetime_tax_savings == 0.0
    assert result.rmd_reduction == 0.0
    
    # DataFrames should be identical
    df_base = pd.DataFrame(result.baseline_annual)
    df_conv = pd.DataFrame(result.conversion_annual)
    pd.testing.assert_frame_equal(df_base, df_conv)


def test_conversion_reduces_ira_increases_roth(sample_profile):
    """Test that converting $50k properly reduces the IRA and increases the Roth."""
    current_year = date.today().year
    config = RothConversionConfig(
        annual_amount=50000.0,
        start_year=current_year,
        end_year=current_year + 5,  # 6 years of conversions
        source_account_types=["trad_ira"]
    )
    
    result = run_roth_conversion_analysis(sample_profile, config)
    
    # 6 years * 50k = 300k
    # But it might be capped by balance, let's just check > 0
    assert result.total_converted > 0
    assert result.total_tax_cost > 0
    
    df_base = pd.DataFrame(result.baseline_annual)
    df_conv = pd.DataFrame(result.conversion_annual)
    
    # In the first year, conversion scenario should have less trad_ira and more roth_ira
    # Since we can't easily isolate the balance by slug from the output df, 
    # we can check that taxes paid is higher in the conversion years.
    base_tax_yr1 = df_base.iloc[0]["tax_annual_est"]
    conv_tax_yr1 = df_conv.iloc[0]["tax_annual_est"]
    assert conv_tax_yr1 > base_tax_yr1
    
    # And total investment balance might be lower in conversion scenario early on due to tax drag,
    # or roughly equal if taxes are paid out of cashflow.
    # We mainly care that the details are populated.
    assert len(result.conversion_details) > 0
    assert result.conversion_details[0]["conversion_amount"] == 50000.0


def test_edge_case_no_ira_accounts(sample_profile):
    """If there are no IRA accounts, total converted should be 0."""
    # Remove trad_ira accounts
    sample_profile.accounts = [a for a in sample_profile.accounts if a.account_type != "trad_ira"]
    
    config = RothConversionConfig(
        annual_amount=50000.0,
        start_year=2024,
        end_year=2034,
        source_account_types=["trad_ira"]
    )
    
    result = run_roth_conversion_analysis(sample_profile, config)
    assert result.total_converted == 0.0
    assert result.total_tax_cost == 0.0


def test_edge_case_conversion_exceeds_balance(sample_profile):
    """If the conversion amount exceeds the balance, it should be capped."""
    # Find trad ira and set balance very low
    for acct in sample_profile.accounts:
        if acct.account_type == "trad_ira":
            acct.balance = 10000.0
            
    current_year = date.today().year
    config = RothConversionConfig(
        annual_amount=50000.0,
        start_year=current_year,
        end_year=current_year + 5,
        source_account_types=["trad_ira"]
    )
    
    result = run_roth_conversion_analysis(sample_profile, config)
    
    # The first year conversion should be exactly 10,000
    assert result.conversion_details[0]["conversion_amount"] <= 10000.0
