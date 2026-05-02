"""
Dynamic Add/Remove Callbacks.

Handles appending new empty blocks to the dynamic lists
and removing items when the trash icon is clicked.
"""

import dash
from dash import Input, Output, State, ALL, Patch
import uuid

def register_dynamic_callbacks(app: dash.Dash):
    # We use a trick: importing the item renderers from the layout pages directly
    from ui.pages.income import _income_source_item
    from ui.pages.expenses import _expense_item, _one_time_expense_item
    from ui.pages.investments import _investment_item
    from ui.pages.real_estate import _property_item

    # ── Map configs ──
    _configs = [
        ("btn-add-income", "income-sources-container", _income_source_item, {"name": "New Income", "annual_amount": 0}),
        ("btn-add-expense", "recurring-expenses-container", _expense_item, {"monthly_amount": 0}),
        ("btn-add-otex", "onetime-expenses-container", _one_time_expense_item, {"amount": 0, "year": 2030}),
        ("btn-add-account", "accounts-container", _investment_item, {"name": "New Account", "balance": 0}),
        ("btn-add-property", "properties-container", _property_item, {"name": "New Property", "rental_inflation_rate_pct": 3.0}),
    ]

    # Register Add Callbacks
    for btn_id, container_id, render_func, default_dict in _configs:
        @app.callback(
            Output(container_id, "children", allow_duplicate=True),
            Input(btn_id, "n_clicks"),
            prevent_initial_call=True
        )
        def _add_item(n_clicks, func=render_func, defaults=default_dict):
            # Generate a random high index to avoid conflicts
            idx = n_clicks + 1000
            patched = Patch()
            patched.append(func(idx, defaults))
            return patched

    # ── Soft Delete via Clientside CSS ──
    # Dash Patching cannot dynamically delete indexed elements universally.
    # Therefore, when a user clicks 'Remove', we fire JS to hide the wrapper natively.
    # Our engine state-sync callback will look for `style={'display': 'none'}` and drop it.

    _delete_configs = [
        ("btn-delete-income", "income-item"),
        ("btn-delete-expense", "expense-item"),
        ("btn-delete-otex", "otex-item"),
        ("btn-delete-account", "account-item"),
        ("btn-delete-property", "property-item")
    ]

    for btn_type, wrapper_type in _delete_configs:
        app.clientside_callback(
            """
            function(n_clicks) {
                if (n_clicks > 0) {
                    return {"display": "none"};
                }
                return window.dash_clientside.no_update;
            }
            """,
            Output({"type": wrapper_type, "index": dash.MATCH}, "style"),
            Input({"type": btn_type, "index": dash.MATCH}, "n_clicks"),
            prevent_initial_call=True
        )

    # ── Real Estate: toggle rental cashflow fields by property type ──
    app.clientside_callback(
        """
        function(prop_type) {
            return {"display": prop_type === "rental" ? "block" : "none"};
        }
        """,
        Output({"type": "prop-rental-group", "index": dash.MATCH}, "style"),
        Input({"type": "prop-type", "index": dash.MATCH}, "value"),
        prevent_initial_call=False
    )

    # ── Real Estate: toggle mortgage details by has-mortgage radio ──
    app.clientside_callback(
        """
        function(has_mortgage) {
            return {"display": has_mortgage === "yes" ? "block" : "none"};
        }
        """,
        Output({"type": "prop-mortgage-group", "index": dash.MATCH}, "style"),
        Input({"type": "prop-has-mortgage",    "index": dash.MATCH}, "value"),
        prevent_initial_call=False
    )
