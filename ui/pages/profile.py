"""
Profile & Settings page.

Two-column form for both people + global plan settings.

Left column  — Your info (name, age, retirement age, life expectancy, SS)
Right column — Spouse info (same fields)
Bottom row   — Filing status dropdown, inflation rate slider

All input component IDs follow the convention:
  profile-{owner}-{field}
  e.g. profile-self-name, profile-spouse-retirement-age

These IDs are referenced by the persistence callbacks in Phase 13.
"""

from __future__ import annotations

from typing import Optional

import dash_bootstrap_components as dbc
from dash import html, dcc

from engine.models import PlanProfile, Person, MonteCarloConfig
from ui.components import (
    section_card, input_row, slider_row, select_row,
    two_col, divider, summary_row, info_badge,
)


# ---------------------------------------------------------------------------
# Filing status options
# ---------------------------------------------------------------------------
_FILING_OPTIONS = [
    {"label": "Married Filing Jointly",   "value": "married_jointly"},
    {"label": "Married Filing Separately", "value": "married_separately"},
    {"label": "Single",                   "value": "single"},
]


# ---------------------------------------------------------------------------
# SS claiming age marks
# ---------------------------------------------------------------------------
_SS_MARKS = {age: str(age) for age in range(62, 71)}


# ---------------------------------------------------------------------------
# Person form block
# ---------------------------------------------------------------------------
def _person_form(owner: str, person: Person, label: str) -> html.Div:
    """Build the inputs for one person (self or spouse)."""
    pfx = f"profile-{owner}"

    # SS benefit display based on the claiming age
    ss_at_fra  = person.ss_monthly_benefit
    from engine.social_security import fra_in_years, adjusted_monthly_benefit
    from datetime import date

    curr_age = person.current_age
    claim_age = float(person.ss_claiming_age or 67)

    birth_yr   = person.birth_year
    fra        = fra_in_years(birth_yr)
    adj_benefit = adjusted_monthly_benefit(
        ss_at_fra, claim_age, birth_yr
    )

    return section_card(
        title=f"{'👤' if owner == 'self' else '👥'}  {label}",
        children=[
            input_row(
                label="Name",
                input_id=f"{pfx}-name",
                input_type="text",
                value=person.name,
                placeholder="Full name",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Birth Year",
                            input_id=f"{pfx}-birth-year",
                            value=person.birth_year,
                            min_val=1920, max_val=2026, step=1,
                            tooltip="Year you were born.",
                        ),
                        xs=12, md=8,
                    ),
                    dbc.Col(
                        input_row(
                            label="Month",
                            input_id=f"{pfx}-birth-month",
                            value=person.birth_month,
                            min_val=1, max_val=12, step=1,
                            tooltip="Month of birth (1-12).",
                        ),
                        xs=12, md=4,
                    ),
                ],
            ),
            dbc.Row(
                [
                    dbc.Col(
                        html.Div([
                            html.Span("Computed Age: ", style={"color": "var(--text-secondary)", "fontSize": "12px"}),
                            html.Span(f"{curr_age} yrs", style={"color": "var(--accent-blue)", "fontWeight": "600", "fontSize": "13px"}),
                        ], style={"padding": "4px 0 12px 0"}),
                        width=12
                    )
                ]
            ),
            dbc.Row(
                [
                    dbc.Col(
                        input_row(
                            label="Retirement Year",
                            input_id=f"{pfx}-retirement-year",
                            value=person.retirement_year,
                            min_val=2024, max_val=2100, step=1,
                            tooltip="The year you plan to stop working.",
                        ),
                        xs=12, md=6,
                    ),
                    dbc.Col(
                        html.Div([
                            html.Span("Retirement Age: ", style={"color": "var(--text-secondary)", "fontSize": "12px"}),
                            html.Span(f"{person.retirement_age} yrs", style={"color": "var(--accent-blue)", "fontWeight": "600", "fontSize": "13px"}),
                        ], style={"padding": "36px 0 0 0"}),
                        xs=12, md=6,
                    ),
                ],
                className="g-3",
            ),
            input_row(
                label="Life Expectancy",
                input_id=f"{pfx}-life-expectancy",
                value=person.life_expectancy,
                min_val=65, max_val=110, step=1,
                tooltip="Planning horizon — how long to model the plan.",
                suffix=" yrs",
            ),

            divider(),

            # Social Security
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("Social Security", style={"fontWeight": "600"}),
                            html.Span(" "),
                            info_badge("Federal", "blue"),
                        ],
                        style={"fontSize": "13px", "marginBottom": "14px",
                               "display": "flex", "alignItems": "center", "gap": "8px"},
                    ),
                    input_row(
                        label="Estimated Monthly Benefit at FRA",
                        input_id=f"{pfx}-ss-benefit",
                        value=person.ss_monthly_benefit,
                        min_val=0, max_val=5000, step=50,
                        prefix="$",
                        tooltip=(
                            "Your estimated SS benefit at Full Retirement Age (FRA). "
                            "Find this on your SSA.gov statement."
                        ),
                    ),
                    slider_row(
                        label="Claiming Age",
                        slider_id=f"{pfx}-ss-claiming-age",
                        min_val=62, max_val=70,
                        value=person.ss_claiming_age,
                        step=1,
                        marks=_SS_MARKS,
                        tooltip=(
                            "Age when you'll start receiving SS benefits. "
                            "Claiming before FRA permanently reduces your benefit; "
                            "delaying past FRA (up to 70) increases it."
                        ),
                        suffix=" yrs",
                    ),
                    # Adjusted benefit preview
                    html.Div(
                        [
                            html.Span(
                                "Adjusted monthly benefit: ",
                                style={"color": "var(--text-muted)", "fontSize": "12px"},
                            ),
                            html.Span(
                                f"${adj_benefit:,.0f} / mo",
                                id=f"{pfx}-ss-adjusted-preview",
                                style={
                                    "color":      "var(--accent-green)",
                                    "fontWeight": "600",
                                    "fontSize":   "13px",
                                },
                            ),
                            html.Span(
                                f"  (FRA = {fra:.1f})",
                                style={"color": "var(--text-muted)", "fontSize": "11px"},
                            ),
                        ],
                        style={"marginTop": "4px", "marginBottom": "4px"},
                    ),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Global settings block
# ---------------------------------------------------------------------------
def _global_settings(profile: PlanProfile) -> html.Div:
    return section_card(
        title="⚙️  Plan Settings",
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        select_row(
                            label="Filing Status",
                            select_id="profile-filing-status",
                            options=_FILING_OPTIONS,
                            value=profile.filing_status,
                            tooltip=(
                                "Tax filing status. Affects federal and CA "
                                "bracket thresholds and standard deduction."
                            ),
                        ),
                        xs=12, md=6,
                    ),
                    dbc.Col(
                        slider_row(
                            label="Annual Inflation Rate",
                            slider_id="profile-inflation-rate",
                            min_val=1.0, max_val=6.0,
                            value=profile.inflation_rate_pct,
                            step=0.25,
                            marks={v: f"{v}%" for v in [1, 2, 3, 4, 5, 6]},
                            tooltip=(
                                "Applied to expenses and as COLA for Social Security. "
                                "Historical US average is ~3%."
                            ),
                            suffix="%",
                        ),
                        xs=12, md=6,
                    ),
                ],
                className="g-4",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Monte Carlo settings block
# ---------------------------------------------------------------------------
def _monte_carlo_settings(mc: MonteCarloConfig) -> html.Div:
    return section_card(
        title="🎲  Monte Carlo Simulation Settings",
        subtitle="Return rates and volatility are now configured per-account on the Investments tab.",
        children=[
            dbc.Row([
                dbc.Col([
                    input_row(
                        "Random Seed (optional)",
                        "profile-mc-seed",
                        input_type="number",
                        value=mc.random_seed,
                        placeholder="Leave blank for random",
                        min_val=0,
                        step=1,
                        tooltip="Set a fixed seed to get the same results every run.",
                    ),
                ], xs=12, md=6),
                dbc.Col([
                    html.Div(
                        [
                            html.Span("📊  Per-account returns & volatility",
                                      style={"fontSize": "13px", "color": "var(--text-secondary)",
                                             "fontWeight": "500"}),
                            html.Br(),
                            html.Span(
                                "Set the Annual Return % and Volatility % for each account on the Investments tab.",
                                style={"fontSize": "12px", "color": "var(--text-muted)"},
                            ),
                        ],
                        style={"padding": "12px", "borderRadius": "8px",
                               "background": "var(--bg-surface)", "marginTop": "4px"},
                    ),
                ], xs=12, md=6),
            ], className="g-4"),
        ],
    )


# ---------------------------------------------------------------------------
# Main layout function
# ---------------------------------------------------------------------------
def _profile_summary(profile: PlanProfile) -> html.Div:
    """Helper to build the summary strip for the profile page."""
    from engine.social_security import adjusted_monthly_benefit
    from datetime import date

    current_year = date.today().year
    birth_yr_self   = profile.self_person.birth_year
    birth_yr_spouse = profile.spouse.birth_year

    adj_self   = adjusted_monthly_benefit(
        float(profile.self_person.ss_monthly_benefit or 0),
        float(profile.self_person.ss_claiming_age or 67), birth_yr_self,
    )
    adj_spouse = adjusted_monthly_benefit(
        float(profile.spouse.ss_monthly_benefit or 0),
        float(profile.spouse.ss_claiming_age or 67), birth_yr_spouse,
    )

    y_to_ret_slf = max(0, int(profile.self_person.retirement_age or 0) - profile.self_person.current_age)
    y_to_ret_sp  = max(0, int(profile.spouse.retirement_age or 0) - profile.spouse.current_age)

    return summary_row([
        ("Years to retirement (you)",    f"{y_to_ret_slf} yrs",  "blue"),
        ("Years to retirement (spouse)", f"{y_to_ret_sp} yrs",  "blue"),
        ("Your SS at claim",             f"${adj_self:,.0f}/mo",   "green"),
        ("Spouse SS at claim",           f"${adj_spouse:,.0f}/mo", "green"),
    ])

def layout(profile_data: Optional[dict] = None) -> html.Div:
    """Render the full profile page."""
    if profile_data:
        profile = PlanProfile.from_dict(profile_data)
    else:
        profile = PlanProfile.sample()

    from datetime import date
    # Build the two-column person forms
    people_row = two_col(
        _person_form("self",   profile.self_person, f"You — {profile.self_person.name}"),
        _person_form("spouse", profile.spouse,       f"Spouse — {profile.spouse.name}"),
    )

    return html.Div(
        [
            html.P("Enter your personal details. All data is saved locally — never leaves your computer.",
                   style={"fontSize": "14px", "color": "var(--text-secondary)", "marginBottom": "24px"}),
            people_row,
            html.Div(style={"height": "4px"}),
            _global_settings(profile),
            html.Div(style={"height": "4px"}),
            _monte_carlo_settings(profile.monte_carlo),
            html.Div(
                _profile_summary(profile),
                id="profile-summary-container"
            ),
            # Reset button row (save is automatic)
            html.Div(
                [
                    html.Button(
                        "↺  Reset to Defaults",
                        id="profile-reset-btn",
                        className="btn-ghost",
                        n_clicks=0,
                    ),
                ],
                style={"marginTop": "20px", "display": "flex", "alignItems": "center"},
            ),
        ]
    )
