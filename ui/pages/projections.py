"""
Projections page.

The main analytics dashboard with 4 detailed interactive charts:
1. Detailed Net Worth Over Time (stacked area by account)
2. Income vs Expenses (bar + line)
3. Portfolio Drawdown (line)
4. Retirement Income Sources (stacked bar)
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import html, dcc

from engine.models import PlanProfile, ACCOUNT_TAX_TREATMENT
from engine.projections import run_projection, _slugify
from ui.components import (
    section_card, page_header, two_col,
    PLOTLY_DARK_TEMPLATE, retirement_vline, summary_row
)

# Shared colour palette mapping for consistency
_COLORS = {
    # Accounts / buckets
    "re_equity":    "rgba(251,191,36,0.35)",   # Amber fill
    "tax_deferred": "rgba(167,139,250,0.5)",   # Purple fill
    "tax_free":     "rgba(52,211,153,0.5)",    # Green fill
    "taxable":      "rgba(74,122,247,0.5)",    # Blue fill

    # Flows
    "income":  "#34d399",   # Green
    "expense": "#f87171",   # Red
    "taxes":   "#fb923c",   # Orange
    "deficit": "#f43f5e",   # Rose
    "surplus": "#2dd4bf",   # Teal

    # Income Sources
    "ss":          "#4a7af7",   # Blue
    "rental":      "#fbbf24",   # Amber
    "withdrawals": "#a78bfa",  # Purple
    "salary":      "#2dd4bf",  # Teal
}


def _build_slug_tax_map(profile: PlanProfile) -> dict[str, str]:
    """
    Build a mapping from DataFrame column name (``bal_{slug}``) to tax
    treatment string (``"taxable"``, ``"tax_deferred"``, ``"tax_free"``)
    using the profile's actual account metadata.

    This replaces the previous heuristic that tried to guess account type
    from slug substrings like '401' or 'roth', which failed for accounts
    with user-provided names that don't contain those keywords.
    """
    return {
        f"bal_{_slugify(a.name)}": ACCOUNT_TAX_TREATMENT.get(a.account_type, "taxable")
        for a in profile.accounts
    }

def _get_projection_data(profile_data: Optional[dict], projection_data: Optional[list]) -> tuple[PlanProfile, pd.DataFrame]:
    """Helper to deserialize or lazy-evaluate projection runs."""
    profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
    if projection_data:
        df = pd.DataFrame(projection_data)
    else:
        # Default run to populate charts if store is empty during initial load
        _, df = run_projection(profile)
    return profile, df


# ---------------------------------------------------------------------------
# Chart 1: Detailed Net Worth Stacked Area
# ---------------------------------------------------------------------------
def _nw_details_chart(annual_df: pd.DataFrame, retire_yr: int, profile: PlanProfile) -> go.Figure:
    fig = go.Figure()
    years = annual_df["year"]

    # Bottom layer: Real Estate Equity
    if "equity_re_total" in annual_df:
        fig.add_trace(go.Scatter(
            x=years, y=annual_df["equity_re_total"],
            name="Real Estate Equity",
            mode="lines", fill="tozeroy",
            line=dict(width=0), fillcolor=_COLORS["re_equity"],
            stackgroup="nw"
        ))

    # Build slug → tax_treatment map from the actual profile accounts
    slug_tax_map = _build_slug_tax_map(profile)
    bal_cols = [c for c in annual_df.columns if c.startswith("bal_")]

    tax_def  = [c for c in bal_cols if slug_tax_map.get(c) == "tax_deferred"]
    tax_free = [c for c in bal_cols if slug_tax_map.get(c) == "tax_free"]
    taxable  = [c for c in bal_cols if slug_tax_map.get(c) == "taxable"]

    if taxable:
        y_taxable = annual_df[taxable].sum(axis=1)
        fig.add_trace(go.Scatter(
            x=years, y=y_taxable, name="Taxable Accounts",
            mode="lines", fill="tonexty", line=dict(width=0),
            fillcolor=_COLORS["taxable"], stackgroup="nw"
        ))

    if tax_def:
        y_def = annual_df[tax_def].sum(axis=1)
        fig.add_trace(go.Scatter(
            x=years, y=y_def, name="Tax-Deferred (401k/Trad)",
            mode="lines", fill="tonexty", line=dict(width=0),
            fillcolor=_COLORS["tax_deferred"], stackgroup="nw"
        ))

    if tax_free:
        y_free = annual_df[tax_free].sum(axis=1)
        fig.add_trace(go.Scatter(
            x=years, y=y_free, name="Tax-Free (Roth/HSA)",
            mode="lines", fill="tonexty", line=dict(width=0),
            fillcolor=_COLORS["tax_free"], stackgroup="nw"
        ))

    # Total Line
    fig.add_trace(go.Scatter(
        x=years, y=annual_df["net_worth_eoy"],
        name="Net Worth", mode="lines",
        line=dict(color="white", width=1.5),
        showlegend=False, hoverinfo="skip"
    ))

    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Detailed Net Worth Accumulation", "x": 0.02},
        "shapes": shapes, "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Chart 2: Income vs Expenses & Taxes
# ---------------------------------------------------------------------------
def _cashflow_chart(annual_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    fig = go.Figure()
    years = annual_df["year"]
    
    # Inflows (Income + Withdrawals)
    total_in = annual_df["income_total"] + annual_df["withdrawal_total"]
    fig.add_trace(go.Bar(
        x=years, y=total_in, name="Total Capital Inflow",
        marker_color=_COLORS["income"], opacity=0.85
    ))
    
    # Outflows (Expenses)
    fig.add_trace(go.Bar(
        x=years, y=annual_df["expense_total"], name="Base Expenses",
        marker_color=_COLORS["expense"], opacity=0.85
    ))
    
    # Taxes (Stacked on top of expenses visually? Let's just group them next to it for now)
    fig.add_trace(go.Bar(
        x=years, y=annual_df["tax_annual_est"], name="Taxes Paid",
        marker_color=_COLORS["taxes"], opacity=0.85
    ))
    
    # Net Surplus/Deficit line
    fig.add_trace(go.Scatter(
        x=years, y=annual_df["surplus_deficit"], name="Surplus/Deficit",
        mode="lines", line=dict(color="white", width=2, dash="dot"),
    ))

    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Annual Cash Flow & Tax Withholding", "x": 0.02},
        "barmode": "group",
        "shapes": shapes, "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Chart 3: Portfolio Drawdown Line
# ---------------------------------------------------------------------------
def _drawdown_chart(annual_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=annual_df["year"], y=annual_df["balance_investment_total"],
        name="Investment Portfolio", mode="lines+markers",
        line=dict(color="#4a7af7", width=3),
        marker=dict(size=4)
    ))

    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Investment Portfolio Drawdown Projection", "x": 0.02},
        "shapes": shapes, "annotations": annotations,
        "showlegend": False,
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Chart 4: Retirement Income Source Distribution
# ---------------------------------------------------------------------------
def _income_sources_chart(annual_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    # Filter to retirement years only (simplifies view)
    ret_df = annual_df[annual_df["year"] >= retire_yr]
    years = ret_df["year"]
    
    fig = go.Figure()
    
    # 1. Social Security
    ss_total = ret_df["income_ss_self"] + ret_df["income_ss_spouse"]
    fig.add_trace(go.Bar(
        x=years, y=ss_total, name="Social Security",
        marker_color=_COLORS["ss"],
    ))
    
    # 2. Portfolio Withdrawals
    fig.add_trace(go.Bar(
        x=years, y=ret_df["withdrawal_total"], name="Account Withdrawals",
        marker_color=_COLORS["withdrawals"],
    ))
    
    # 3. Rental Net Income
    if "income_rental_net" in ret_df:
        fig.add_trace(go.Bar(
            x=years, y=ret_df["income_rental_net"], name="Net Rental CF",
            marker_color=_COLORS["rental"],
        ))
        
    # 4. Any continuing salary
    salary = ret_df["income_salary_self"] + ret_df["income_salary_spouse"]
    fig.add_trace(go.Bar(
        x=years, y=salary, name="Working Income",
        marker_color=_COLORS["salary"],
    ))

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Retirement Inflows (Stacked)", "x": 0.02},
        "barmode": "stack",
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Main layout function
# ---------------------------------------------------------------------------
def layout(profile_data: Optional[dict] = None, projection_data: Optional[list] = None) -> html.Div:
    profile, annual_df = _get_projection_data(profile_data, projection_data)
    ret_yr = profile.retirement_year_self
    
    # Build charts
    nw_fig     = _nw_details_chart(annual_df, ret_yr, profile)  # pass profile for correct account classification
    cash_fig   = _cashflow_chart(annual_df, ret_yr)
    draw_fig   = _drawdown_chart(annual_df, ret_yr)
    inflow_fig = _income_sources_chart(annual_df, ret_yr)
    
    # Interactive view controls wrapper (to be functionalized in Phase 13)
    controls = html.Div(
        [
            html.Div("View Granularity", style={"fontSize": "11px", "textTransform": "uppercase", "color": "var(--text-muted)", "marginBottom": "6px"}),
            dcc.RadioItems(
                id="projections-view-toggle",
                options=[
                    {"label": " Annual   ", "value": "annual"},
                    {"label": " Monthly", "value": "monthly", "disabled": True} # Disabled until callbacks phase
                ],
                value="annual",
                inline=True,
                style={"color": "var(--text-primary)", "fontSize": "13px"}
            )
        ],
        style={"marginBottom": "20px"}
    )
    
    return html.Div(
        [
            html.Div(
                [
                    page_header(
                        "Life Projections",
                        subtitle="Detailed analytics engine runs your data out to life expectancy.",
                        icon="🔮",
                    ),
                    # We can float the controls right aligned in advanced UI, but putting it below header for now
                    controls,
                ]
            ),
            
            # Row 1 of Charts
            two_col(
                section_card("Net Worth Components", children=[dcc.Graph(figure=nw_fig, config={"displayModeBar": False})]),
                section_card("Cashflow Breakdown", children=[dcc.Graph(figure=cash_fig, config={"displayModeBar": False})]),
                left_width=6
            ),
            
            # Row 2 of Charts
            two_col(
                section_card("Retirement Inflows", children=[dcc.Graph(figure=inflow_fig, config={"displayModeBar": False})]),
                section_card("Portfolio Drawdown", children=[dcc.Graph(figure=draw_fig, config={"displayModeBar": False})]),
                left_width=6
            ),
            
        ]
    )
