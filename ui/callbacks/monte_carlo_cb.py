"""
Monte Carlo callbacks.

Uses Dash's background callback (background=True) so the simulation runs in
a background thread while the UI stays responsive and shows a live progress bar.

All UI state (progress bar, button disabled, progress visibility) is managed
through the set_progress function rather than Dash's `running` parameter.
This prevents stale cached state from persisting when the page components are
recreated during navigation.
"""

from __future__ import annotations

import json
import dash
from dash import Input, Output, State, clientside_callback
import dash_bootstrap_components as dbc
import diskcache

from engine.models import PlanProfile
from engine.monte_carlo import run_monte_carlo, monte_carlo_result_to_dict, MonteCarloResult

# Shared cache for intermediate results (same cache dir as background manager)
import os
import platform

def _get_base_cache_dir():
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base = os.path.join(_PROJECT_ROOT, ".cache")
    if os.name == "posix" and "microsoft" in platform.uname().release.lower() and base.startswith("/mnt/"):
        return os.path.join("/tmp", "finance_planner_cache")
    return base

_CACHE_DIR = os.environ.get("FINANCE_CACHE_DIR", os.path.join(_get_base_cache_dir(), "mc_intermediate"))
os.makedirs(_CACHE_DIR, exist_ok=True)
_cache = diskcache.Cache(_CACHE_DIR)
_INTERMEDIATE_KEY = "mc_intermediate_result"
_INTERMEDIATE_READY_KEY = "mc_intermediate_ready"

# Shared UI state constants
_RUNNING_STYLE = {"display": "block", "marginTop": "16px"}
_HIDDEN_STYLE = {"display": "none"}
_RUN_BTN_DISABLED_STYLE = {"width": "200px", "fontSize": "14px", "fontWeight": "700", "opacity": "0.5"}
_RUN_BTN_ENABLED_STYLE = {"width": "200px", "fontSize": "14px", "fontWeight": "700", "opacity": "1.0"}


def _reset_progress(set_progress):
    """Push initial "running" UI state."""
    set_progress((0, "0%", "Starting…", _RUNNING_STYLE, True, _RUN_BTN_DISABLED_STYLE))


def _idle_progress(set_progress):
    """Push final "idle" UI state."""
    set_progress((0, "0%", "", _HIDDEN_STYLE, False, _RUN_BTN_ENABLED_STYLE))


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
        live_updates_input=State("mc-input-live-updates", "value"),
        adaptive_spending_input=State("mc-input-adaptive-spending", "value"),
    ),
    progress=[
        Output("mc-progress-bar",       "value"),   # 0-100
        Output("mc-progress-pct",       "children"),# "42%"
        Output("mc-progress-label",     "children"),# "Trial 420 of 1,000"
        Output("mc-progress-container", "style"),   # visibility
        Output("btn-run-monte-carlo", "disabled"),  # button disabled
        Output("btn-run-monte-carlo", "style"),     # button style
    ],
    background=True,
    prevent_initial_call=True,
)
def run_simulation(
    set_progress,
    n_clicks,
    profile_data,
    num_trials_input,
    live_updates_input,
    adaptive_spending_input,
):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    # ── Initial progress reset ────────────────────────────────────────
    _reset_progress(set_progress)

    # Clear any stale intermediate data
    _cache.delete(_INTERMEDIATE_KEY)
    _cache.set(_INTERMEDIATE_READY_KEY, False)

    try:
        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()

        # Override num_trials from the dropdown if provided
        if num_trials_input:
            profile.monte_carlo.num_trials = int(num_trials_input)

        live_updates = bool(live_updates_input and "on" in live_updates_input)
        live_interval = 200 if live_updates else 0

        mc_config = profile.monte_carlo
        n_total = mc_config.num_trials

        # ── Progress bridge ───────────────────────────────────────────
        def _progress(current: int, total: int) -> None:
            pct_int  = int(current / total * 100)
            pct_str  = f"{pct_int}%"
            label    = f"Trial {current:,} of {total:,}"
            set_progress((pct_int, pct_str, label, _RUNNING_STYLE, True, _RUN_BTN_DISABLED_STYLE))

        # ── Intermediate results bridge ───────────────────────────────
        retire_yr = profile.retirement_year_self

        def _intermediate(partial_result: MonteCarloResult) -> None:
            data = monte_carlo_result_to_dict(partial_result)
            data["_retire_yr"] = retire_yr
            _cache.set(_INTERMEDIATE_KEY, data)
            _cache.set(_INTERMEDIATE_READY_KEY, True)

        # dcc.Checklist returns a list: ["on"] if checked, [] if not
        adaptive = bool(adaptive_spending_input and "on" in adaptive_spending_input)
        profile.monte_carlo.adaptive_spending = adaptive
        profile.monte_carlo.live_updates = live_updates

        result = run_monte_carlo(
            profile,
            progress_callback=_progress,
            intermediate_callback=_intermediate if live_interval > 0 else None,
            intermediate_interval=live_interval,
            adaptive_spending=adaptive,
        )
        data = monte_carlo_result_to_dict(result)

        # ── CRITICAL: Disable the live-interval BEFORE storing the final
        # result so no stale dash.Patch() can race with mc-results-area's
        # fresh figure.
        _cache.delete(_INTERMEDIATE_KEY)
        _cache.set(_INTERMEDIATE_READY_KEY, False)
        _idle_progress(set_progress)

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

        # Push final "idle" UI state on error too
        _idle_progress(set_progress)

        err_toast = dbc.Toast(
            f"Simulation failed: {str(e)}",
            header="Monte Carlo Error ❌",
            icon="danger",
            duration=7000,
            is_open=True,
        )
        return dash.no_update, err_toast


def register_monte_carlo_callbacks(app: dash.Dash) -> None:

    # Keep the MC tab's controls in the same profile store used by the rest of
    # the app, so navigating away and back preserves the choices.
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Input("mc-input-num-trials", "value"),
        Input("mc-input-adaptive-spending", "value"),
        Input("mc-input-live-updates", "value"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def sync_monte_carlo_controls(
        num_trials_value,
        adaptive_spending_value,
        live_updates_value,
        profile_data,
    ):
        if num_trials_value is None:
            raise dash.exceptions.PreventUpdate

        profile_data = profile_data or {}
        existing_mc = profile_data.get("monte_carlo", {})

        new_num_trials = int(num_trials_value)
        new_adaptive   = bool(adaptive_spending_value and "on" in adaptive_spending_value)
        new_live       = bool(live_updates_value and "on" in live_updates_value)

        # Skip the write if nothing actually changed — prevents spurious
        # profile-store updates (and downstream React loops) at simulation end.
        if (
            existing_mc.get("num_trials") == new_num_trials
            and existing_mc.get("adaptive_spending") == new_adaptive
            and existing_mc.get("live_updates") == new_live
        ):
            raise dash.exceptions.PreventUpdate

        profile_data["monte_carlo"] = {
            **existing_mc,
            "num_trials": new_num_trials,
            "adaptive_spending": new_adaptive,
            "live_updates": new_live,
        }
        return profile_data



    # NOTE: Live fan chart updates have been removed to avoid a React
    # "Maximum update depth exceeded" loop caused by the race between
    # live-update interval ticks and the final results callback replacing
    # the entire mc-results-area (which includes a fresh mc-fan-chart).
    # The progress bar already provides live feedback during simulation.
    # ── 2. Render final results (full build with all charts) ─────────────
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
