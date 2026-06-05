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


def _default_plan_filename(profile_data: dict, plan_file_data: dict | None = None) -> str:
    """Return a safe default filename for Save As."""
    current_filename = (plan_file_data or {}).get("filename")
    if current_filename:
        return Path(current_filename).name

    plan_name = str(profile_data.get("plan_name") or "my_retirement_plan").strip()
    safe_name = "".join(
        ch.lower() if ch.isalnum() else "_"
        for ch in plan_name
    ).strip("_")
    return f"{safe_name or 'my_retirement_plan'}.json"


def _select_save_path(profile_data: dict, plan_file_data: dict | None = None) -> str:
    import tkinter as tk
    from tkinter import filedialog

    known_path = (plan_file_data or {}).get("path")
    initialdir = Path(known_path).parent if known_path else APP_DATA_DIR

    root = tk.Tk()
    root.attributes("-topmost", True)
    root.withdraw()
    try:
        return filedialog.asksaveasfilename(
            parent=root,
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            initialdir=str(initialdir.resolve()),
            initialfile=_default_plan_filename(profile_data, plan_file_data),
            title="Save Retirement Plan As",
        )
    finally:
        root.destroy()


def _select_load_path() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.attributes("-topmost", True)
    root.withdraw()
    try:
        return filedialog.askopenfilename(
            parent=root,
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            initialdir=str(APP_DATA_DIR.resolve()),
            title="Load Retirement Plan",
        )
    finally:
        root.destroy()


def _plan_file_data(file_path: str | Path) -> dict:
    path = Path(file_path).resolve()
    return {
        "path": str(path),
        "filename": path.name,
    }


def _write_plan_file(profile_data: dict, file_path: str | Path) -> dict:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile_data, f, indent=2)

    return _plan_file_data(path)


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

    # ── Save Plan / Save Plan As ────────────────────────────────────────
    @app.callback(
        Output("toast-container", "children", allow_duplicate=True),
        Output("plan-file-store", "data", allow_duplicate=True),
        Input("btn-save-plan", "n_clicks"),
        Input("btn-save-plan-as", "n_clicks"),
        State("profile-store", "data"),
        State("plan-file-store", "data"),
        prevent_initial_call=True
    )
    def save_plan(n_save, n_save_as, profile_data, plan_file_data):
        trigger = dash.ctx.triggered_id
        if not profile_data or trigger not in ("btn-save-plan", "btn-save-plan-as"):
            raise dash.exceptions.PreventUpdate

        try:
            if trigger == "btn-save-plan":
                file_path = (plan_file_data or {}).get("path")
                if not file_path:
                    return dbc.Toast(
                        "No current plan file is selected. Use Save Plan as ... first.",
                        header="No Current File",
                        icon="warning",
                        duration=4000,
                        is_open=True,
                    ), dash.no_update

            else:
                file_path = _select_save_path(profile_data, plan_file_data)
                if not file_path:
                    return dbc.Toast(
                        "Save cancelled.",
                        header="Cancelled",
                        icon="warning",
                        duration=3000,
                        is_open=True,
                    ), dash.no_update

            saved_file_data = _write_plan_file(profile_data, file_path)

            return dbc.Toast(
                f"Plan saved to: {saved_file_data['path']}",
                header="Save Success",
                icon="success",
                duration=4000,
                is_open=True,
            ), saved_file_data
        except Exception as e:
            return dbc.Toast(
                f"Failed to save: {str(e)}",
                header="Error",
                icon="danger",
                duration=4000,
                is_open=True,
            ), dash.no_update

    # ── Load Plan ────────────────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("plan-file-store", "data", allow_duplicate=True),
        Output("url", "pathname", allow_duplicate=True), # Force reload by pushing to dash
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-load-plan", "n_clicks"),
        Input("upload-plan", "contents"),
        State("upload-plan", "filename"),
        prevent_initial_call=True
    )
    def load_plan(n_clicks, contents, filename):
        trigger = dash.ctx.triggered_id
        if trigger not in ("btn-load-plan", "upload-plan"):
            raise dash.exceptions.PreventUpdate

        try:
            if trigger == "btn-load-plan":
                file_path = _select_load_path()
                if not file_path:
                    return dash.no_update, dash.no_update, dash.no_update, dbc.Toast(
                        "Load cancelled.",
                        header="Cancelled",
                        icon="warning",
                        duration=3000,
                        is_open=True,
                    )

                if not str(file_path).lower().endswith(".json"):
                    raise ValueError("Only JSON files are supported")

                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                plan_file_data = _plan_file_data(file_path)
                return data, plan_file_data, "/", dbc.Toast(
                    f"Plan '{plan_file_data['filename']}' loaded successfully.",
                    header="Load Success",
                    icon="success",
                    duration=3000,
                    is_open=True,
                )

            if contents is None:
                raise dash.exceptions.PreventUpdate

            filename = filename or "uploaded_plan.json"

            # Validate file extension
            if not filename.lower().endswith('.json'):
                raise ValueError("Only JSON files are supported")

            import base64
            # contents look like "data:application/json;base64,eyJwbGFu..."
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string)
            data = json.loads(decoded.decode('utf-8'))

            return data, {"path": None, "filename": filename}, "/", dbc.Toast(
                f"Plan '{filename}' loaded successfully.", header="Load Success", icon="success", duration=3000, is_open=True
            )
        except Exception as e:
            return dash.no_update, dash.no_update, dash.no_update, dbc.Toast(
                f"Failed to load: {str(e)}", header="Error", icon="danger", duration=4000, is_open=True
            )
