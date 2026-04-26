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
        ("btn-add-property", "properties-container", _property_item, {"name": "New Property"}),
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

    # Delete Callbacks (Dash Patch doesn't easily support deleting by index from patterned inputs,
    # so typically we just hide them via CSS, or we force a page reload from the profile-store.
    # For MVP, we use a simple generic pattern to clear the contents visually, 
    # but a true sync requires the State callback.)
