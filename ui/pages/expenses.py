"""
Expenses page.

Contains:
1. Recurring Expenses — monthly base amount and retirement scale %.
2. One-Time Expenses — list of single large expenses tied to a target year.
"""

from __future__ import annotations

from typing import Optional

import dash_bootstrap_components as dbc
from dash import html, dcc
import plotly.graph_objects as go

from engine.models import PlanProfile
from ui.components import (
    section_card, page_header, input_row, select_row, slider_row,
    two_col, summary_row, dynamic_item, add_button, empty_state, PLOTLY_DARK_TEMPLATE
)

_CATEGORY_OPTIONS = [
    {"label": "Housing",       "value": "housing"},
    {"label": "Food",          "value": "food"},
    {"label": "Transport",     "value": "transport"},
    {"label": "Healthcare",    "value": "healthcare"},
    {"label": "Discretionary", "value": "discretionary"},
    {"label": "Other",         "value": "other"},
]


def _expense_item(idx: int, exp: dict) -> html.Div:
    """Render a single recurring expense block."""
    cat_label = next((c["label"] for c in _CATEGORY_OPTIONS if c["value"] == exp.get("category", "other")), "Other")
    # Use the stored name if it differs from the category slug; otherwise show the category label
    display_name = exp.get("name") or cat_label

    return dynamic_item(
        item_index=idx,
        title=display_name,
        delete_id={"type": "btn-delete-expense", "index": idx},
        item_id={"type": "expense-item", "index": idx},
        children=[
            input_row(
                label="Name",
                input_id={"type": "expense-name", "index": idx},
                input_type="text",
                value=exp.get("name", ""),
                placeholder=f"{cat_label} (optional label)",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        select_row(
                            label="Category",
                            select_id={"type": "expense-category", "index": idx},
                            options=_CATEGORY_OPTIONS,
                            value=exp.get("category", "other"),
                        ),
                        xs=12, md=6,
                    ),
                    dbc.Col(
                        input_row(
                            label="Monthly Amount",
                            input_id={"type": "expense-amount", "index": idx},
                            value=exp.get("monthly_amount", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=6,
                    ),
                ],
                className="g-3"
            ),
            slider_row(
                label="Retirement Scaling",
                slider_id={"type": "expense-retire-pct", "index": idx},
                min_val=0, max_val=200,
                value=exp.get("retirement_pct", 100),
                step=5,
                marks={v: f"{v}%" for v in [0, 50, 100, 150, 200]},
                tooltip="How much of this expense remains in retirement? E.g. set to 0% if mortgage will be paid off.",
                suffix="%"
            ),
            dcc.Checklist(
                options=[{"label": " Adjust for Inflation", "value": "inflation_adjusted"}],
                value=["inflation_adjusted"] if exp.get("inflation_adjusted", True) else [],
                id={"type": "expense-inflation", "index": idx},
                inputStyle={"marginRight": "8px"},
                style={"fontSize": "13px", "color": "var(--text-secondary)", "marginTop": "4px"}
            )
        ]
    )

def _one_time_expense_item(idx: int, otex: dict) -> html.Div:
    """Render a single one-time expense block."""
    return dynamic_item(
        item_index=idx,
        title=otex.get("name", "New Expense"),
        delete_id={"type": "btn-delete-otex", "index": idx},
        item_id={"type": "otex-item", "index": idx},
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Name",
                            input_id={"type": "otex-name", "index": idx},
                            input_type="text",
                            value=otex.get("name", ""),
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        input_row(
                            label="Amount",
                            input_id={"type": "otex-amount", "index": idx},
                            value=otex.get("amount", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        input_row(
                            label="Target Year",
                            input_id={"type": "otex-year", "index": idx},
                            value=otex.get("year", 2030),
                            min_val=2020, max_val=2100,
                        ),
                        xs=12, md=4,
                    ),
                ],
                className="g-3"
            ),
            dcc.Checklist(
                options=[{"label": " Adjust for Inflation", "value": "inflation_adjusted"}],
                value=["inflation_adjusted"] if otex.get("inflation_adjusted", True) else [],
                id={"type": "otex-inflation", "index": idx},
                inputStyle={"marginRight": "8px"},
                style={"fontSize": "13px", "color": "var(--text-secondary)", "marginTop": "4px"}
            )
        ]
    )

def _expense_pie_chart(profile: PlanProfile) -> go.Figure:
    """Pie chart to show the breakdown of current recurring expenses."""
    labels = []
    values = []
    
    # Define a set of colors mapping to categories for consistency
    cat_colors = {
        "housing": "#4a7af7",      # blue
        "food": "#34d399",         # green
        "transport": "#fbbf24",    # amber
        "healthcare": "#f87171",   # red
        "discretionary": "#a78bfa",# purple
        "other": "#2dd4bf"         # teal
    }
    
    colors = []
    
    for exp in profile.expenses:
        amt = float(exp.monthly_amount or 0)
        if amt > 0:
            cat_str = next((c["label"] for c in _CATEGORY_OPTIONS if c["value"] == exp.category), "Other")
            labels.append(cat_str)
            values.append(amt)
            colors.append(cat_colors.get(exp.category, "#94a3b8"))
            
    if not values:
        labels, values, colors = ["No Expenses"], [1], ["#1c2540"]
        
    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.6,
        marker=dict(colors=colors, line=dict(color="var(--bg-card)", width=2)),
        textinfo="label+percent",
        textfont=dict(size=11, family="Inter"),
        hovertemplate="%{label}: $%{value:,.0f}/mo<extra></extra>",
    ))
    
    total = sum(values) if values[0] != 1 or labels[0] != "No Expenses" else 0
    fig.add_annotation(
        text=f"${total:,.0f}<br><span style='font-size:10px'>/ month</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=15, color="var(--text-primary)", family="Inter"),
        align="center",
    )
    
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Current Breakdown", "x": 0.02, "xanchor": "left"},
        "height": 260,
        "margin": {"l": 10, "r": 10, "t": 40, "b": 10},
        "showlegend": False,
    })
    fig.update_layout(**layout)
    return fig

def layout(profile_data: Optional[dict] = None) -> html.Div:
    """Render the expenses page."""
    profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
    
    # ── Recurring Expenses ───────────────────────────────────────────────
    expenses_list = []
    if not profile.expenses:
        expenses_list.append(empty_state("No recurring expenses defined.", "🛒"))
    else:
        for idx, exp in enumerate(profile.expenses):
            expenses_list.append(_expense_item(idx, exp.__dict__))

    recurring_section = section_card(
        title="🛒  Recurring Expenses",
        children=[
            html.Div(expenses_list, id="recurring-expenses-container"),
            add_button("Add Recurring Expense", btn_id="btn-add-expense")
        ]
    )
    
    # ── One-Time Expenses ────────────────────────────────────────────────
    otex_list = []
    if not profile.one_time_expenses:
        otex_list.append(empty_state("No one-time expenses planned.", "🎯"))
    else:
        for idx, otex in enumerate(profile.one_time_expenses):
            otex_list.append(_one_time_expense_item(idx, otex.__dict__))
            
    onetime_section = section_card(
        title="🎯  One-Time Expenses",
        children=[
            html.Div(otex_list, id="onetime-expenses-container"),
            add_button("Add One-Time Expense", btn_id="btn-add-otex")
        ]
    )

    # ── Pie Chart Section ────────────────────────────────────────────────
    chart_section = section_card(
        title="📊  Expense Overview",
        children=[
            dcc.Graph(
                id="expense-breakdown-chart",
                figure=_expense_pie_chart(profile),
                config={"displayModeBar": False, "responsive": True},
            )
        ]
    )

    # ── Summary Strip ────────────────────────────────────────────────────
    current_annual = sum((float(e.monthly_amount or 0)) * 12 for e in profile.expenses)
    
    # Estimate retirement annual cost (roughly, un-inflated)
    retire_annual = sum((float(e.monthly_amount or 0)) * 12 * (float(e.retirement_pct or 100) / 100) for e in profile.expenses)
    
    strip = summary_row([
        ("Current Annual Spend", f"${current_annual:,.0f}/yr", "amber"),
        ("Retirement Annual Spend (Est)", f"${retire_annual:,.0f}/yr", "blue"),
        ("One-Time Events", f"{len(profile.one_time_expenses)} planned", "purple"),
    ])

    return html.Div(
        [
            page_header(
                "Expenses",
                subtitle="Map out your current spending and estimate how it changes during retirement.",
                icon="🛒",
            ),
            two_col(recurring_section, html.Div([chart_section, onetime_section]), left_width=7),
            strip,
            html.Div(
                html.Button(
                    "💾  Save Updates",
                    id="expenses-save-btn",
                    className="btn-primary-custom",
                    n_clicks=0,
                ),
                style={"marginTop": "20px"},
            ),
        ]
    )
