"""
Dashboard page — at-a-glance retirement summary.

Content
-------
  Row 1 — 4 KPI metric cards:
    - Years to retirement
    - Projected nest egg (at retirement)
    - Estimated monthly retirement income
    - Plan success age (when money runs out — or "Life" if it doesn't)

  Row 2 — charts:
    - Left (8 cols): Net worth over time — stacked area by asset class
    - Right (4 cols): Retirement income breakdown donut + quick-action buttons

Data
----
Accepts ``profile_data`` (PlanProfile dict) and ``projection_data``
(annual_df records).  If projection_data is None the engine is run
automatically from the profile so the dashboard always shows real numbers.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import dcc
from dash import html

from engine.models import PlanProfile
from engine.projections import run_projection
from ui.components import (
    metric_card, section_card, page_header, four_col,
    PLOTLY_DARK_TEMPLATE, retirement_vline, summary_row, info_badge,
)


# ---------------------------------------------------------------------------
# Colour palette (consistent with design system)
# ---------------------------------------------------------------------------
_C = {
    "blue":   "#4a7af7",
    "green":  "#34d399",
    "amber":  "#fbbf24",
    "purple": "#a78bfa",
    "teal":   "#2dd4bf",
    "red":    "#f87171",
    "muted":  "rgba(148,163,184,0.3)",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fmt_currency(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _fmt_years(n: int) -> str:
    return f"{n} yr{'' if n == 1 else 's'}"


def _get_projection(profile_data: Optional[dict], projection_data: Optional[list]) -> tuple:
    """Return (profile, annual_df) — run engine if projection_data is missing."""
    if profile_data:
        profile = PlanProfile.from_dict(profile_data)
    else:
        profile = PlanProfile.sample()

    if projection_data:
        import pandas as pd
        annual_df = pd.DataFrame(projection_data)
    else:
        _, annual_df = run_projection(profile)

    return profile, annual_df


# ---------------------------------------------------------------------------
# KPI card values
# ---------------------------------------------------------------------------
def _compute_kpis(profile: PlanProfile, annual_df) -> dict:
    today      = date.today().year
    retire_yr  = profile.retirement_year_self
    years_left = max(0, retire_yr - today)

    # Nest egg at retirement
    retire_row = annual_df[annual_df["year"] == retire_yr]
    if retire_row.empty:
        retire_row = annual_df.iloc[annual_df["year"].sub(retire_yr).abs().argsort()].iloc[[0]]
    nest_egg = retire_row["balance_investment_total"].values[0] if not retire_row.empty else 0

    # Monthly retirement income (SS + rental — no withdrawals counted as "income")
    retire_slice = annual_df[annual_df["year"] >= retire_yr].head(5)
    avg_income   = retire_slice["income_total"].mean() / 12 if not retire_slice.empty else 0

    # Plan success: last year with positive net worth
    positive_nw = annual_df[annual_df["net_worth_eoy"] > 0]
    if not positive_nw.empty:
        last_pos_year = int(positive_nw["year"].max())
        last_pos_age  = int(positive_nw.iloc[-1]["self_age_eoy"])
        success_label = (
            f"Age {last_pos_age}+"
            if last_pos_year >= profile.plan_end_year
            else f"Age {last_pos_age}"
        )
        is_success = last_pos_year >= profile.plan_end_year
    else:
        success_label = "—"
        is_success    = False

    return {
        "years_left":    years_left,
        "nest_egg":      nest_egg,
        "monthly_income":avg_income,
        "success_label": success_label,
        "is_success":    is_success,
        "retire_yr":     retire_yr,
    }


# ---------------------------------------------------------------------------
# Chart: Net Worth Over Time
# ---------------------------------------------------------------------------
def _net_worth_chart(annual_df, retire_yr: int) -> go.Figure:
    years = annual_df["year"].tolist()

    # Investment balance broken into buckets if columns exist
    bal_cols_deferred = [c for c in annual_df.columns
                         if c.startswith("bal_") and c in annual_df.columns]

    # Use aggregate columns for simplicity
    invest = annual_df["balance_investment_total"].tolist()
    re     = annual_df["equity_re_total"].tolist()

    fig = go.Figure()

    # Real estate equity (bottom layer)
    fig.add_trace(go.Scatter(
        x=years, y=re,
        name="Real Estate Equity",
        fill="tozeroy",
        mode="lines",
        line=dict(width=0, color=_C["amber"]),
        fillcolor="rgba(251,191,36,0.25)",
        hovertemplate="Real Estate: $%{y:,.0f}<extra></extra>",
    ))

    # Investment portfolio (stacked on top)
    re_arr = list(re)
    invest_top = [i + r for i, r in zip(invest, re_arr)]
    fig.add_trace(go.Scatter(
        x=years, y=invest_top,
        name="Investment Portfolio",
        fill="tonexty",
        mode="lines",
        line=dict(width=1.5, color=_C["blue"]),
        fillcolor="rgba(74,122,247,0.2)",
        hovertemplate="Investments: $%{y:,.0f}<extra></extra>",
    ))

    # Net worth line on top
    fig.add_trace(go.Scatter(
        x=years, y=annual_df["net_worth_eoy"].tolist(),
        name="Total Net Worth",
        mode="lines",
        line=dict(width=2.5, color=_C["green"], dash="solid"),
        hovertemplate="Net Worth: $%{y:,.0f}<extra></extra>",
    ))

    # Retirement year marker
    shapes, annotations = retirement_vline(retire_yr, "Retirement")

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title":       {"text": "Net Worth Over Time", "x": 0.02, "xanchor": "left"},
        "shapes":      shapes,
        "annotations": annotations,
        "legend":      {**PLOTLY_DARK_TEMPLATE["layout"]["legend"],
                        "orientation": "h", "y": -0.15, "x": 0},
        "height":      340,
    })
    fig.update_layout(**layout)
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return fig


# ---------------------------------------------------------------------------
# Chart: Retirement Income Breakdown Donut
# ---------------------------------------------------------------------------
def _income_donut(profile: PlanProfile, annual_df) -> go.Figure:
    retire_yr    = profile.retirement_year_self
    retire_slice = annual_df[
        (annual_df["year"] >= retire_yr) &
        (annual_df["year"] < retire_yr + 5)
    ]

    if retire_slice.empty:
        retire_slice = annual_df.tail(5)

    avg = retire_slice.mean(numeric_only=True)

    # Build income slices
    labels, values, colors_list = [], [], []

    def _add(label: str, col: str, color: str):
        v = float(avg.get(col, 0))
        if v > 0:
            labels.append(label)
            values.append(v)
            colors_list.append(color)

    _add("Salary / Work",    "income_salary_self",   _C["blue"])
    _add("Spouse Work",      "income_salary_spouse", "rgba(74,122,247,0.6)")
    _add("Social Security",  "income_ss_self",       _C["green"])
    _add("Spouse SS",        "income_ss_spouse",     "rgba(52,211,153,0.6)")
    _add("Rental Income",    "income_rental_net",    _C["amber"])
    _add("Withdrawals",      "withdrawal_total",     _C["purple"])

    if not values:
        values = [1]
        labels = ["No data"]
        colors_list = [_C["muted"]]

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.6,
        marker=dict(colors=colors_list, line=dict(color="var(--bg-card)", width=2)),
        textinfo="label+percent",
        textfont=dict(size=11, family="Inter"),
        hovertemplate="%{label}: $%{value:,.0f}/yr<extra></extra>",
    ))

    total_annual = sum(values)
    fig.add_annotation(
        text=f"${total_annual / 12:,.0f}<br><span style='font-size:10px'>/ month</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=15, color="var(--text-primary)", family="Inter"),
        align="center",
    )

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title":  {"text": "Retirement Income Sources", "x": 0.02, "xanchor": "left"},
        "height": 240,
        "margin": {"l": 10, "r": 10, "t": 44, "b": 10},
        "showlegend": False,
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Quick action buttons
# ---------------------------------------------------------------------------
def _quick_actions() -> html.Div:
    return html.Div(
        [
            html.Div(
                "Quick Actions",
                style={
                    "fontSize": "11px", "fontWeight": "600",
                    "letterSpacing": "0.8px", "textTransform": "uppercase",
                    "color": "var(--text-muted)", "marginBottom": "10px",
                },
            ),
            html.A(
                "👤  Edit Profile",
                href="/profile",
                className="btn-ghost",
                style={"display": "block", "width": "100%", "marginBottom": "8px",
                       "textDecoration": "none", "textAlign": "center"},
            ),
            html.A(
                "🔮  View Projections",
                href="/projections",
                className="btn-ghost",
                style={"display": "block", "width": "100%", "marginBottom": "8px",
                       "textDecoration": "none", "textAlign": "center"},
            ),
            html.A(
                html.Button("▶  Run Projections", className="btn-primary-custom w-100"),
                href="/projections",
                style={"textDecoration": "none"},
            ),
        ],
        style={"marginTop": "16px"},
    )


# ---------------------------------------------------------------------------
# Main layout function
# ---------------------------------------------------------------------------
def layout(
    profile_data: Optional[dict] = None,
    projection_data: Optional[list] = None,
) -> html.Div:
    """Render the full dashboard page."""
    import plotly.io as pio

    profile, annual_df = _get_projection(profile_data, projection_data)
    kpis = _compute_kpis(profile, annual_df)

    # ── KPI cards ─────────────────────────────────────────────────────────
    card_row = four_col(
        metric_card(
            title="Years to Retirement",
            value=_fmt_years(kpis["years_left"]),
            subtitle=f"Retiring in {kpis['retire_yr']}",
            icon="⏳",
            accent="blue",
            card_id="kpi-years-to-retire",
        ),
        metric_card(
            title="Nest Egg at Retirement",
            value=_fmt_currency(kpis["nest_egg"]),
            subtitle="Investment portfolio",
            icon="🥚",
            accent="green",
            card_id="kpi-nest-egg",
        ),
        metric_card(
            title="Monthly Income",
            value=_fmt_currency(kpis["monthly_income"]),
            subtitle="SS + rental (first 5 yrs)",
            icon="💰",
            accent="amber",
            card_id="kpi-monthly-income",
        ),
        metric_card(
            title="Plan Success",
            value=kpis["success_label"],
            subtitle="Money lasts until..." if not kpis["is_success"] else "Funds outlast plan",
            icon="✅" if kpis["is_success"] else "⚠️",
            accent="green" if kpis["is_success"] else "amber",
            card_id="kpi-plan-success",
        ),
    )

    # ── Charts ─────────────────────────────────────────────────────────────
    nw_fig     = _net_worth_chart(annual_df, kpis["retire_yr"])
    donut_fig  = _income_donut(profile, annual_df)

    chart_row = dbc.Row(
        [
            # Net worth chart (left 8 cols)
            dbc.Col(
                html.Div(
                    dcc.Graph(
                        id="dash-networth-chart",
                        figure=nw_fig,
                        config={"displayModeBar": False, "responsive": True},
                    ),
                    className="section-card",
                    style={"padding": "12px"},
                ),
                xs=12, lg=8,
            ),

            # Right panel (donut + quick actions)
            dbc.Col(
                [
                    html.Div(
                        dcc.Graph(
                            id="dash-income-donut",
                            figure=donut_fig,
                            config={"displayModeBar": False, "responsive": True},
                        ),
                        className="section-card",
                        style={"padding": "12px"},
                    ),
                    _quick_actions(),
                ],
                xs=12, lg=4,
            ),
        ],
        className="g-4",
    )

    # ── Summary strip ──────────────────────────────────────────────────────
    last_row = annual_df.iloc[-1]
    strip = summary_row([
        ("Plan end net worth",   _fmt_currency(float(last_row["net_worth_eoy"])),    "green"),
        ("Total real estate eq", _fmt_currency(float(last_row["equity_re_total"])),  "blue"),
        ("Plan horizon",         f"{profile.self_person.current_age}–{int(last_row['self_age_eoy'])} yrs", "amber"),
        ("Inflation assumed",    f"{profile.inflation_rate_pct:.1f}% / yr",          "purple"),
    ])

    return html.Div(
        [
            page_header(
                "Dashboard",
                subtitle=f"{profile.plan_name}  ·  {profile.self_person.name} & {profile.spouse.name}",
                icon="📊",
            ),
            card_row,
            html.Div(style={"height": "8px"}),
            chart_row,
            strip,
        ]
    )
