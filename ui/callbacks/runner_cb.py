"""
Runner callbacks.

Handles global app actions:
- Running projections
- Saving/loading plans to disk
"""
import json
import os
from pathlib import Path

import dash
from dash import Input, Output, State, html
import dash_bootstrap_components as dbc
from datetime import datetime

from engine.models import PlanProfile
from engine.projections import run_projection

APP_DATA_DIR = Path("data/profiles")
DEFAULT_PLAN_FILE = APP_DATA_DIR / "my_plan.json"

def register_runner_callbacks(app: dash.Dash):
    
    # Ensure data dir exists
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # ── Run Projections ──────────────────────────────────────────────────
    @app.callback(
        Output("projection-store", "data"),
        Output("url", "pathname", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-run-projections", "n_clicks"),
        Input("dash-run-btn", "n_clicks"),  # On dashboard
        State("profile-store", "data"),
        prevent_initial_call=True
    )
    def run_engine(n_clicks_top, n_clicks_dash, profile_data):
        if not dash.ctx.triggered_id:
            raise dash.exceptions.PreventUpdate
            
        try:
            profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
            _, annual_df = run_projection(profile)
            
            toast = dbc.Toast(
                "Projections calculated successfully. All charts updated.",
                header="Engine Success",
                icon="success",
                duration=4000,
                is_open=True,
            )
            
            # Save the records back to the store and navigate to projections page
            records = annual_df.to_dict("records")
            return records, "/projections", toast
            
        except Exception as e:
            err_toast = dbc.Toast(
                f"Calculation failed: {str(e)}",
                header="Engine Error",
                icon="danger",
                duration=6000,
                is_open=True,
            )
            return dash.no_update, dash.no_update, err_toast

    # ── Save Plan ────────────────────────────────────────────────────────
    @app.callback(
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-save-plan", "n_clicks"),
        State("profile-store", "data"),
        prevent_initial_call=True
    )
    def save_plan(n_clicks, profile_data):
        if not profile_data:
            raise dash.exceptions.PreventUpdate
            
        try:
            with open(DEFAULT_PLAN_FILE, "w") as f:
                json.dump(profile_data, f, indent=2)
                
            return dbc.Toast(
                f"Plan saved to {DEFAULT_PLAN_FILE.name}.",
                header="Save Success",
                icon="success",
                duration=3000,
                is_open=True,
            )
        except Exception as e:
            return dbc.Toast(f"Failed to save: {str(e)}", header="Error", icon="danger", duration=4000, is_open=True)

    # ── Load Plan ────────────────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("url", "pathname", allow_duplicate=True), # Force reload by pushing to dash
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-load-plan", "n_clicks"),
        prevent_initial_call=True
    )
    def load_plan(n_clicks):
        if not DEFAULT_PLAN_FILE.exists():
            return dash.no_update, dash.no_update, dbc.Toast(
                "No saved plan found.", header="Load Plan", icon="warning", duration=3000, is_open=True
            )
            
        try:
            with open(DEFAULT_PLAN_FILE, "r") as f:
                data = json.load(f)
                
            return data, "/", dbc.Toast(
                "Plan loaded successfully.", header="Load Success", icon="success", duration=3000, is_open=True
            )
        except Exception as e:
            return dash.no_update, dash.no_update, dbc.Toast(
                f"Failed to load: {str(e)}", header="Error", icon="danger", duration=4000, is_open=True
            )
