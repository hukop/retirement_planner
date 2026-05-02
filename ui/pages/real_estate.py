"""
Real Estate page.

Contains:
1. Properties dynamic list (Primary, Rental) 
2. Summary strip charting total real estate equity
"""

from __future__ import annotations

from typing import Optional

import dash_bootstrap_components as dbc
from dash import html, dcc
import plotly.graph_objects as go

from engine.models import PlanProfile
from ui.components import (
    section_card, page_header, input_row, select_row,
    two_col, summary_row, dynamic_item, add_button, empty_state, PLOTLY_DARK_TEMPLATE
)

_PROPERTY_TYPES = [
    {"label": "Primary Residence", "value": "primary"},
    {"label": "Rental Property", "value": "rental"},
]

def _property_item(idx: int, prop: dict) -> html.Div:
    """Render a single property block."""
    prop_type = prop.get("property_type", "primary")
    
    return dynamic_item(
        item_index=idx,
        title=prop.get("name", "New Property"),
        delete_id={"type": "btn-delete-property", "index": idx},
        item_id={"type": "property-item", "index": idx},
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Name",
                            input_id={"type": "prop-name", "index": idx},
                            input_type="text",
                            value=prop.get("name", ""),
                        ),
                        xs=12, md=6,
                    ),
                    dbc.Col(
                        select_row(
                            label="Type",
                            select_id={"type": "prop-type", "index": idx},
                            options=_PROPERTY_TYPES,
                            value=prop_type,
                        ),
                        xs=12, md=6,
                    ),
                ],
                className="g-3"
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Current Value",
                            input_id={"type": "prop-value", "index": idx},
                            value=prop.get("current_value", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=6,
                    ),
                    dbc.Col(
                        input_row(
                            label="Annual Appreciation",
                            input_id={"type": "prop-appreciation", "index": idx},
                            value=prop.get("appreciation_rate_pct", 3.0),
                            suffix="%",
                            step=0.1,
                        ),
                        xs=12, md=6,
                    ),
                ],
                className="g-3"
            ),
            
            # Form header for Mortgage
            html.Div(
                "Mortgage Details", 
                style={"fontSize": "11px", "fontWeight": "600", "letterSpacing": "0.8px", 
                       "textTransform": "uppercase", "color": "var(--text-muted)", "margin": "16px 0 8px"}
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Loan Balance",
                            input_id={"type": "prop-mortgage-bal", "index": idx},
                            value=prop.get("mortgage_balance", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=3,
                    ),
                    dbc.Col(
                        input_row(
                            label="Interest Rate",
                            input_id={"type": "prop-mortgage-rate", "index": idx},
                            value=prop.get("mortgage_rate_pct", 3.0),
                            suffix="%",
                            step=0.125,
                        ),
                        xs=12, md=3,
                    ),
                    dbc.Col(
                        input_row(
                            label="Years Remaining",
                            input_id={"type": "prop-mortgage-years", "index": idx},
                            value=prop.get("years_remaining", 30),
                            suffix=" yrs",
                        ),
                        xs=12, md=3,
                    ),
                    dbc.Col(
                        input_row(
                            label="Monthly Payment",
                            input_id={"type": "prop-mortgage-payment", "index": idx},
                            value=prop.get("monthly_payment", 0),
                            prefix="$",
                            tooltip="Principal + Interest only."
                        ),
                        xs=12, md=3,
                    ),
                ],
                className="g-3"
            ),
            
            # Conditionally render rental fields if we have a rental Property (visually grouped)
            html.Div(
                [
                    html.Div(
                        "Rental Cashflow", 
                        style={"fontSize": "11px", "fontWeight": "600", "letterSpacing": "0.8px", 
                               "textTransform": "uppercase", "color": "var(--text-muted)", "margin": "16px 0 8px"}
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                input_row(
                                    label="Gross Monthly Rent",
                                    input_id={"type": "prop-rent-inc", "index": idx},
                                    value=prop.get("monthly_rental_income", 0),
                                    prefix="$",
                                ),
                                xs=12, md=6,
                            ),
                            dbc.Col(
                                input_row(
                                    label="Monthly Operating Expenses",
                                    input_id={"type": "prop-rent-exp", "index": idx},
                                    value=prop.get("monthly_expenses", 0),
                                    prefix="$",
                                    tooltip="Property tax, insurance, maintenance, HOA. Exclude mortgage."
                                ),
                                xs=12, md=6,
                            ),
                        ],
                        className="g-3"
                    )
                ], 
                style={"display": "block"} if prop_type == "rental" else {"display": "none"},
                id={"type": "prop-rental-group", "index": idx}
            )
        ]
    )

def _equity_bubble_chart(profile: PlanProfile) -> go.Figure:
    """A bubble chart showing Property Value vs Mortgage Balance, bubble size is Equity."""
    
    names = []
    x_vals = []
    y_vals = []
    equity_sizes = []
    colors = []
    
    for prop in profile.properties:
        pv = float(prop.current_value or 0)
        mb = float(prop.mortgage_balance or 0)
        equity = max(0, pv - mb)
        names.append(prop.name)
        x_vals.append(pv)
        y_vals.append(mb)
        equity_sizes.append(equity)
        colors.append("#4a7af7" if prop.property_type == "primary" else "#34d399")
        
    if not profile.properties:
        x_vals, y_vals, equity_sizes = [0], [0], [0]
        
    fig = go.Figure(go.Scatter(
        x=x_vals,
        y=y_vals,
        mode="markers",
        marker=dict(
            size=equity_sizes,
            sizemode="area",
            sizeref=2.*max(equity_sizes + [1])/(40.**2),
            sizemin=4,
            color=colors,
            opacity=0.7,
            line=dict(color="var(--bg-card)", width=1)
        ),
        text=names,
        hovertemplate="<b>%{text}</b><br>Value: $%{x:,.0f}<br>Mortgage: $%{y:,.0f}<br>Equity: %{marker.size}<extra></extra>"
    ))
    
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Equity Distribution", "x": 0.02, "xanchor": "left"},
        "height": 280,
        "margin": {"l": 50, "r": 20, "t": 50, "b": 40},
        "xaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["xaxis"], "title": "Property Value ($)"},
        "yaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["yaxis"], "title": "Mortgage Balance ($)", "autorange": "reversed"}, # Reversed so 0 mortgage is at the top
    })
    
    fig.update_layout(**layout)
    return fig


def layout(profile_data: Optional[dict] = None) -> html.Div:
    """Render the real estate page."""
    profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
    
    # ── Property List ───────────────────────────────────────────────────
    props_list = []
    if not profile.properties:
        props_list.append(empty_state("No real estate defined.", "🏠"))
    else:
        for idx, prop in enumerate(profile.properties):
            props_list.append(_property_item(idx, prop.__dict__))

    props_section = section_card(
        title="🏠  Real Estate Portfolio",
        children=[
            html.Div(props_list, id="properties-container"),
            add_button("Add Property", btn_id="btn-add-property")
        ]
    )
    
    # ── Equity Chart ────────────────────────────────────────────────────
    chart_section = section_card(
        title="📊  Value vs Liability",
        children=[
            dcc.Graph(
                id="re-equity-chart",
                figure=_equity_bubble_chart(profile),
                config={"displayModeBar": False, "responsive": True},
            )
        ]
    )

    # ── Summary Strip ───────────────────────────────────────────────────
    total_val = sum(float(p.current_value or 0) for p in profile.properties)
    total_debt = sum(float(p.mortgage_balance or 0) for p in profile.properties)
    total_equity = total_val - total_debt
    
    net_rent_mo = sum((float(p.monthly_rental_income or 0) - float(p.monthly_expenses or 0) - float(p.monthly_payment or 0)) 
                       for p in profile.properties if p.property_type == "rental")
    
    strip = summary_row([
        ("Total Net Equity", f"${total_equity:,.0f}", "blue"),
        ("Total Property Value", f"${total_val:,.0f}", "green"),
        ("Total Debt", f"${total_debt:,.0f}", "red"),
        ("Net Rental CF", f"${net_rent_mo:,.0f}/mo", "amber"),
    ])

    return html.Div(
        [
            page_header(
                "Real Estate",
                subtitle="Track primary residences and cash-flowing rental properties.",
                icon="🏠",
            ),
            two_col(props_section, chart_section, left_width=8),
            strip,
            html.Div(
                html.Button(
                    "💾  Save Updates",
                    id="real-estate-save-btn",
                    className="btn-primary-custom",
                    n_clicks=0,
                ),
                style={"marginTop": "20px"},
            ),
        ]
    )
