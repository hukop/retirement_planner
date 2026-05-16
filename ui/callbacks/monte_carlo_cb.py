"""
Monte Carlo callbacks.

Uses Dash's background callback (background=True) so the simulation runs in
a background thread while the UI stays responsive and shows a live progress bar.

Progress is reported by passing set_progress() to run_monte_carlo() every 25
trials, which updates:
  - mc-progress-bar    → Bootstrap Progress value (0-100)
  - mc-progress-pct    → "42%" text label
  - mc-progress-label  → "Trial 420 of 1,000"
"""

from __future__ import annotations

import copy

import dash
from dash import Input, Output, State, html
import dash_bootstrap_components as dbc

from engine.models import PlanProfile, MonteCarloConfig
from engine.monte_carlo import run_monte_carlo, monte_carlo_result_to_dict


# ── Run Simulation (background callback with live progress) ───────────
@dash.callback(
    output=[
        Output("monte-carlo-store", "data"),
        Output("toast-container", "children", allow_duplicate=True),
    ],
    inputs=dict(
        n_clicks=Input("btn-run-monte-carlo", "n_clicks"),
        profile_data=State("profile-store",  "data"),
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
            {"width": "220px", "fontSize": "14px", "fontWeight": "700", "opacity": "0.5"},
            {"width": "220px", "fontSize": "14px", "fontWeight": "700", "opacity": "1.0"}),
    ],
    background=True,
    prevent_initial_call=True,
)
def run_simulation(
    set_progress,
    n_clicks,
    profile_data,
):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    # ── Initial progress reset ────────────────────────────────────────
    set_progress((0, "0%", "Starting…"))

    try:
        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
        
        # Assumptions are already in profile.monte_carlo (synced from Profile page)
        mc_config = profile.monte_carlo
        n_total = mc_config.num_trials

        # ── Progress bridge ───────────────────────────────────────────
        def _progress(current: int, total: int) -> None:
            pct_int  = int(current / total * 100)
            pct_str  = f"{pct_int}%"
            label    = f"Trial {current:,} of {total:,}"
            set_progress((pct_int, pct_str, label))

        result = run_monte_carlo(profile, progress_callback=_progress)
        data   = monte_carlo_result_to_dict(result)

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
        err_toast = dbc.Toast(
            f"Simulation failed: {str(e)}",
            header="Monte Carlo Error ❌",
            icon="danger",
            duration=7000,
            is_open=True,
        )
        return dash.no_update, err_toast


def register_monte_carlo_callbacks(app: dash.Dash) -> None:
    # ── Update results area when store changes ────────────────────────────
    @app.callback(
        Output("mc-results-area", "children"),
        Input("monte-carlo-store", "data"),
        State("profile-store",    "data"),
    )
    def update_results_area(mc_data, profile_data):
        if not mc_data:
            raise dash.exceptions.PreventUpdate

        from engine.models import PlanProfile
        from engine.monte_carlo import MonteCarloResult
        from ui.pages.monte_carlo import _results_section

        profile   = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
        retire_yr = profile.retirement_year_self
        result    = MonteCarloResult(**mc_data)
        return _results_section(result, retire_yr)
