"""
Monte Carlo callbacks.

Uses Dash's background callback (background=True) so the simulation runs in
a background thread while the UI stays responsive and shows a live progress bar.

Live chart updates are powered by diskcache: the background worker writes
intermediate MonteCarloResult snapshots every N trials, and a dcc.Interval-
driven callback polls the cache and re-renders the results section.

Progress is reported by passing set_progress() to run_monte_carlo() every 25
trials, which updates:
  - mc-progress-bar    → Bootstrap Progress value (0-100)
  - mc-progress-pct    → "42%" text label
  - mc-progress-label  → "Trial 420 of 1,000"
"""

from __future__ import annotations

import copy
import json

import dash
from dash import Input, Output, State, html, dcc
import dash_bootstrap_components as dbc
import diskcache

from engine.models import PlanProfile, MonteCarloConfig
from engine.monte_carlo import run_monte_carlo, monte_carlo_result_to_dict, MonteCarloResult

# Shared cache for intermediate results (same cache dir as background manager)
import os
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".cache")
_cache = diskcache.Cache(_CACHE_DIR)
_INTERMEDIATE_KEY = "mc_intermediate_result"
_INTERMEDIATE_READY_KEY = "mc_intermediate_ready"


# ── Run Simulation (background callback with live progress) ───────────
@dash.callback(
    output=[
        Output("monte-carlo-store", "data"),
        Output("toast-container", "children", allow_duplicate=True),
    ],
    inputs=dict(
        n_clicks=Input("btn-run-monte-carlo", "n_clicks"),
        profile_data=State("profile-store",  "data"),
        num_trials_input=State("mc-input-num-trials", "value"),
        update_interval_input=State("mc-input-live-interval", "value"),
        adaptive_spending_input=State("mc-input-adaptive-spending", "value"),
    ),
    progress=[
        Output("mc-progress-bar",       "value"),   # 0-100
        Output("mc-progress-pct",       "children"),# "42%"
        Output("mc-progress-label",     "children"),# "Trial 420 of 1,000"
    ],
    running=[
        # Show/hide progress bar container
        (Output("mc-progress-container", "style"),
            {"display": "block", "marginTop": "16px"},
            {"display": "none"}),
        # Disable the run button while running
        (Output("btn-run-monte-carlo", "disabled"), True, False),
        (Output("btn-run-monte-carlo", "style"),
            {"width": "200px", "fontSize": "14px", "fontWeight": "700", "opacity": "0.5"},
            {"width": "200px", "fontSize": "14px", "fontWeight": "700", "opacity": "1.0"}),
        # Enable/disable the live-update polling interval
        (Output("mc-live-interval", "disabled"), False, True),
    ],
    background=True,
    prevent_initial_call=True,
)
def run_simulation(
    set_progress,
    n_clicks,
    profile_data,
    num_trials_input,
    update_interval_input,
    adaptive_spending_input,
):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    # ── Initial progress reset ────────────────────────────────────────
    set_progress((0, "0%", "Starting…"))

    # Clear any stale intermediate data
    _cache.delete(_INTERMEDIATE_KEY)
    _cache.set(_INTERMEDIATE_READY_KEY, False)

    try:
        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()

        # Override num_trials from the dropdown if provided
        if num_trials_input:
            profile.monte_carlo.num_trials = int(num_trials_input)

        # Parse the live update interval (0 = no live updates)
        live_interval = int(update_interval_input or 0)

        mc_config = profile.monte_carlo
        n_total = mc_config.num_trials

        # ── Progress bridge ───────────────────────────────────────────
        def _progress(current: int, total: int) -> None:
            pct_int  = int(current / total * 100)
            pct_str  = f"{pct_int}%"
            label    = f"Trial {current:,} of {total:,}"
            set_progress((pct_int, pct_str, label))

        # ── Intermediate results bridge ───────────────────────────────
        retire_yr = profile.retirement_year_self

        def _intermediate(partial_result: MonteCarloResult) -> None:
            data = monte_carlo_result_to_dict(partial_result)
            data["_retire_yr"] = retire_yr
            _cache.set(_INTERMEDIATE_KEY, data)
            _cache.set(_INTERMEDIATE_READY_KEY, True)

        # dcc.Checklist returns a list: ["on"] if checked, [] if not
        adaptive = bool(adaptive_spending_input and "on" in adaptive_spending_input)

        result = run_monte_carlo(
            profile,
            progress_callback=_progress,
            intermediate_callback=_intermediate if live_interval > 0 else None,
            intermediate_interval=live_interval,
            adaptive_spending=adaptive,
        )
        data = monte_carlo_result_to_dict(result)

        # Clear intermediate data now that final result is ready
        _cache.delete(_INTERMEDIATE_KEY)
        _cache.set(_INTERMEDIATE_READY_KEY, False)

        pct_str = f"{result.success_rate * 100:.1f}%"
        toast   = dbc.Toast(
            f"Completed {n_total:,} trials — Probability of Success: {pct_str}",
            header="Monte Carlo Complete ✅",
            icon="success",
            duration=5000,
            is_open=True,
        )
        return data, toast

    except Exception as e:
        import traceback
        traceback.print_exc()
        _cache.delete(_INTERMEDIATE_KEY)
        _cache.set(_INTERMEDIATE_READY_KEY, False)
        err_toast = dbc.Toast(
            f"Simulation failed: {str(e)}",
            header="Monte Carlo Error ❌",
            icon="danger",
            duration=7000,
            is_open=True,
        )
        return dash.no_update, err_toast


def register_monte_carlo_callbacks(app: dash.Dash) -> None:


    # ── 2. Render live updates ───────────────────────────────────────────
    @app.callback(
        Output("mc-results-area", "children", allow_duplicate=True),
        Input("mc-live-interval", "n_intervals"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def render_live_updates(n_intervals, profile_data):
        ready = _cache.get(_INTERMEDIATE_READY_KEY, False)
        if not ready:
            raise dash.exceptions.PreventUpdate

        data = _cache.get(_INTERMEDIATE_KEY)
        if not data:
            raise dash.exceptions.PreventUpdate

        # Consume snapshot
        _cache.set(_INTERMEDIATE_READY_KEY, False)

        from engine.models import PlanProfile
        from engine.monte_carlo import MonteCarloResult
        from ui.pages.monte_carlo import _results_section

        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
        retire_yr = profile.retirement_year_self
        
        # Clean data for reconstruction
        data.pop("_retire_yr", None)
        result = MonteCarloResult(**data)
        
        return _results_section(result, retire_yr)

    # ── 3. Render final results ──────────────────────────────────────────
    @app.callback(
        Output("mc-results-area", "children", allow_duplicate=True),
        Input("monte-carlo-store", "data"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def render_final_results(final_data, profile_data):
        if not final_data:
            from ui.pages.monte_carlo import _empty_state
            return _empty_state()

        from engine.models import PlanProfile
        from engine.monte_carlo import MonteCarloResult
        from ui.pages.monte_carlo import _results_section

        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
        retire_yr = profile.retirement_year_self
        
        result = MonteCarloResult(**final_data)
        return _results_section(result, retire_yr)
