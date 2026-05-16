"""
Monte Carlo Simulation page.

Layout
------
  Section 0 — Intro description (inline with page flow)
  Section 1 — Configuration panel (trials, return, std dev, seed)
  Section 2 — Headline KPI cards (success %, median, worst, best)
  Section 3 — Fan Chart (net worth percentile bands over time)
  Section 4 — Scenario Comparison (median / worst / best / deterministic lines)
  Section 5 — Terminal Net Worth Histogram
  Section 6 — Ruin Year Distribution
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import html, dcc

from engine.models import PlanProfile, MonteCarloConfig
from engine.monte_carlo import MonteCarloResult
from ui.components import (
    section_card, two_col, four_col, metric_card,
    PLOTLY_DARK_TEMPLATE, retirement_vline, summary_row,
    slider_row, input_row,
)


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
_C = {
    "p50":   "#4a7af7",      # Blue — median line
    "p25_75":"rgba(74,122,247,0.25)",   # Inner band (25th–75th)
    "p10_90":"rgba(74,122,247,0.12)",   # Middle band (10th–90th)
    "p5_95": "rgba(74,122,247,0.06)",   # Outer band (5th–95th)
    "det":   "#fbbf24",      # Amber — deterministic line
    "worst": "#f87171",      # Red — worst case
    "best":  "#34d399",      # Green — best case
    "zero":  "rgba(248,113,113,0.3)",   # Red fill — ruin zone
    "success_hi":  "#34d399",
    "success_mid": "#fbbf24",
    "success_lo":  "#f87171",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_currency(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _success_accent(rate: float) -> str:
    if rate >= 0.80:
        return "green"
    if rate >= 0.60:
        return "amber"
    return "red"


# ---------------------------------------------------------------------------
# KPI cards from result
# ---------------------------------------------------------------------------
def _kpi_cards(result: MonteCarloResult) -> dbc.Row:
    nws  = result.terminal_net_worths  # already sorted ascending
    p50  = float(np.percentile(nws, 50))
    p5   = float(np.percentile(nws, 5))
    p95  = float(np.percentile(nws, 95))
    rate = result.success_rate
    acc  = _success_accent(rate)

    return four_col(
        metric_card(
            title="Probability of Success",
            value=_fmt_pct(rate),
            subtitle=f"Portfolio survives to plan end ",
            icon="🎯",
            accent=acc,
            card_id="mc-kpi-success",
        ),
        metric_card(
            title="Median End Net Worth",
            value=_fmt_currency(p50),
            subtitle="50th percentile terminal value",
            icon="📊",
            accent="blue",
            card_id="mc-kpi-median",
        ),
        metric_card(
            title="Worst Case (5th %ile)",
            value=_fmt_currency(p5),
            subtitle="Only 5% of scenarios do worse",
            icon="📉",
            accent="red",
            card_id="mc-kpi-worst",
        ),
        metric_card(
            title="Best Case (95th %ile)",
            value=_fmt_currency(p95),
            subtitle="Only 5% of scenarios do better",
            icon="📈",
            accent="green",
            card_id="mc-kpi-best",
        ),
    )


# ---------------------------------------------------------------------------
# Chart 1: Fan Chart (percentile bands)
# ---------------------------------------------------------------------------
def _fan_chart(result: MonteCarloResult, retire_yr: int) -> go.Figure:
    fig  = go.Figure()
    yrs  = result.years
    pcts = result.net_worth_percentiles   # list[list[float]], shape (n_years, 7)
    # Percentile indices: [5, 10, 25, 50, 75, 90, 95] → idx 0..6
    p5   = [row[0] for row in pcts]
    p10  = [row[1] for row in pcts]
    p50  = [row[3] for row in pcts]

    # ── Severe downside band: 5th → 10th (darkest fill) ──────────────────
    fig.add_trace(go.Scatter(
        x=yrs + yrs[::-1], y=p10 + p5[::-1],
        fill="toself", fillcolor="rgba(248,113,113,0.20)",
        line=dict(width=0), showlegend=True,
        name="5th–10th %ile (severe downside)", hoverinfo="skip",
    ))

    # ── Moderate downside band: 10th → median (lighter fill) ─────────────
    fig.add_trace(go.Scatter(
        x=yrs + yrs[::-1], y=p50 + p10[::-1],
        fill="toself", fillcolor="rgba(251,191,36,0.12)",
        line=dict(width=0), showlegend=True,
        name="10th–50th %ile (moderate downside)", hoverinfo="skip",
    ))

    # ── 10th percentile boundary line ────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=yrs, y=p10,
        mode="lines", line=dict(color="rgba(251,191,36,0.7)", width=1.5, dash="dash"),
        name="10th %ile",
        hovertemplate="10th %ile: $%{y:,.0f}<extra></extra>",
    ))

    # ── 5th percentile boundary line ─────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=yrs, y=p5,
        mode="lines", line=dict(color="rgba(248,113,113,0.8)", width=1.5, dash="dash"),
        name="5th %ile (worst 5%)",
        hovertemplate="5th %ile: $%{y:,.0f}<extra></extra>",
    ))

    # ── Median line ───────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=yrs, y=p50,
        mode="lines", line=dict(color=_C["p50"], width=2.5),
        name="Median (50th %ile)",
        hovertemplate="Median: $%{y:,.0f}<extra></extra>",
    ))

    # ── Zero line (ruin threshold) ────────────────────────────────────────
    fig.add_hline(
        y=0, line=dict(color="rgba(248,113,113,0.6)", width=1, dash="dash"),
    )

    shapes, annotations = retirement_vline(retire_yr)
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "shapes": shapes,
        "annotations": annotations,
        "legend": {**PLOTLY_DARK_TEMPLATE["layout"]["legend"],
                   "orientation": "h", "y": -0.18, "x": 0},
        "height": 600,
    })
    fig.update_layout(**layout)
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return fig


# ---------------------------------------------------------------------------
# Chart 2: Terminal Net Worth Histogram
# ---------------------------------------------------------------------------
def _terminal_histogram(result: MonteCarloResult) -> go.Figure:
    fig = go.Figure()
    nws = result.terminal_net_worths

    fig.add_trace(go.Histogram(
        x=nws,
        nbinsx=40,
        marker=dict(
            color=[_C["worst"] if v < 0 else _C["p50"] for v in nws],
            line=dict(color="rgba(0,0,0,0)", width=0),
        ),
        opacity=0.75,
        name="Terminal Net Worth",
        hovertemplate="$%{x:,.0f}: %{y} trials<extra></extra>",
    ))

    # Ruin boundary
    fig.add_vline(
        x=0,
        line=dict(color="rgba(248,113,113,0.8)", width=2, dash="dash"),
        annotation_text="Ruin → $0",
        annotation_position="top right",
        annotation_font=dict(color="#f87171", size=11),
    )

    # Median annotation
    p50 = float(np.percentile(nws, 50))
    fig.add_vline(
        x=p50,
        line=dict(color=_C["p50"], width=1.5, dash="dot"),
        annotation_text=f"Median: {_fmt_currency(p50)}",
        annotation_position="top left",
        annotation_font=dict(color=_C["p50"], size=11),
    )

    fail_count = sum(1 for v in nws if v <= 0)
    fail_pct   = fail_count / len(nws) * 100

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "showlegend": False,
        "height": 320,
        "yaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["yaxis"],
                  "tickprefix": "", "tickformat": ",d", "title": "# Trials"},
        "xaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["xaxis"],
                  "tickprefix": "$", "tickformat": ",.0f", "title": "Terminal Net Worth"},
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Chart 4: Ruin Year Histogram
# ---------------------------------------------------------------------------
def _ruin_year_chart(result: MonteCarloResult) -> go.Figure:
    ruin_yrs = [ry for ry in result.ruin_years if ry is not None]
    fig = go.Figure()

    if not ruin_yrs:
        fig.add_annotation(
            text="✅  No trials experienced portfolio ruin",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=16, color="#34d399"),
        )
        layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
        layout.update({
            "height": 280,
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
        })
        fig.update_layout(**layout)
        return fig

    fig.add_trace(go.Histogram(
        x=ruin_yrs,
        nbinsx=max(10, len(set(ruin_yrs))),
        marker=dict(color=_C["worst"], opacity=0.75),
        name="Year money runs out",
        hovertemplate="Year %{x}: %{y} trials<extra></extra>",
    ))

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "showlegend": False,
        "height": 280,
        "yaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["yaxis"],
                  "tickprefix": "", "tickformat": ",d", "title": "# Trials"},
        "xaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["xaxis"],
                  "tickprefix": "", "tickformat": "d", "title": "Calendar Year"},
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Configuration panel (shown before first run and always editable)
# ---------------------------------------------------------------------------
def _config_panel() -> html.Div:
    return html.Div([
        html.Div(
            html.Button(
                [html.Span("▶  Run Simulation", id="mc-btn-label")],
                id="btn-run-monte-carlo",
                className="btn-primary-custom",
                n_clicks=0,
                style={"width": "220px", "fontSize": "14px", "fontWeight": "700"},
            ),
            style={"display": "flex", "justifyContent": "flex-start"},
        ),

        # ── Progress bar (hidden until simulation starts) ─────────────────
        html.Div(
            [
                html.Div(
                    [
                        html.Span("Running simulation…", id="mc-progress-label",
                                  style={"fontSize": "12px", "color": "var(--text-secondary)", "fontWeight": "500"}),
                        html.Span("0%", id="mc-progress-pct",
                                  style={"fontSize": "12px", "fontWeight": "700", "color": "var(--accent-blue)"}),
                    ],
                    style={"display": "flex", "justifyContent": "space-between", "marginBottom": "6px"},
                ),
                dbc.Progress(
                    id="mc-progress-bar", value=0, max=100, animated=True, striped=True, color="primary",
                    style={"height": "10px", "borderRadius": "6px", "backgroundColor": "var(--bg-surface)"},
                ),
            ],
            id="mc-progress-container",
            style={"display": "none", "marginTop": "16px"},
        ),
    ])


# ---------------------------------------------------------------------------
# Results section (rendered from store data)
# ---------------------------------------------------------------------------
def _results_section(result: MonteCarloResult, retire_yr: int) -> html.Div:
    fan_fig   = _fan_chart(result, retire_yr)
    hist_fig  = _terminal_histogram(result)
    ruin_fig  = _ruin_year_chart(result)
    kpi_cards = _kpi_cards(result)

    # Summary strip
    fail_count = sum(1 for ry in result.ruin_years if ry is not None)
    strip = summary_row([
        ("Trials run",          f"{result.num_trials:,}",               "blue"),
        ("Success rate",        _fmt_pct(result.success_rate),          "green" if result.success_rate >= 0.8 else "amber"),
        ("Trials that fail",    f"{fail_count:,}  ({fail_count/result.num_trials*100:.1f}%)", "red"),
        ("Assumed equity mean", f"{result.mean_return_pct:.1f}% / yr",  "purple"),
        ("Assumed volatility",  f"{result.std_dev_pct:.1f}% std dev",   "amber"),
    ])

    return html.Div([
        html.Div(style={"height": "24px"}),

        # Fan chart (full width)
        section_card(
            "Net Worth Downside Risk Chart",
            subtitle="Shows where the bottom 50% of outcomes land. The amber zone is the moderate downside (10th–50th %ile); the red zone is the severe downside (5th–10th %ile).",
            children=[dcc.Graph(figure=fan_fig, config={"displayModeBar": False})],
        ),
        html.Div(style={"height": "16px"}),

        # KPI cards (now below the first chart)
        kpi_cards,
        html.Div(style={"height": "16px"}),

        # Distribution Row: Terminal NW + Ruin Year
        two_col(
            section_card(
                "Terminal Net Worth Distribution",
                subtitle="Final portfolio values across all trials.",
                children=[dcc.Graph(figure=hist_fig, config={"displayModeBar": False})],
            ),
            section_card(
                "Ruin Year Analysis",
                subtitle="When the portfolio runs out of money (if at all).",
                children=[dcc.Graph(figure=ruin_fig, config={"displayModeBar": False})],
            ),
        ),
        html.Div(style={"height": "16px"}),

        strip,
    ])


# ---------------------------------------------------------------------------
# Empty state (before first run)
# ---------------------------------------------------------------------------
def _empty_state() -> html.Div:
    return html.Div(
        [
            html.Div("🎲", style={"fontSize": "48px", "marginBottom": "12px"}),
            html.Div(
                "Run a simulation to see results.",
                style={"color": "var(--text-muted)", "fontSize": "14px"},
            ),
        ],
        style={
            "textAlign":    "center",
            "padding":      "80px 20px",
            "border":       "1px dashed var(--border-subtle)",
            "borderRadius": "var(--radius-md)",
            "marginTop":    "16px",
        },
    )


# ---------------------------------------------------------------------------
# Main layout function
# ---------------------------------------------------------------------------
def layout(
    profile_data: Optional[dict] = None,
    mc_data:      Optional[dict] = None,
) -> html.Div:
    """
    Render the full Monte Carlo page.

    Parameters
    ----------
    profile_data : serialised PlanProfile dict (from profile-store)
    mc_data      : serialised MonteCarloResult dict (from monte-carlo-store),
                   or None if no simulation has been run yet
    """
    from engine.models import PlanProfile
    from dataclasses import fields as dc_fields

    profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
    config  = profile.monte_carlo
    retire_yr = profile.retirement_year_self

    # ── Control panel ─────────────────────────────────────────────────────
    control_panel = html.Div(_config_panel(), style={"marginBottom": "24px"})

    # ── Results section ───────────────────────────────────────────────────
    if mc_data:
        result = MonteCarloResult(**mc_data)
        results_content = _results_section(result, retire_yr)
    else:
        results_content = _empty_state()

    return html.Div(
        [
            control_panel,
            dcc.Loading(
                id="mc-results-loading",
                type="circle",
                color="var(--accent-blue)",
                children=html.Div(id="mc-results-area", children=results_content),
            ),
        ]
    )
