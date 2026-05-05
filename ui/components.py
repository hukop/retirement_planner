"""
Reusable Dash UI components for the Retirement Planner.

All components return Dash/DBC elements and use CSS classes from assets/styles.css.
Components are pure functions — no callbacks here.

Component catalog
-----------------
  metric_card(...)          → Summary KPI card (4 per row on dashboard)
  section_card(...)         → Bordered card for grouping form inputs
  input_row(...)            → Labeled input with optional tooltip
  slider_row(...)           → Labeled slider with value display
  select_row(...)           → Labeled dropdown
  page_header(...)          → Page title + subtitle block
  info_badge(...)           → Colored tag/badge
  divider()                 → Horizontal rule
  dynamic_item(...)         → Collapsible item card used in add/remove lists
  add_button(...)           → "+ Add" button for dynamic lists
  empty_state(...)          → Placeholder shown when a list is empty
  two_col(left, right)      → Responsive 2-column grid
  three_col(a, b, c)        → Responsive 3-column grid
  four_col(a, b, c, d)      → 4 metric cards in a row
  summary_row(...)          → Key-value summary strip below a form section
"""

from __future__ import annotations

from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import html, dcc


# ---------------------------------------------------------------------------
# Color accent helpers
# ---------------------------------------------------------------------------
ACCENT_CLASSES = {
    "blue":   "accent-blue",
    "green":  "accent-green",
    "amber":  "accent-amber",
    "purple": "accent-purple",
    "red":    "accent-red",
    "teal":   "accent-teal",
}

BADGE_CLASSES = {
    "blue":   "badge-blue",
    "green":  "badge-green",
    "amber":  "badge-amber",
    "red":    "badge-red",
    "purple": "badge-purple",
}


# ---------------------------------------------------------------------------
# metric_card
# ---------------------------------------------------------------------------
def metric_card(
    title:    str,
    value:    str,
    subtitle: str = "",
    trend:    Optional[str] = None,       # e.g. "+12%", "-3%"
    trend_dir: str = "up",               # "up" | "down"
    icon:     str = "",                  # emoji icon
    accent:   str = "blue",             # "blue"|"green"|"amber"|"purple"
    card_id:  Optional[str] = None,
) -> dbc.Col:
    """
    A KPI summary card with large value, optional trend badge, and accent bar.

    Returns a ``dbc.Col`` (width=3) suitable for placing inside a ``dbc.Row``.
    """
    trend_el = []
    if trend:
        trend_el = [
            html.Span(
                trend,
                className=f"metric-card-trend {trend_dir}",
            )
        ]

    icon_el = html.Div(icon, className="metric-card-icon") if icon else html.Div()

    card_kwargs = dict(className="metric-card " + ACCENT_CLASSES.get(accent, ""))
    if card_id:
        card_kwargs["id"] = card_id

    return dbc.Col(
        html.Div(
            [
                icon_el,
                html.Div(title, className="metric-card-label"),
                html.Div(
                    [html.Span(value, className="metric-card-value")] + trend_el,
                    style={"display": "flex", "alignItems": "baseline", "gap": "0"},
                ),
                html.Div(subtitle, className="metric-card-subtitle"),
            ],
            **card_kwargs,
        ),
        xs=12, sm=6, md=3,
    )


# ---------------------------------------------------------------------------
# section_card
# ---------------------------------------------------------------------------
def section_card(
    title:    str,
    children: Any,
    subtitle: str = "",
    icon:     str = "",
    card_id:  Optional[str] = None,
) -> html.Div:
    """
    A card with a titled header and arbitrary children (form inputs, etc.).
    """
    header_content = (
        [html.Span(icon, className="card-icon"), title]
        if icon else [title]
    )

    kwargs: dict = {"className": "section-card"}
    if card_id:
        kwargs["id"] = card_id
        
    title_block = html.Div(header_content, className="section-card-title")
    if subtitle:
        title_block = html.Div([
            title_block,
            html.P(subtitle, className="section-card-subtitle")
        ])

    return html.Div(
        [
            title_block,
            html.Div(children),
        ],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# input_row
# ---------------------------------------------------------------------------
def input_row(
    label:        str,
    input_id:     str,
    input_type:   str = "number",
    value:        Any = None,
    placeholder:  str = "",
    tooltip:      str = "",
    min_val:      Optional[float] = None,
    max_val:      Optional[float] = None,
    step:         Optional[float] = None,
    prefix:       str = "",             # e.g. "$"
    suffix:       str = "",             # e.g. "%"
    debounce:     bool = True,
    disabled:     bool = False,
) -> html.Div:
    """
    A labeled numeric or text input with optional tooltip, prefix, and suffix.

    The ``input_id`` is used as the Dash component id and as the tooltip
    anchor id (suffixed with ``-tooltip``).
    """
    # Tooltip icon
    tooltip_el = []
    if tooltip:
        tooltip_el = [
            html.Span("?", className="help-icon", id=f"{input_id}-tip"),
            dbc.Tooltip(tooltip, target=f"{input_id}-tip", placement="top"),
        ]

    label_el = html.Div(
        [label] + tooltip_el,
        className="input-label",
    )

    inp_kwargs: dict = {
        "id":          input_id,
        "type":        input_type,
        "debounce":    debounce,
        "className":   "form-control",
        "disabled":    disabled,
    }
    if value is not None:
        inp_kwargs["value"] = value
    if placeholder:
        inp_kwargs["placeholder"] = placeholder
    if min_val is not None:
        inp_kwargs["min"] = min_val
    if max_val is not None:
        inp_kwargs["max"] = max_val
    if step is not None:
        inp_kwargs["step"] = step

    inp = dbc.Input(**inp_kwargs)

    # Wrap with prefix / suffix if provided
    if prefix or suffix:
        input_el = dbc.InputGroup(
            (
                ([dbc.InputGroupText(prefix)] if prefix else [])
                + [inp]
                + ([dbc.InputGroupText(suffix)] if suffix else [])
            ),
            size="sm",
        )
    else:
        input_el = inp

    return html.Div(
        [label_el, input_el],
        className="input-row",
    )


# ---------------------------------------------------------------------------
# slider_row
# ---------------------------------------------------------------------------
def slider_row(
    label:     str,
    slider_id: str,
    min_val:   float,
    max_val:   float,
    value:     float,
    step:      float = 1,
    marks:     Optional[dict] = None,
    tooltip:   str = "",
    suffix:    str = "",
) -> html.Div:
    """
    A labeled slider with a live value display to its right.

    The displayed numeric value reacts to slider position via a clientside
    callback wired in the layout (pattern-matching callback).
    """
    tooltip_el = []
    if tooltip:
        tooltip_el = [
            html.Span("?", className="help-icon", id=f"{slider_id}-tip"),
            dbc.Tooltip(tooltip, target=f"{slider_id}-tip", placement="top"),
        ]

    val_display = html.Span(
        f"{value:.0f}{suffix}",
        id=f"{slider_id}-display",
        style={"fontSize": "13px", "fontWeight": "600", "color": "var(--accent-blue)"},
    )

    return html.Div(
        [
            html.Div(
                [label] + tooltip_el + [html.Span(" — ", style={"color": "var(--text-muted)"}), val_display],
                className="input-label",
            ),
            dcc.Slider(
                id=slider_id,
                min=min_val,
                max=max_val,
                value=value,
                step=step,
                marks=marks or {int(v): str(int(v)) for v in
                                [min_val, (min_val + max_val) / 2, max_val]},
                tooltip={"placement": "bottom", "always_visible": False},
                className="mb-3",
            ),
        ],
        className="input-row",
    )


# ---------------------------------------------------------------------------
# select_row
# ---------------------------------------------------------------------------
def select_row(
    label:     str,
    select_id: str,
    options:   list[dict],           # [{"label": ..., "value": ...}, ...]
    value:     Any = None,
    tooltip:   str = "",
    clearable: bool = False,
) -> html.Div:
    """A labeled Dash dropdown / select."""
    tooltip_el = []
    if tooltip:
        tooltip_el = [
            html.Span("?", className="help-icon", id=f"{select_id}-tip"),
            dbc.Tooltip(tooltip, target=f"{select_id}-tip", placement="top"),
        ]

    return html.Div(
        [
            html.Div([label] + tooltip_el, className="input-label"),
            dbc.Select(
                id=select_id,
                options=options,
                value=value,
                className="form-control",
            ),
        ],
        className="input-row",
    )


# ---------------------------------------------------------------------------
# info_badge / tag
# ---------------------------------------------------------------------------
def info_badge(text: str, accent: str = "blue") -> html.Span:
    """Small colored pill badge."""
    return html.Span(text, className=f"tag {BADGE_CLASSES.get(accent, 'badge-blue')}")


# ---------------------------------------------------------------------------
# divider
# ---------------------------------------------------------------------------
def divider() -> html.Hr:
    """Thin horizontal rule matching the design system."""
    return html.Hr(className="divider")


# ---------------------------------------------------------------------------
# dynamic_item  (used inside add/remove lists)
# ---------------------------------------------------------------------------
def dynamic_item(
    item_index: int | str,
    title:      str,
    children:   Any,
    delete_id:  dict,          # Dash pattern-matching id dict for the delete button
    item_id:    Optional[dict] = None,
) -> html.Div:
    """
    A removable item card used in dynamic lists (income sources, accounts, etc.).

    Parameters
    ----------
    item_index : integer or string identifier shown in the title
    title      : display name for the item header
    children   : form inputs inside the item
    delete_id  : pattern-matching ID dict for the delete button
                 e.g. {"type": "del-income", "index": 0}
    item_id    : optional pattern-matching ID for the outer div
    """
    outer_kwargs: dict = {"className": "dynamic-item"}
    if item_id:
        outer_kwargs["id"] = item_id

    return html.Div(
        [
            html.Div(
                [
                    html.Div(title, className="dynamic-item-title"),
                    html.Button(
                        "✕ Remove",
                        id=delete_id,
                        className="btn-danger-ghost",
                        n_clicks=0,
                    ),
                ],
                className="dynamic-item-header",
            ),
            html.Div(children),
        ],
        **outer_kwargs,
    )


# ---------------------------------------------------------------------------
# add_button
# ---------------------------------------------------------------------------
def add_button(label: str, btn_id: str | dict) -> html.Div:
    """'+ Add ...' button row for dynamic lists."""
    return html.Div(
        html.Button(
            f"+ {label}",
            id=btn_id,
            className="btn-ghost",
            n_clicks=0,
        ),
        className="add-item-btn-row",
    )


# ---------------------------------------------------------------------------
# empty_state
# ---------------------------------------------------------------------------
def empty_state(message: str, icon: str = "📭") -> html.Div:
    """Placeholder shown when a dynamic list has no items."""
    return html.Div(
        [
            html.Div(icon, style={"fontSize": "32px", "marginBottom": "8px"}),
            html.Div(message, style={"color": "var(--text-muted)", "fontSize": "13px"}),
        ],
        style={
            "textAlign":    "center",
            "padding":      "32px 16px",
            "border":       "1px dashed var(--border-subtle)",
            "borderRadius": "var(--radius-md)",
        },
    )


# ---------------------------------------------------------------------------
# Layout grids
# ---------------------------------------------------------------------------
def two_col(left: Any, right: Any, left_width: int = 6) -> dbc.Row:
    """Two-column responsive layout."""
    right_width = 12 - left_width
    return dbc.Row(
        [
            dbc.Col(left,  xs=12, md=left_width),
            dbc.Col(right, xs=12, md=right_width),
        ],
        className="g-4",
    )


def three_col(a: Any, b: Any, c: Any) -> dbc.Row:
    """Three equal columns."""
    return dbc.Row(
        [dbc.Col(a, xs=12, md=4), dbc.Col(b, xs=12, md=4), dbc.Col(c, xs=12, md=4)],
        className="g-3",
    )


def four_col(a: Any, b: Any, c: Any, d: Any) -> dbc.Row:
    """Four metric card columns (use with metric_card which returns dbc.Col)."""
    return dbc.Row([a, b, c, d], className="g-3 mb-4")


# ---------------------------------------------------------------------------
# summary_row  (compact key-value strip below a section)
# ---------------------------------------------------------------------------
def summary_row(items: list[tuple[str, str, str]]) -> html.Div:
    """
    A horizontal strip of key-value pairs with colored values.

    Parameters
    ----------
    items : list of (label, value_str, accent_color)
            e.g. [("Total Balance", "$1,410,000", "green"), ...]
    """
    cols = []
    for label, value, accent in items:
        color = {
            "green":  "var(--accent-green)",
            "red":    "var(--accent-red)",
            "blue":   "var(--accent-blue)",
            "amber":  "var(--accent-amber)",
            "purple": "var(--accent-purple)",
        }.get(accent, "var(--text-primary)")

        cols.append(
            html.Div(
                [
                    html.Div(label,
                             style={"fontSize": "11px", "color": "var(--text-muted)",
                                    "textTransform": "uppercase", "letterSpacing": "0.7px",
                                    "marginBottom": "2px"}),
                    html.Div(value,
                             style={"fontSize": "15px", "fontWeight": "700", "color": color}),
                ],
                style={"flex": "1", "minWidth": "120px"},
            )
        )

    return html.Div(
        cols,
        style={
            "display":        "flex",
            "flexWrap":       "wrap",
            "gap":            "20px",
            "padding":        "16px 20px",
            "background":     "var(--bg-surface)",
            "border":         "1px solid var(--border-subtle)",
            "borderRadius":   "var(--radius-md)",
            "marginTop":      "16px",
        },
    )


# ---------------------------------------------------------------------------
# Plotly chart layout template (apply to all figures)
# ---------------------------------------------------------------------------
PLOTLY_DARK_TEMPLATE = {
    "layout": {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor":  "rgba(0,0,0,0)",
        "font":          {"family": "Inter, sans-serif", "color": "#94a3b8", "size": 12},
        "title":         {"font": {"color": "#f0f4ff", "size": 15, "family": "Inter"}},
        "xaxis": {
            "gridcolor":     "rgba(148,163,184,0.06)",
            "linecolor":     "rgba(148,163,184,0.12)",
            "tickcolor":     "rgba(148,163,184,0.12)",
            "tickfont":      {"size": 11},
            "showgrid":      True,
            "zeroline":      False,
        },
        "yaxis": {
            "gridcolor":     "rgba(148,163,184,0.06)",
            "linecolor":     "rgba(148,163,184,0.12)",
            "tickcolor":     "rgba(148,163,184,0.12)",
            "tickfont":      {"size": 11},
            "showgrid":      True,
            "zeroline":      False,
            "tickprefix":    "$",
            "tickformat":    ",.0f",
        },
        "legend": {
            "bgcolor":     "rgba(22,29,46,0.8)",
            "bordercolor": "rgba(148,163,184,0.15)",
            "borderwidth": 1,
            "font":        {"size": 12},
        },
        "hoverlabel": {
            "bgcolor":    "#161d2e",
            "bordercolor":"rgba(74,122,247,0.4)",
            "font":       {"size": 12, "family": "Inter"},
        },
        "colorway": [
            "#4a7af7",   # blue
            "#34d399",   # green
            "#fbbf24",   # amber
            "#a78bfa",   # purple
            "#f87171",   # red
            "#2dd4bf",   # teal
            "#fb923c",   # orange
        ],
        "margin": {"l": 56, "r": 20, "t": 48, "b": 48},
        "hovermode": "x unified",
    }
}

# Retirement year line annotation helper
def retirement_vline(
    retire_year: int,
    label: str = "Retirement",
) -> list[dict]:
    """
    Return a list containing a Plotly vertical-line shape and annotation
    for marking the retirement year on any chart.
    """
    return [
        {
            "type": "line",
            "x0": retire_year, "x1": retire_year,
            "y0": 0, "y1": 1,
            "xref": "x", "yref": "paper",
            "line": {"color": "rgba(251,191,36,0.5)", "width": 1.5, "dash": "dot"},
        }
    ], [
        {
            "x": retire_year,
            "y": 1,
            "xref": "x",
            "yref": "paper",
            "text": label,
            "showarrow": False,
            "font": {"size": 11, "color": "#fbbf24"},
            "bgcolor": "rgba(251,191,36,0.1)",
            "bordercolor": "rgba(251,191,36,0.3)",
            "borderwidth": 1,
            "borderpad": 4,
            "yanchor": "top",
        }
    ]
