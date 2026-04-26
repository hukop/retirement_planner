"""
Investments page.

Contains:
1. Investment Accounts — dynamic list of tax-deferred, tax-free, and taxable accounts.
2. Portfolio Overview — pie chart showing breakdown by account type.
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

_OWNER_OPTIONS = [
    {"label": "Self", "value": "self"},
    {"label": "Spouse", "value": "spouse"},
    {"label": "Joint", "value": "joint"},
]

_ACCOUNT_TYPE_OPTIONS = [
    {"label": "Pre-Tax (401k/403b)", "value": "401k"},
    {"label": "Traditional IRA", "value": "trad_ira"},
    {"label": "Roth IRA", "value": "roth_ira"},
    {"label": "Taxable Brokerage", "value": "brokerage"},
    {"label": "HSA", "value": "hsa"},
    {"label": "Cash / Savings", "value": "savings"},
]

def _investment_item(idx: int, acc: dict) -> html.Div:
    """Render a single investment account block."""
    acc_type = acc.get("account_type", "brokerage")
    
    return dynamic_item(
        item_index=idx,
        title=acc.get("name", "New Account"),
        delete_id={"type": "btn-delete-account", "index": idx},
        item_id={"type": "account-item", "index": idx},
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Name",
                            input_id={"type": "acc-name", "index": idx},
                            input_type="text",
                            value=acc.get("name", ""),
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        select_row(
                            label="Account Type",
                            select_id={"type": "acc-type", "index": idx},
                            options=_ACCOUNT_TYPE_OPTIONS,
                            value=acc_type,
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        select_row(
                            label="Owner",
                            select_id={"type": "acc-owner", "index": idx},
                            options=_OWNER_OPTIONS,
                            value=acc.get("owner", "joint"),
                        ),
                        xs=12, md=4,
                    ),
                ],
                className="g-3"
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Current Balance",
                            input_id={"type": "acc-balance", "index": idx},
                            value=acc.get("balance", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        input_row(
                            label="Expected Return",
                            input_id={"type": "acc-return", "index": idx},
                            value=acc.get("annual_return_pct", 7.0),
                            suffix="%",
                            step=0.1,
                            tooltip="Average annual estimated growth. (e.g. 7% for stocks)"
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        input_row(
                            label="Annual Contribution",
                            input_id={"type": "acc-contrib", "index": idx},
                            value=acc.get("annual_contribution", 0),
                            prefix="$",
                            min_val=0,
                        ),
                        xs=12, md=4,
                    ),
                ],
                className="g-3"
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Cost Basis",
                            input_id={"type": "acc-cost-basis", "index": idx},
                            value=acc.get("cost_basis", 0),
                            prefix="$",
                            min_val=0,
                            tooltip="Only required for taxable brokerage accounts to calculate capital gains properly."
                        ),
                        xs=12, md=4,
                    ),
                    dbc.Col(
                        input_row(
                            label="Employer Match %",
                            input_id={"type": "acc-match", "index": idx},
                            value=acc.get("employer_match_pct", 0),
                            suffix="%",
                            step=0.1,
                            tooltip="Employer 401k match based on your salary."
                        ),
                        xs=12, md=4,
                    ),
                ],
                className="g-3 pt-2 pb-1"
            ),
        ]
    )

def _portfolio_breakdown_chart(profile: PlanProfile) -> go.Figure:
    """Pie chart showing account balances clustered by tax characteristics."""
    labels = ["Tax-Deferred (401k/Trad)", "Tax-Free (Roth/HSA)", "Taxable (Brokerage/Bank)"]
    values = [0, 0, 0]
    
    for acc in profile.accounts:
        if acc.account_type in ("401k", "trad_ira"):
            values[0] += acc.balance
        elif acc.account_type in ("roth_ira", "hsa"):
            values[1] += acc.balance
        else:
            values[2] += acc.balance
            
    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.6,
        marker=dict(
            colors=["#fbbf24", "#34d399", "#4a7af7"], # amber, green, blue
            line=dict(color="var(--bg-card)", width=2)
        ),
        textinfo="label+percent",
        textfont=dict(size=11, family="Inter"),
        hovertemplate="%{label}: $%{value:,.0f}<extra></extra>",
    ))
    
    total = sum(values)
    fig.add_annotation(
        text=f"${total:,.0f}<br><span style='font-size:10px'>Total Nest Egg</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=15, color="var(--text-primary)", family="Inter"),
        align="center",
    )
    
    layout = dict(PLOTLY_DARK_TEMPLATE["layout"])
    layout.update({
        "title": {"text": "Tax Liability Distribution", "x": 0.02, "xanchor": "left"},
        "height": 280,
        "margin": {"l": 10, "r": 10, "t": 40, "b": 10},
        "showlegend": False,
    })
    fig.update_layout(**layout)
    return fig

def layout(profile_data: Optional[dict] = None) -> html.Div:
    """Render the investments page."""
    profile = PlanProfile.from_dict(profile_data) if profile_data else PlanProfile.sample()
    
    # ── Accounts List ───────────────────────────────────────────────────
    accounts_list = []
    if not profile.accounts:
        accounts_list.append(empty_state("No investment accounts setup.", "📈"))
    else:
        for idx, acc in enumerate(profile.accounts):
            accounts_list.append(_investment_item(idx, acc.__dict__))

    accounts_section = section_card(
        title="📈  Investment Accounts",
        children=[
            html.Div(accounts_list, id="accounts-container"),
            add_button("Add Account", btn_id="btn-add-account")
        ]
    )
    
    # ── Portfolio Chart Section ─────────────────────────────────────────
    chart_section = section_card(
        title="💼  Portfolio Overview",
        children=[
            dcc.Graph(
                id="portfolio-breakdown-chart",
                figure=_portfolio_breakdown_chart(profile),
                config={"displayModeBar": False, "responsive": True},
            )
        ]
    )

    # ── Summary Strip ───────────────────────────────────────────────────
    total_balance = sum(a.balance for a in profile.accounts)
    total_contribs = sum(a.annual_contribution for a in profile.accounts)
    
    strip = summary_row([
        ("Total Net Worth (Investments)", f"${total_balance:,.0f}", "blue"),
        ("Planned Annual Savings", f"${total_contribs:,.0f}", "green"),
        ("Number of Accounts", f"{len(profile.accounts)} total", "purple"),
    ])

    return html.Div(
        [
            page_header(
                "Investments",
                subtitle="Map out your stock portfolios, IRAs, and savings mapping toward your nest egg.",
                icon="📈",
            ),
            two_col(accounts_section, chart_section, left_width=8),
            strip,
        ]
    )
