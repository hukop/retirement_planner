"""
Roth Conversion Projection page.

Interactive tool to model the impact of converting Traditional IRA funds to a
Roth IRA over a specified window.
"""

from __future__ import annotations
from typing import Optional
from datetime import date

import dash_bootstrap_components as dbc
from dash import html, dcc
from dash.dash_table import DataTable
import plotly.graph_objects as go
import pandas as pd

from engine.models import PlanProfile
from engine.roth_conversion import RothConversionResult
from ui.components import (
    section_card, metric_card, four_col, input_row, two_col,
    PLOTLY_DARK_TEMPLATE, retirement_vline
)

# ---------------------------------------------------------------------------
# UI Controls (Inputs)
# ---------------------------------------------------------------------------
def _config_panel() -> html.Div:
    """The configuration form for the Roth conversion analysis."""
    current_yr = date.today().year
    
    return section_card(
        "Conversion Strategy",
        icon="🔄",
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Annual Conversion Amount",
                            input_id="roth-input-amount",
                            value=50000,
                            prefix="$",
                            step=1000,
                            tooltip="Amount to convert from Traditional IRA to Roth IRA each year.",
                            persistence=True,
                        ),
                        width=12, md=3
                    ),
                    dbc.Col(
                        input_row(
                            label="Start Year",
                            input_id="roth-input-start-year",
                            value=current_yr,
                            step=1,
                            tooltip="First year to perform a conversion.",
                            persistence=True,
                        ),
                        width=12, md=3
                    ),
                    dbc.Col(
                        input_row(
                            label="End Year",
                            input_id="roth-input-end-year",
                            value=current_yr + 9, # Default 10 yr window
                            step=1,
                            tooltip="Last year to perform a conversion.",
                            persistence=True,
                        ),
                        width=12, md=3
                    ),
                    dbc.Col(
                        html.Div(
                            [
                                html.Div(style={"height": "24px"}), # spacer for label
                                html.Button(
                                    "▶ Run Analysis",
                                    id="btn-run-roth",
                                    className="btn-primary-custom w-100",
                                    n_clicks=0,
                                ),
                            ]
                        ),
                        width=12, md=3
                    )
                ],
                className="g-3"
            )
        ]
    )


# ---------------------------------------------------------------------------
# Empty State
# ---------------------------------------------------------------------------
def _empty_state() -> html.Div:
    return html.Div(
        [
            html.H3("Ready to Analyze", style={"color": "var(--text-primary)"}),
            html.P(
                "Configure your conversion strategy above and click Run Analysis "
                "to compare scenarios.",
                style={"color": "var(--text-muted)"}
            ),
        ],
        style={
            "textAlign": "center",
            "padding": "60px 20px",
            "border": "1px dashed var(--border-subtle)",
            "borderRadius": "var(--radius-lg)",
            "marginTop": "24px"
        }
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _net_worth_comparison_chart(baseline_df: pd.DataFrame, conv_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    fig = go.Figure()
    years = baseline_df["year"]
    
    # Conversion Scenario (Green/Teal)
    fig.add_trace(go.Scatter(
        x=years, y=conv_df["net_worth_eoy"],
        name="With Conversion",
        mode="lines",
        line=dict(color="#2dd4bf", width=3),
    ))
    
    # Baseline Scenario (Gray/Blue)
    fig.add_trace(go.Scatter(
        x=years, y=baseline_df["net_worth_eoy"],
        name="Baseline (No Conversion)",
        mode="lines",
        line=dict(color="#4a7af7", width=2, dash="dash"),
    ))
    
    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "shapes": shapes, "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
        "hovermode": "x unified"
    })
    fig.update_layout(**layout)
    return fig


def _annual_tax_chart(baseline_df: pd.DataFrame, conv_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    fig = go.Figure()
    years = baseline_df["year"]
    
    fig.add_trace(go.Bar(
        x=years, y=conv_df["tax_annual_est"],
        name="Taxes (With Conversion)",
        marker_color="#fb923c", opacity=0.85
    ))
    
    fig.add_trace(go.Bar(
        x=years, y=baseline_df["tax_annual_est"],
        name="Taxes (Baseline)",
        marker_color="#94a3b8", opacity=0.6
    ))
    
    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "barmode": "group",
        "shapes": shapes, "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
    })
    fig.update_layout(**layout)
    return fig


def _account_mix_chart(conv_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    """Shows the shift from Tax-Deferred to Tax-Free in the conversion scenario."""
    fig = go.Figure()
    years = conv_df["year"]
    
    # We don't have the exact tax buckets pre-aggregated in the output easily,
    # but we can sum the columns that have "trad_ira", "401k", "roth" in them.
    # A robust way is to use the same logic as projections if we had the profile.
    # Since we just have the df, let's look for "tax_free" and "tax_deferred".
    # Wait, the projections df only has `bal_{slug}`.
    # For now, let's plot the total Investment Balance as a simple line if we can't easily parse buckets.
    # Actually, we can just do a total portfolio balance vs baseline.
    
    fig.add_trace(go.Scatter(
        x=years, y=conv_df["balance_investment_total"],
        name="Investments (Conversion)",
        mode="lines", line=dict(color="#2dd4bf", width=2),
    ))
    
    # If the user wants to see the RMDs explicitly:
    if "withdrawal_rmd" in conv_df.columns:
        fig.add_trace(go.Bar(
            x=years, y=conv_df["withdrawal_rmd"],
            name="RMDs",
            marker_color="#f87171",
            yaxis="y2", opacity=0.6
        ))

    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "shapes": shapes, "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
        "yaxis2": {
            "title": "RMDs",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "tickformat": ",.0f",
            "tickprefix": "$"
        }
    })
    fig.update_layout(**layout)
    return fig


def _marginal_rate_chart(details: list[dict]) -> go.Figure:
    fig = go.Figure()
    
    if not details:
        return fig
        
    df = pd.DataFrame(details)
    
    fig.add_trace(go.Scatter(
        x=df["year"], y=df["marginal_rate_combined"],
        name="Combined Marginal Rate",
        mode="lines+markers", line=dict(color="#a78bfa", width=2),
    ))
    
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "yaxis": {
            **PLOTLY_DARK_TEMPLATE["layout"]["yaxis"],
            "tickprefix": "",
            "ticksuffix": "%",
            "tickformat": ".1f"
        }
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Results Section
# ---------------------------------------------------------------------------
def _results_section(result: RothConversionResult, retire_yr: int) -> html.Div:
    
    # 1. KPI Metrics
    metrics_row = four_col(
        metric_card(
            title="Total Converted",
            value=f"${result.total_converted:,.0f}",
            subtitle=f"Over {result.years_of_conversion} years",
            accent="blue",
            icon="🔄"
        ),
        metric_card(
            title="Tax Cost (Upfront)",
            value=f"${result.total_tax_cost:,.0f}",
            subtitle="Incremental tax during window",
            accent="red",
            icon="💸"
        ),
        metric_card(
            title="Lifetime Tax Savings",
            value=f"${result.lifetime_tax_savings:,.0f}",
            subtitle="Net difference in taxes paid",
            accent="green",
            icon="🛡️"
        ),
        metric_card(
            title="Net Worth Delta (End)",
            value=f"${result.net_worth_delta_at_end:,.0f}",
            subtitle=f"Breakeven: {result.breakeven_year or 'N/A'}",
            accent="purple" if result.net_worth_delta_at_end > 0 else "red",
            trend="+" if result.net_worth_delta_at_end > 0 else "",
            trend_dir="up" if result.net_worth_delta_at_end > 0 else "down",
            icon="💰"
        ),
    )
    
    baseline_df = pd.DataFrame(result.baseline_annual)
    conv_df = pd.DataFrame(result.conversion_annual)
    
    nw_fig = _net_worth_comparison_chart(baseline_df, conv_df, retire_yr)
    tax_fig = _annual_tax_chart(baseline_df, conv_df, retire_yr)
    mix_fig = _account_mix_chart(conv_df, retire_yr)
    rate_fig = _marginal_rate_chart(result.conversion_details)
    
    # DataTable
    df_details = pd.DataFrame(result.conversion_details)
    table = html.Div()
    if not df_details.empty:
        table = DataTable(
            data=df_details.to_dict("records"),
            columns=[
                {"name": "Year", "id": "year"},
                {"name": "Converted ($)", "id": "conversion_amount", "type": "numeric", "format": {"specifier": "$,.0f"}},
                {"name": "Incr. Tax ($)", "id": "incremental_tax", "type": "numeric", "format": {"specifier": "$,.0f"}},
                {"name": "Fed Rate (%)", "id": "marginal_rate_federal", "type": "numeric", "format": {"specifier": ".1f"}},
                {"name": "CA Rate (%)", "id": "marginal_rate_ca", "type": "numeric", "format": {"specifier": ".1f"}},
                {"name": "Trad IRA Left ($)", "id": "source_balance_after", "type": "numeric", "format": {"specifier": "$,.0f"}},
            ],
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": "var(--bg-surface)",
                "color": "var(--text-primary)",
                "border": "1px solid var(--border-subtle)",
                "fontFamily": "Inter, sans-serif",
                "fontSize": "13px",
                "padding": "8px"
            },
            style_header={
                "backgroundColor": "var(--bg-panel)",
                "fontWeight": "600",
                "color": "var(--text-secondary)"
            }
        )
    
    return html.Div(
        [
            metrics_row,
            
            two_col(
                section_card("Net Worth Comparison", children=[dcc.Graph(figure=nw_fig, config={"displayModeBar": False})]),
                section_card("Annual Tax Paid", children=[dcc.Graph(figure=tax_fig, config={"displayModeBar": False})]),
                left_width=6
            ),
            
            two_col(
                section_card("Investments & RMDs (Conversion)", children=[dcc.Graph(figure=mix_fig, config={"displayModeBar": False})]),
                section_card("Marginal Tax Rate During Window", children=[dcc.Graph(figure=rate_fig, config={"displayModeBar": False})]),
                left_width=6
            ),
            
            section_card(
                "Conversion Schedule Details",
                children=[table]
            )
        ],
        style={"marginTop": "24px"}
    )


# ---------------------------------------------------------------------------
# Main Layout
# ---------------------------------------------------------------------------
def layout(profile_data: Optional[dict] = None, roth_data: Optional[dict] = None) -> html.Div:
    # Build default config panel
    panel = _config_panel()
    
    # If we have run data, show results; otherwise empty state
    if roth_data:
        profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
        retire_yr = profile.retirement_year_self
        # Reconstruct result object
        result = RothConversionResult(**roth_data)
        content = _results_section(result, retire_yr)
    else:
        content = _empty_state()

    return html.Div(
        [
            html.Div(
                [
                    html.P(
                        "Model the long-term impact of converting pre-tax Traditional IRA funds to a tax-free Roth IRA.",
                        style={"fontSize": "14px", "color": "var(--text-secondary)", "marginBottom": "24px"}
                    ),
                ]
            ),
            panel,
            html.Div(id="roth-results-area", children=content)
        ]
    )
