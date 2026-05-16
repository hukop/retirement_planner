"""
Income page.

Contains:
1. Active Incomes (Salaries, Pensions, Other) — dynamically rendered from the profile.
2. Social Security Claiming Strategy — a live chart showing the impact of claiming age on benefits.
"""

from __future__ import annotations

from typing import Optional

import dash_bootstrap_components as dbc
from dash import html, dcc
import plotly.graph_objects as go

from engine.models import PlanProfile, IncomeSource
from ui.components import (
    section_card, input_row, select_row,
    two_col, summary_row, dynamic_item, add_button, empty_state, PLOTLY_DARK_TEMPLATE
)

_OWNER_OPTIONS = [
    {"label": "Self", "value": "self"},
    {"label": "Spouse", "value": "spouse"},
    {"label": "Joint", "value": "joint"},
]

_INCOME_TYPE_OPTIONS = [
    {"label": "Salary", "value": "salary"},
    {"label": "Pension", "value": "pension"},
    {"label": "Other", "value": "other"},
]


def _income_source_item(idx: int, inc: dict) -> html.Div:
    """Render a single income source block."""
    pfx = f"income-{idx}"

    # We use pattern-matching dictionaries for IDs if we want to handle dynamic updates,
    # but for initial rendering during layout phase, simple dict form strings work.

    return dynamic_item(
        item_index=idx,
        title=inc.get("name", "New Income Source"),
        subtitle=f"${float(inc.get('annual_amount', 0) or 0):,.0f} / yr",
        delete_id={"type": "btn-delete-income", "index": idx},
        item_id={"type": "income-item", "index": idx},
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Name",
                            input_id={"type": "income-name", "index": idx},
                            input_type="text",
                            value=inc.get("name", ""),
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        select_row(
                            label="Owner",
                            select_id={"type": "income-owner", "index": idx},
                            options=_OWNER_OPTIONS,
                            value=inc.get("owner", "self"),
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        select_row(
                            label="Type",
                            select_id={"type": "income-type", "index": idx},
                            options=_INCOME_TYPE_OPTIONS,
                            value=inc.get("income_type", "salary"),
                        ),
                        xs=12, md=4,
                    ),
                ],
                className="g-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Annual Amount",
                            input_id={"type": "income-amount", "index": idx},
                            value=inc.get("annual_amount", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=3,
                    ),
                    dbc.Col(
                        input_row(
                            label="Annual Raise",
                            input_id={"type": "income-raise", "index": idx},
                            value=inc.get("annual_raise_pct", 0.0),
                            suffix="%",
                            step=0.1,
                            tooltip="Estimated annual increase (e.g. cost of living adjustment)."
                        ),
                        xs=12, md=3,
                    ),
                    dbc.Col(
                        input_row(
                            label="Start Age",
                            input_id={"type": "income-start-age", "index": idx},
                            value=inc.get("start_age", 0),
                            suffix=" yrs",
                            tooltip="Age to start receiving this income. 0 means current age.",
                        ),
                        xs=12, md=3,
                    ),
                    dbc.Col(
                        input_row(
                            label="End Age",
                            input_id={"type": "income-end-age", "index": idx},
                            value=inc.get("end_age", 0),
                            suffix=" yrs",
                            tooltip="Age this income stops. 0 means never stops.",
                        ),
                        xs=12, md=3,
                    ),
                ],
                className="g-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Checklist(
                            options=[{"label": " Taxable Income", "value": "taxable"}],
                            value=["taxable"] if inc.get("taxable", True) else [],
                            id={"type": "income-taxable", "index": idx},
                            inputStyle={"marginRight": "8px"},
                            style={"fontSize": "13px", "color": "var(--text-secondary)", "marginTop": "8px"}
                        )
                    )
                ]
            )
        ]
    )

def _ss_strategy_chart(profile: PlanProfile) -> go.Figure:
    """Build a bar chart showing Social Security benefits at different claiming ages."""
    from engine.social_security import compute_ss_benefit, fra_in_years
    from datetime import date

    current_year = date.today().year

    fig = go.Figure()

    for owner, person, color in [("Self", profile.self_person, "#4a7af7"),
                                 ("Spouse", profile.spouse, "#34d399")]:
        pia = float(person.ss_monthly_benefit or 0)
        if pia <= 0:
            continue

        birth_year = current_year - int(person.current_age or 50)
        fra = fra_in_years(birth_year)

        ages = list(range(62, 71))
        benefits = []

        for age in ages:
            # Temporarily set claim age to test
            copy_person = person
            copy_person.ss_claiming_age = age
            ss = compute_ss_benefit(copy_person, current_year)
            benefits.append(ss.adjusted_monthly * 12)

        fig.add_trace(go.Bar(
            x=ages,
            y=benefits,
            name=owner,
            marker_color=color,
            hovertemplate=f"{owner} Claiming at %{{x}}: $%{{y:,.0f}}/yr<extra></extra>"
        ))

        # Add visual indicator for FRA
        fig.add_vline(x=fra, line_width=1, line_dash="dash", line_color=color, opacity=0.5)

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Annual Benefit by Claiming Age", "x": 0.02, "xanchor": "left"},
        "height": 300,
        "barmode": "group",
        "margin": {"l": 50, "r": 20, "t": 50, "b": 40},
        "legend": {"orientation": "h", "y": -0.15, "x": 0},
    })
    fig.update_layout(**layout)
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    fig.update_xaxes(title_text="Claiming Age")
    return fig

def layout(profile_data: Optional[dict] = None) -> html.Div:
    """Render the income page."""
    profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()

    # ── Active Incomes List ──────────────────────────────────────────────
    incomes_list = []
    if not profile.incomes:
        incomes_list.append(empty_state("No income sources defined. Click '+ Add Income' below.", "💼"))
    else:
        for idx, inc in enumerate(profile.incomes):
            incomes_list.append(_income_source_item(idx, inc.__dict__))

    income_section = section_card(
        title="💼  Income Sources",
        subtitle="Track your salaries, pensions, and review your Social Security strategy.",
        children=[
            html.Div(incomes_list, id="income-sources-container"),
            add_button("Add Income Source", btn_id="btn-add-income")
        ]
    )

    # ── Social Security Analysis ─────────────────────────────────────────
    ss_chart = dcc.Graph(
        id="ss-strategy-chart",
        figure=_ss_strategy_chart(profile),
        config={"displayModeBar": False, "responsive": True},
    )

    ss_section = section_card(
        title="US Social Security Claiming Analysis",
        children=[
            html.P("Compare the impact of different claiming ages on your annual Social Security benefits. Claiming earlier permanently reduces the annual payout, while delaying increases it.",
                   style={"fontSize": "13px", "color": "var(--text-secondary)", "marginBottom": "20px"}),
            ss_chart
        ]
    )

    # ── Summary Strip ────────────────────────────────────────────────────
    total_active_income = sum(float(i.annual_amount or 0) for i in profile.incomes)

    strip = summary_row([
        ("Total Active Income", f"${total_active_income:,.0f}/yr", "blue"),
        ("Income Sources", f"{len(profile.incomes)} sources", "purple"),
    ])

    return html.Div(
        [
            two_col(income_section, ss_section, left_width=7),
            strip,
            html.Div(
                html.Button(
                    "💾  Save Updates",
                    id="income-save-btn",
                    className="btn-primary-custom",
                    n_clicks=0,
                ),
                style={"marginTop": "20px"},
            ),
        ]
    )
