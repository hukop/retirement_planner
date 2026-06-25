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
# noqa: E501
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
# KPI cards from result (returns a list of metric_card values for four_col)
# ---------------------------------------------------------------------------
def _kpi_card_values(result: MonteCarloResult) -> list:
    nws  = result.terminal_net_worths  # already sorted ascending
    p50  = float(np.percentile(nws, 50))
    p5   = float(np.percentile(nws, 5))
    p95  = float(np.percentile(nws, 95))
    rate = result.success_rate
    acc  = _success_accent(rate)

    return [
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
    ]


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
                   "orientation": "h", "y": -0.08, "x": 0},
        "height": 600,
        "uirevision": "constant",
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
    total = len(nws)

    # 1. Separate Ruin ($0) and Surviving (> $0) trials
    ruin_count = sum(1 for v in nws if v <= 0)
    ruin_pct = ruin_count / total * 100
    surviving_nws = [v for v in nws if v > 0]

    max_nw = max(nws) if nws else 1_000_000.0
    max_sqrt = np.sqrt(max(max_nw, 1_000_000.0))

    bin_w = max_sqrt / 39

    if ruin_count > 0:
        fig.add_trace(go.Bar(
            x=[-bin_w / 2],
            y=[ruin_pct],
            width=[bin_w],
            marker=dict(color=_C["worst"], line=dict(color="rgba(0,0,0,0)", width=0)),
            opacity=0.85,
            name="Ruin ($0)",
            hovertemplate=f"Ruin ($0): {ruin_pct:.1f}% ({ruin_count:,} trials)<extra></extra>",
        ))

    if surviving_nws:
        sqrt_surviving = [np.sqrt(v) for v in surviving_nws]

        # Uniform-width bins in sqrt space
        bin_edges_full = np.linspace(0, max_sqrt, 40)
        bin_w = bin_edges_full[1] - bin_edges_full[0]

        # Find which bin contains the 90th percentile; merge everything from there
        p90_val = np.percentile(surviving_nws, 90)
        p90_sqrt = np.sqrt(p90_val)
        merge_idx = np.searchsorted(bin_edges_full, p90_sqrt, side='right') - 1
        merge_idx = max(0, min(merge_idx, len(bin_edges_full) - 2))

        # Keep edges up to and including the merged bin (same uniform width)
        bin_edges = bin_edges_full[:merge_idx + 2]

        counts, _ = np.histogram(sqrt_surviving, bins=bin_edges)
        # Fold data above the last edge into the merged bin
        counts[-1] += sum(1 for v in sqrt_surviving if v > bin_edges[-1])
        counts_pct = counts / total * 100

        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        bin_widths = bin_edges[1:] - bin_edges[:-1]

        hover_texts = []
        for i in range(len(counts)):
            left_val = bin_edges[i] ** 2
            right_val = bin_edges[i+1] ** 2
            if i == len(counts) - 1:
                hover_texts.append(f"{_fmt_currency(p90_val)}+: {counts_pct[i]:.1f}% ({counts[i]:,} trials, top 10%)")
            else:
                hover_texts.append(f"{_fmt_currency(left_val)} - {_fmt_currency(right_val)}: {counts_pct[i]:.1f}% ({counts[i]:,} trials)")

        fig.add_trace(go.Bar(
            x=bin_centers,
            y=counts_pct,
            width=bin_widths,
            marker=dict(color=_C["p50"], line=dict(color="rgba(0,0,0,0)", width=0)),
            opacity=0.75,
            name="Surviving (> $0)",
            text=hover_texts,
            hovertemplate="%{text}<extra></extra>",
        ))

    # Ruin boundary (dashed line at exactly 0) — annotation above the plot
    fig.add_vline(
        x=0,
        line=dict(color="rgba(248,113,113,0.8)", width=2, dash="dash"),
    )
    fig.add_annotation(
        x=0, y=1, xref="x", yref="paper",
        text="Ruin → $0",
        showarrow=False,
        xanchor="left", yanchor="bottom",
        font=dict(color="#f87171", size=11),
    )

    # Median annotation
    p50 = float(np.percentile(nws, 50))
    if p50 > 0:
        p50_x = np.sqrt(p50)
        p50_text = f"Median: {_fmt_currency(p50)}"
    else:
        p50_x = -bin_w / 2
        p50_text = "Median: $0"

    fig.add_vline(
        x=p50_x,
        line=dict(color=_C["p50"], width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=p50_x, y=1, xref="x", yref="paper",
        text=p50_text,
        showarrow=False,
        xanchor="right", yanchor="bottom",
        font=dict(color=_C["p50"], size=11),
    )

    # Cap x-axis at the last (merged) bin's right edge
    if surviving_nws:
        x_max_sqrt = bin_edges[-1]
    else:
        x_max_sqrt = max_sqrt

    nice_ticks = [0, 1e6, 5e6, 1e7, 2.5e7, 5e7, 1e8, 2.5e8, 5e8, 1e9, 2.5e9, 5e9, 1e10]
    tickvals = []
    ticktext = []
    for val in nice_ticks:
        sqrt_val = np.sqrt(val)
        if sqrt_val <= x_max_sqrt + bin_w * 0.5:
            tickvals.append(sqrt_val)
            ticktext.append("$0" if val == 0 else _fmt_currency(val))

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "showlegend": True,
        "barmode": "overlay",
        "height": 340,
        "margin": {"t": 56, "r": 4},
        "legend": {
            **PLOTLY_DARK_TEMPLATE["layout"]["legend"],
            "orientation": "h",
            "x": 1, "y": 1.16,
            "xanchor": "right", "yanchor": "bottom",
        },
        "yaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["yaxis"],
                  "tickprefix": "", "tickformat": ".1f", "ticksuffix": "%", "title": "% of Trials",
                  "automargin": True, "range": [0, None]},
        "xaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["xaxis"],
                  "title": "Terminal Net Worth (Square Root Scale)",
                  "tickvals": tickvals,
                  "ticktext": ticktext,
                  "range": [-bin_w * 0.5, x_max_sqrt + bin_w * 0.8],
                  "showticklabels": True,
                  "tickmode": "array",
                  "tickprefix": "",
                  "ticksuffix": ""},
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

    # Calculate histogram manually for maximum stability across Plotly versions
    num_bins = max(10, min(50, len(set(ruin_yrs))))
    counts, bin_edges = np.histogram(ruin_yrs, bins=num_bins)

    # Convert to percentages of total trials
    pcts = (counts / max(1, result.num_trials)) * 100.0
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    fig.add_trace(go.Bar(
        x=bin_centers,
        y=pcts,
        marker=dict(color=_C["worst"], opacity=0.75),
        name="Year money runs out",
        hovertemplate="Year %{x:.0f}: %{y:.1f}% of total trials<extra></extra>",
    ))

    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "showlegend": False,
        "height": 280,
        "yaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["yaxis"],
                  "tickprefix": "", "tickformat": ".1f", "ticksuffix": "%", "title": "% of Total Trials"},
        "xaxis": {**PLOTLY_DARK_TEMPLATE["layout"]["xaxis"],
                  "tickprefix": "", "tickformat": "d", "title": "Calendar Year"},
        "bargap": 0.1,
    })
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Configuration panel (shown before first run and always editable)
# ---------------------------------------------------------------------------
def _config_panel(config: MonteCarloConfig) -> html.Div:
    live_update_value = ["on"] if getattr(config, "live_updates", True) else []
    adaptive_value = ["on"] if getattr(config, "adaptive_spending", False) else []

    return html.Div([
        dbc.Row([
            dbc.Col(
                html.Button(
                    [html.Span("▶  Run Simulation", id="mc-btn-label")],
                    id="btn-run-monte-carlo",
                    className="btn-primary-custom",
                    n_clicks=0,
                    style={"width": "200px", "fontSize": "14px", "fontWeight": "700"},
                ),
                width="auto",
            ),
            dbc.Col(
                [
                    html.Span("using", style={"fontSize": "13px", "color": "var(--text-muted)", "margin": "0 10px 0 15px"}),
                    dbc.Select(
                        id="mc-input-num-trials",
                        options=[
                            {"label": "100 trials", "value": "100"},
                            {"label": "500 trials", "value": "500"},
                            {"label": "1,000 trials", "value": "1000"},
                            {"label": "2,500 trials", "value": "2500"},
                            {"label": "5,000 trials", "value": "5000"},
                        ],
                        value=str(config.num_trials),
                        size="sm",
                        style={"width": "120px", "display": "inline-block", "backgroundColor": "var(--bg-input)", "borderColor": "var(--border-input)", "color": "var(--text-primary)", "fontSize": "12px"}
                    ),
                ],
                className="d-flex align-items-center",
                width="auto",
            ),
            dbc.Col(
                dcc.Checklist(
                    id="mc-input-live-updates",
                    options=[{"label": " Live Updates", "value": "on"}],
                    value=live_update_value,
                    inputStyle={"marginRight": "8px"},
                    style={"fontSize": "13px", "color": "var(--text-secondary)", "marginLeft": "20px"},
                ),
                className="d-flex align-items-center",
                width="auto",
            ),
            dbc.Col(
                dcc.Checklist(
                    id="mc-input-adaptive-spending",
                    options=[{"label": " Adaptive Spending", "value": "on"}],
                    value=adaptive_value,
                    inputStyle={"marginRight": "8px"},
                    style={"fontSize": "13px", "color": "var(--text-secondary)", "marginLeft": "20px"},
                ),
                className="d-flex align-items-center",
                width="auto",
            ),
        ], align="center", className="g-0"),

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

    # Summary strip
    fail_count = sum(1 for ry in result.ruin_years if ry is not None)
    strip = summary_row([
        ("Trials run",          f"{result.num_trials:,}",               "blue"),
        ("Success rate",        _fmt_pct(result.success_rate),          "green" if result.success_rate >= 0.8 else "amber"),
        ("Trials that fail",    f"{fail_count:,}  ({fail_count/result.num_trials*100:.1f}%)", "red"),
        ("Per-account returns",  "Set on Investments tab",              "purple"),
        ("Per-account volatility", "Set on Investments tab",            "amber"),
    ])

    return html.Div([
        html.Div(style={"height": "24px"}),

        # Fan chart
        section_card(
            "Net Worth Downside Risk Chart",
            children=[dcc.Graph(id="mc-fan-chart", figure=fan_fig,
                                config={"displayModeBar": False})],
        ),
        html.Div(style={"height": "16px"}),

        # KPI cards
        html.Div(id="mc-kpi-cards", children=four_col(*_kpi_card_values(result))),
        html.Div(style={"height": "16px"}),

        # Distribution Row: Terminal NW + Ruin Year
        two_col(
            section_card(
                "Terminal Net Worth Distribution",
                subtitle="Final portfolio values across all trials.",
                children=[html.Div(
                    dcc.Graph(id="mc-histogram", figure=hist_fig, config={"displayModeBar": False}),
                    style={"marginTop": "-40px"},
                )],
            ),
            section_card(
                "Ruin Year Analysis",
                subtitle="When the portfolio runs out of money (if at all).",
                children=[dcc.Graph(id="mc-ruin-chart", figure=ruin_fig, config={"displayModeBar": False})],
            ),
        ),
        html.Div(style={"height": "16px"}),

        html.Div(id="mc-summary-strip", children=strip),
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
    control_panel = html.Div(_config_panel(config), style={"marginBottom": "24px"})

    # ── Results section ───────────────────────────────────────────────────
    if mc_data:
        result = MonteCarloResult(**mc_data)
        results_content = _results_section(result, retire_yr)
    else:
        results_content = _empty_state()

    return html.Div(
        [
            control_panel,
            html.Div(id="mc-results-area", children=results_content),
        ]
    )
