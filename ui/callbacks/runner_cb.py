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
        State("profile-store", "data"),
        prevent_initial_call=True
    )
    def run_engine(n_clicks_top, profile_data):
        if not dash.ctx.triggered_id:
            raise dash.exceptions.PreventUpdate
            
        try:
            profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
            
            # Basic validation check to ensure run_projection has what it needs
            if not profile.self_person or profile.self_person.current_age is None:
                 raise ValueError("Incomplete Profile: Self current age is required.")
                 
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
        Output("download-plan", "data"),
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-save-plan", "n_clicks"),
        State("profile-store", "data"),
        prevent_initial_call=True
    )
    def save_plan(n_clicks, profile_data):
        if not profile_data:
            raise dash.exceptions.PreventUpdate
            
        try:
            # We use dcc.send_string to push the JSON natively to the browser's download manager.
            default_filename = profile_data.get("plan_name", "my_retirement_plan").replace(" ", "_").lower() + ".json"
            
            json_str = json.dumps(profile_data, indent=2)
            
            return dict(content=json_str, filename=default_filename), dbc.Toast(
                "Plan downloaded successfully.",
                header="Save Success",
                icon="success",
                duration=3000,
                is_open=True,
            )
        except Exception as e:
            return dash.no_update, dbc.Toast(f"Failed to save: {str(e)}", header="Error", icon="danger", duration=4000, is_open=True)

    # ── Load Plan ────────────────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("url", "pathname", allow_duplicate=True), # Force reload by pushing to dash
        Output("toast-container", "children", allow_duplicate=True),
        Input("upload-plan", "contents"),
        State("upload-plan", "filename"),
        prevent_initial_call=True
    )
    def load_plan(contents, filename):
        if contents is None:
            raise dash.exceptions.PreventUpdate
            
        try:
            import base64
            # contents look like "data:application/json;base64,eyJwbGFu..."
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string)
            data = json.loads(decoded.decode('utf-8'))
                
            return data, "/", dbc.Toast(
                f"Plan '{filename}' loaded successfully.", header="Load Success", icon="success", duration=3000, is_open=True
            )
        except Exception as e:
            return dash.no_update, dash.no_update, dbc.Toast(
                f"Failed to load: {str(e)}", header="Error", icon="danger", duration=4000, is_open=True
            )
