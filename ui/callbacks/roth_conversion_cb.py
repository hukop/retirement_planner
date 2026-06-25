"""
Roth Conversion callbacks.
"""

from __future__ import annotations

import dash
from dash import Input, Output, State, html
import dash_bootstrap_components as dbc

from engine.models import PlanProfile
from engine.roth_conversion import RothConversionConfig, run_roth_conversion_analysis, roth_conversion_result_to_dict
from ui.serialization import sanitize_for_dash_json

def register_roth_conversion_callbacks(app: dash.Dash):
    
    # ── 1. Run Analysis ──────────────────────────────────────────────────
    @app.callback(
        Output("roth-conversion-store", "data"),
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-run-roth", "n_clicks"),
        State("profile-store", "data"),
        State("roth-input-amount", "value"),
        State("roth-input-start-year", "value"),
        State("roth-input-end-year", "value"),
        prevent_initial_call=True
    )
    def run_analysis(n_clicks, profile_data, amount, start_yr, end_yr):
        if not dash.ctx.triggered_id:
            raise dash.exceptions.PreventUpdate
            
        try:
            profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
            
            config = RothConversionConfig(
                annual_amount=float(amount or 0),
                start_year=int(start_yr or 0),
                end_year=int(end_yr or 0),
                source_account_types=["trad_ira", "401k"]
            )
            
            result = run_roth_conversion_analysis(profile, config)
            data = sanitize_for_dash_json(roth_conversion_result_to_dict(result))
            
            if result.total_converted <= 0:
                return dash.no_update, dbc.Toast(
                    "No funds converted. Ensure you have a non-zero balance in a Traditional IRA or 401(k), and that the conversion amount is greater than 0.",
                    header="Zero Conversion",
                    icon="warning",
                    duration=6000,
                    is_open=True,
                )
            
            toast = dbc.Toast(
                "Roth conversion analysis completed.",
                header="Analysis Success",
                icon="success",
                duration=4000,
                is_open=True,
            )
            
            return data, toast
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_toast = dbc.Toast(
                f"Analysis failed: {str(e)}",
                header="Engine Error",
                icon="danger",
                duration=6000,
                is_open=True,
            )
            return dash.no_update, err_toast

    # ── 2. Render Results ────────────────────────────────────────────────
    @app.callback(
        Output("roth-results-area", "children"),
        Input("roth-conversion-store", "data"),
        State("profile-store", "data"),
        prevent_initial_call=True
    )
    def render_results(roth_data, profile_data):
        if not roth_data:
            from ui.pages.roth_conversion import _empty_state
            return _empty_state()

        from engine.models import PlanProfile
        from engine.roth_conversion import RothConversionResult
        from ui.pages.roth_conversion import _results_section

        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
        retire_yr = profile.retirement_year_self
        
        result = RothConversionResult(**roth_data)
        return _results_section(result, retire_yr)
