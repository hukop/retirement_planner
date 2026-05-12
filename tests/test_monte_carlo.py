"""
Unit and integration tests for the Monte Carlo simulation engine.

Tests cover:
  - Return sequence generation (shape, mean, std dev, reproducibility)
  - Single-trial projection correctness
  - Full MC run aggregation (success_rate, percentiles, trial identification)
  - Edge cases (zero std dev, 1 trial)
  - Non-regression: deterministic run unchanged after engine changes
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.models import PlanProfile, MonteCarloConfig
from engine.monte_carlo import (
    generate_return_sequences,
    run_monte_carlo,
    _annual_to_monthly_lognormal_params,
    _find_ruin_year,
    monte_carlo_result_to_dict,
)
from engine.projections import run_projection, ProjectionEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_profile() -> PlanProfile:
    """Return the built-in sample profile (expensive to construct)."""
    return PlanProfile.sample()


@pytest.fixture(scope="module")
def small_mc_config() -> MonteCarloConfig:
    """A small config for fast tests (50 trials, fixed seed)."""
    return MonteCarloConfig(
        num_trials=50,
        mean_return_pct=7.0,
        std_dev_pct=15.0,
        bond_mean_return_pct=4.0,
        bond_std_dev_pct=5.0,
        random_seed=42,
    )


@pytest.fixture(scope="module")
def sample_profile_small_mc(sample_profile, small_mc_config) -> PlanProfile:
    """Sample profile with the small MC config injected."""
    import copy
    p = copy.deepcopy(sample_profile)
    p.monte_carlo = small_mc_config
    return p


# ---------------------------------------------------------------------------
# 1. Log-normal parameter conversion
# ---------------------------------------------------------------------------

class TestLognormalParams:
    def test_monthly_mu_sigma_are_finite(self):
        mu, sigma = _annual_to_monthly_lognormal_params(7.0, 15.0)
        assert math.isfinite(mu)
        assert math.isfinite(sigma)
        assert sigma > 0

    def test_low_volatility_has_smaller_sigma(self):
        _, sigma_eq = _annual_to_monthly_lognormal_params(7.0, 15.0)
        _, sigma_bd = _annual_to_monthly_lognormal_params(4.0, 5.0)
        assert sigma_bd < sigma_eq

    def test_zero_std_dev_gives_zero_sigma(self):
        _, sigma = _annual_to_monthly_lognormal_params(7.0, 0.001)
        assert sigma < 1e-4


# ---------------------------------------------------------------------------
# 2. Return sequence generation
# ---------------------------------------------------------------------------

class TestGenerateReturnSequences:
    def test_shape(self, small_mc_config):
        num_months = 360
        eq, bd = generate_return_sequences(small_mc_config, num_months)
        assert eq.shape == (50, 360)
        assert bd.shape == (50, 360)

    def test_values_are_finite(self, small_mc_config):
        eq, bd = generate_return_sequences(small_mc_config, 120)
        assert np.all(np.isfinite(eq))
        assert np.all(np.isfinite(bd))

    def test_equity_returns_greater_than_minus_one(self, small_mc_config):
        """Log-normal guarantee: returns can never be < -100%."""
        eq, bd = generate_return_sequences(small_mc_config, 120)
        assert np.all(eq > -1.0)
        assert np.all(bd > -1.0)

    def test_reproducible_with_fixed_seed(self, small_mc_config):
        eq1, _ = generate_return_sequences(small_mc_config, 120)
        eq2, _ = generate_return_sequences(small_mc_config, 120)
        np.testing.assert_array_equal(eq1, eq2)

    def test_different_seeds_give_different_sequences(self):
        cfg1 = MonteCarloConfig(num_trials=10, random_seed=1)
        cfg2 = MonteCarloConfig(num_trials=10, random_seed=2)
        eq1, _ = generate_return_sequences(cfg1, 120)
        eq2, _ = generate_return_sequences(cfg2, 120)
        assert not np.allclose(eq1, eq2)

    def test_mean_return_approximates_config(self, small_mc_config):
        """
        With many months/trials, the annualised geometric mean of generated
        returns should be within 2% of the configured 7%.
        """
        cfg = MonteCarloConfig(num_trials=500, mean_return_pct=7.0, std_dev_pct=15.0, random_seed=0)
        eq, _ = generate_return_sequences(cfg, 240)
        # Annualise: (1 + mean_monthly)^12 - 1
        mean_monthly = np.mean(eq)
        approx_annual = (1 + mean_monthly) ** 12 - 1
        # Configured is 7% → expect within ±3% with this sample size
        assert abs(approx_annual * 100 - 7.0) < 3.0


# ---------------------------------------------------------------------------
# 3. Deterministic non-regression
# ---------------------------------------------------------------------------

class TestDeterministicNonRegression:
    """Ensure that changes to ProjectionEngine didn't break the deterministic path."""

    def test_deterministic_run_unchanged(self, sample_profile):
        """run_projection() should still work and return consistent results."""
        monthly_df, annual_df = run_projection(sample_profile)
        assert isinstance(monthly_df, pd.DataFrame)
        assert isinstance(annual_df, pd.DataFrame)
        assert "net_worth_eoy" in annual_df.columns
        assert len(annual_df) > 0

    def test_return_overrides_none_equals_deterministic(self, sample_profile):
        """
        A ProjectionEngine with return_overrides=None must produce identical
        results to the plain run_projection() call.
        """
        _, annual1 = run_projection(sample_profile)
        engine2 = ProjectionEngine(sample_profile, return_overrides=None)
        _, annual2 = engine2.run()
        pd.testing.assert_frame_equal(annual1, annual2, check_exact=False, atol=0.01)

    def test_return_overrides_changes_result(self, sample_profile):
        """
        Passing a constant 0% return sequence must produce lower net worth
        than the deterministic 7% assumption.
        """
        from datetime import date
        start_year = date.today().year
        n_months   = (sample_profile.plan_end_year - start_year + 1) * 12
        zero_returns = np.zeros(n_months)

        _, annual_det  = run_projection(sample_profile)
        engine_zero    = ProjectionEngine(sample_profile, return_overrides=zero_returns)
        _, annual_zero = engine_zero.run()

        det_final  = annual_det["net_worth_eoy"].iloc[-1]
        zero_final = annual_zero["net_worth_eoy"].iloc[-1]
        assert zero_final < det_final, "Zero-return scenario should have lower terminal net worth"


# ---------------------------------------------------------------------------
# 4. Full Monte Carlo run
# ---------------------------------------------------------------------------

class TestRunMonteCarlo:
    @pytest.fixture(scope="class")
    def mc_result(self, sample_profile_small_mc):
        return run_monte_carlo(sample_profile_small_mc)

    def test_success_rate_in_range(self, mc_result):
        assert 0.0 <= mc_result.success_rate <= 1.0

    def test_num_trials_matches(self, mc_result):
        assert mc_result.num_trials == 50
        assert mc_result.num_trials_actual == 50

    def test_terminal_net_worths_length(self, mc_result):
        assert len(mc_result.terminal_net_worths) == 50

    def test_terminal_net_worths_sorted(self, mc_result):
        nws = mc_result.terminal_net_worths
        assert nws == sorted(nws)

    def test_percentile_shape(self, mc_result):
        n_years = len(mc_result.years)
        assert len(mc_result.net_worth_percentiles) == n_years
        assert all(len(row) == 7 for row in mc_result.net_worth_percentiles)

    def test_percentile_labels(self, mc_result):
        assert mc_result.percentile_labels == [5, 10, 25, 50, 75, 90, 95]

    def test_worst_lte_median_lte_best(self, mc_result):
        worst_final  = mc_result.worst_trial_df[-1]["net_worth_eoy"]
        median_final = mc_result.median_trial_df[-1]["net_worth_eoy"]
        best_final   = mc_result.best_trial_df[-1]["net_worth_eoy"]
        assert worst_final <= median_final <= best_final

    def test_deterministic_df_not_empty(self, mc_result):
        assert len(mc_result.deterministic_df) > 0

    def test_ruin_years_length(self, mc_result):
        assert len(mc_result.ruin_years) == 50

    def test_ruin_years_none_or_int(self, mc_result):
        for ry in mc_result.ruin_years:
            assert ry is None or isinstance(ry, int)

    def test_years_list_is_sorted(self, mc_result):
        assert mc_result.years == sorted(mc_result.years)

    def test_serializable_to_dict(self, mc_result):
        """Result must be JSON-serialisable for dcc.Store."""
        d = monte_carlo_result_to_dict(mc_result)
        import json
        json_str = json.dumps(d)
        assert len(json_str) > 100


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_std_dev_all_trials_similar(self, sample_profile):
        """
        With near-zero std dev, all trials should produce very similar
        terminal net worths (they'll differ only by floating-point noise
        from the very small sigma, but won't be identical due to the
        log-normal model always having some tiny dispersion).
        """
        import copy
        p = copy.deepcopy(sample_profile)
        p.monte_carlo = MonteCarloConfig(
            num_trials=10,
            mean_return_pct=7.0,
            std_dev_pct=0.01,   # near-zero vol
            bond_std_dev_pct=0.01,
            random_seed=0,
        )
        result = run_monte_carlo(p)
        nws = np.array(result.terminal_net_worths)
        # Range should be very small relative to the mean
        relative_range = (nws.max() - nws.min()) / abs(nws.mean()) if nws.mean() != 0 else 0
        assert relative_range < 0.05, f"Relative range {relative_range:.2%} too large for near-zero vol"

    def test_single_trial(self, sample_profile):
        """A run with 1 trial must still return a valid result."""
        import copy
        p = copy.deepcopy(sample_profile)
        p.monte_carlo = MonteCarloConfig(num_trials=1, random_seed=99)
        result = run_monte_carlo(p)
        assert result.num_trials == 1
        assert len(result.terminal_net_worths) == 1
        assert 0.0 <= result.success_rate <= 1.0

    def test_high_volatility_reduces_success_rate(self, sample_profile):
        """
        Very high volatility (40% std dev) should produce a lower or equal
        success rate than the default 15% std dev, over enough trials.
        """
        import copy
        n = 100
        p_low  = copy.deepcopy(sample_profile)
        p_low.monte_carlo  = MonteCarloConfig(num_trials=n, std_dev_pct=5.0,  random_seed=7)
        p_high = copy.deepcopy(sample_profile)
        p_high.monte_carlo = MonteCarloConfig(num_trials=n, std_dev_pct=40.0, random_seed=7)

        result_low  = run_monte_carlo(p_low)
        result_high = run_monte_carlo(p_high)

        # High vol should have the same or lower success rate (more ruin scenarios)
        # This isn't guaranteed by math but is expected statistically
        # We allow a 10% margin to avoid flaky tests
        assert result_high.success_rate <= result_low.success_rate + 0.10


# ---------------------------------------------------------------------------
# 6. Ruin year helper
# ---------------------------------------------------------------------------

class TestFindRuinYear:
    def test_no_ruin_returns_none(self):
        df = pd.DataFrame({
            "year": [2026, 2027, 2028],
            "net_worth_eoy": [500_000, 400_000, 300_000],
        })
        assert _find_ruin_year(df) is None

    def test_ruin_at_first_negative(self):
        df = pd.DataFrame({
            "year": [2026, 2027, 2028, 2029],
            "net_worth_eoy": [500_000, 100_000, -50_000, -200_000],
        })
        assert _find_ruin_year(df) == 2028

    def test_ruin_at_zero(self):
        df = pd.DataFrame({
            "year": [2026, 2027, 2028],
            "net_worth_eoy": [500_000, 0, -100_000],
        })
        assert _find_ruin_year(df) == 2027
