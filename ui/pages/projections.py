"""
Projections page.

The main analytics dashboard with detailed interactive charts:
1. Detailed Net Worth Over Time (stacked area by account)
2. Income vs Expenses (bar + line)
3. Portfolio Drawdown (line)
4. Annual Inflows by Source (stacked bar)
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import html, dcc

from engine.models import PlanProfile, ACCOUNT_TAX_TREATMENT
from engine.projections import CASHFLOW_CHECK_TOLERANCE, run_projection, _slugify
from ui.components import (
    section_card, two_col,
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
    "expense": "#dc2626",   # Dark Red
    "taxes":   "#8b5cf6",   # Purple
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


def _fmt_currency(value: float, signed: bool = False) -> str:
    """Format dollars for compact audit UI."""
    value = float(value or 0)
    sign = ""
    if signed:
        sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _with_cashflow_check_columns(annual_df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure cashflow audit columns exist, including for older session data.

    The engine now emits these columns directly, but users may still have an
    existing projection-store payload from before the fields were added.
    """
    df = annual_df.copy()
    required = {"income_total", "withdrawal_total", "expense_total", "tax_annual_est", "contrib_total"}

    if "cashflow_check" not in df.columns and required.issubset(df.columns):
        employer_match = df["contrib_employer_match"] if "contrib_employer_match" in df.columns else 0.0
        df["cashflow_check"] = (
            df["income_total"]
            + df["withdrawal_total"]
            + employer_match
            - df["expense_total"]
            - df["tax_annual_est"]
            - df["contrib_total"]
        ).round(2)

    if "cashflow_check" in df.columns:
        df["cashflow_check"] = pd.to_numeric(df["cashflow_check"], errors="coerce").fillna(0.0).round(2)
        df["cashflow_check_ok"] = df["cashflow_check"].abs() <= CASHFLOW_CHECK_TOLERANCE

    return df


def _cashflow_failure_table(failed: pd.DataFrame) -> html.Div:
    """Render the failed-year cashflow audit details."""
    display = failed.head(12)
    rows = []
    for _, row in display.iterrows():
        diff = float(row.get("cashflow_check", 0.0) or 0.0)
        rows.append(
            html.Tr(
                [
                    html.Td(f"{int(row['year'])}"),
                    html.Td(_fmt_currency(diff, signed=True), className="cashflow-check-diff"),
                    html.Td(_fmt_currency(row.get("income_total", 0.0))),
                    html.Td(_fmt_currency(row.get("withdrawal_total", 0.0))),
                    html.Td(_fmt_currency(row.get("expense_total", 0.0))),
                    html.Td(_fmt_currency(row.get("tax_annual_est", 0.0))),
                    html.Td(_fmt_currency(row.get("contrib_total", 0.0))),
                ]
            )
        )

    more_count = len(failed) - len(display)
    more_note = html.Div(
        f"{more_count} more failed year{'s' if more_count != 1 else ''} in the CSV export.",
        className="cashflow-check-note",
    ) if more_count > 0 else None

    return html.Div(
        [
            html.Div("Failed years", className="cashflow-check-table-title"),
            html.Div(
                html.Table(
                    [
                        html.Thead(
                            html.Tr([
                                html.Th("Year"),
                                html.Th("Difference"),
                                html.Th("Income"),
                                html.Th("Withdrawals"),
                                html.Th("Expenses"),
                                html.Th("Taxes"),
                                html.Th("Contributions"),
                            ])
                        ),
                        html.Tbody(rows),
                    ],
                    className="cashflow-check-table",
                ),
                className="cashflow-check-table-wrap",
            ),
            more_note,
        ],
        className="cashflow-check-details",
    )


def _cashflow_check_panel(annual_df: pd.DataFrame) -> html.Div:
    """Show annual cashflow check status and failed-year details."""
    if "cashflow_check" not in annual_df.columns or annual_df.empty:
        return section_card(
            "Cashflow Check",
            children=[
                html.Div(
                    "Cashflow audit data is unavailable for this projection run.",
                    className="cashflow-check-note",
                )
            ],
        )

    failed = annual_df[~annual_df["cashflow_check_ok"].fillna(False)]
    max_abs = float(annual_df["cashflow_check"].abs().max() or 0.0)
    status_ok = failed.empty

    summary = summary_row([
        ("Status", "All years pass" if status_ok else f"{len(failed)} failed year{'s' if len(failed) != 1 else ''}",
         "green" if status_ok else "red"),
        ("Largest Difference", _fmt_currency(max_abs), "green" if status_ok else "red"),
        ("Tolerance", _fmt_currency(CASHFLOW_CHECK_TOLERANCE), "blue"),
    ])

    message = html.Div(
        "Annual income, expenses, withdrawals, and contributions balance within rounding tolerance."
        if status_ok else
        "One or more annual cashflow rows do not balance. Review the failed years below.",
        className=f"cashflow-check-message {'ok' if status_ok else 'fail'}",
    )

    children = [message, summary]
    if not status_ok:
        children.append(_cashflow_failure_table(failed))

    return section_card("Cashflow Check", children=children)


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
        "shapes": shapes, "annotations": annotations,
        "showlegend": False,
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Chart 4: Annual Inflows by Source
# ---------------------------------------------------------------------------
def _income_sources_chart(annual_df: pd.DataFrame, retire_yr: int) -> go.Figure:
    years = annual_df["year"]

    fig = go.Figure()

    # 1. Social Security
    ss_total = annual_df["income_ss_self"] + annual_df["income_ss_spouse"]
    fig.add_trace(go.Bar(
        x=years, y=ss_total, name="Social Security",
        marker_color=_COLORS["ss"],
    ))

    # 2. Portfolio Withdrawals
    fig.add_trace(go.Bar(
        x=years, y=annual_df["withdrawal_total"], name="Account Withdrawals",
        marker_color=_COLORS["withdrawals"],
    ))

    # 3. Rental Net Income
    if "income_rental_net" in annual_df:
        fig.add_trace(go.Bar(
            x=years, y=annual_df["income_rental_net"], name="Net Rental CF",
            marker_color=_COLORS["rental"],
        ))

    # 4. Any continuing salary
    salary = annual_df["income_salary_self"] + annual_df["income_salary_spouse"]
    fig.add_trace(go.Bar(
        x=years, y=salary, name="Working Income",
        marker_color=_COLORS["salary"],
    ))

    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "barmode": "stack",
        "shapes": shapes, "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"], "orientation": "h", "y": -0.15, "x": 0},
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Main layout function
# ---------------------------------------------------------------------------
def layout(profile_data: Optional[dict] = None, projection_data: Optional[list] = None) -> html.Div:
    profile, annual_df = _get_projection_data(profile_data, projection_data)
    annual_df = _with_cashflow_check_columns(annual_df)
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
                style={"color": "var(--text-muted)", "fontSize": "13px"}
            )
        ],
        style={"marginBottom": "20px"}
    )

    return html.Div(
        [
            html.Div(
                [
                    html.P("Detailed analytics engine runs your data out to life expectancy.",
                           style={"fontSize": "14px", "color": "var(--text-secondary)", "marginBottom": "24px"}),
                    # We can float the controls right aligned in advanced UI, but putting it below header for now
                    controls,
                ]
            ),

            # Charts - Full Width Single Column
            section_card("Detailed Net Worth Accumulation", children=[dcc.Graph(figure=nw_fig, config={"displayModeBar": False})]),

            _cashflow_check_panel(annual_df),

            section_card("Annual Cash Flow & Tax Withholding", children=[dcc.Graph(figure=cash_fig, config={"displayModeBar": False})]),

            # Row 2 of Charts
            section_card("Annual Inflows by Source", children=[dcc.Graph(figure=inflow_fig, config={"displayModeBar": False})]),

        ]
    )
