"""
Top-level app layout and URL routing.

Structure
---------
  #app-shell
  ├── dcc.Location         (URL router)
  ├── dcc.Store('profile-store')     — active PlanProfile as JSON dict
  ├── dcc.Store('projection-store')  — annual_df as list-of-records
  ├── #sidebar
  │   ├── logo
  │   ├── Dashboard link
  │   ├── Collapsible "My Plan" section
  │   │   ├── Profile, Income, Expenses, Investments, Real Estate
  │   └── Collapsible "Insights" section
  │       ├── Projections, Monte Carlo, Roth Conversion
  └── #main-content
      ├── #topbar  (title + save/load buttons)
      └── #page-content  (routed page output)

Routing
-------
The ``register_routing_callback(app)`` function wires a single callback that
reads ``dcc.Location.pathname`` and renders the matching page layout.
Add new pages by extending ``_ROUTES``.
"""

from __future__ import annotations

import dash
from dash import html, dcc, Input, Output, State, clientside_callback
import dash_bootstrap_components as dbc

from engine.models import PlanProfile


# ---------------------------------------------------------------------------
# Navigation structure
# ---------------------------------------------------------------------------
_DASHBOARD = ("/", "📊", "Dashboard")

_MY_PLAN = [
    ("/profile",      "👤", "Profile"),
    ("/income",       "💼", "Income"),
    ("/expenses",     "🛒", "Expenses"),
    ("/investments",  "📈", "Investments"),
    ("/real-estate",  "🏠", "Real Estate"),
]

_INSIGHTS = [
    ("/projections",      "🔮", "Projections"),
    ("/monte-carlo",      "🎲", "Monte Carlo"),
    ("/roth-conversion",  "🔄", "Roth Conversion"),
]


def _nav_link(href: str, icon: str, label: str) -> dcc.Link:
    return dcc.Link(
        [
            html.Span(icon, className="nav-icon"),
            html.Span(label),
        ],
        href=href,
        className="nav-item-link",
        id=f"nav-{label.lower().replace(' ', '-')}",
    )


def _sidebar() -> html.Div:
    return html.Div(
        [
            # Logo / brand
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("🏦", className="logo-icon"),
                            html.Div(
                                [
                                    html.Div("FinancePlan", style={"lineHeight": "1.1"}),
                                    html.Div(
                                        "Personal — Local — Private",
                                        style={
                                            "fontSize": "9px",
                                            "color":    "var(--text-muted)",
                                            "fontWeight": "400",
                                            "letterSpacing": "0.5px",
                                        },
                                    ),
                                ]
                            ),
                        ],
                        id="sidebar-logo",
                    ),
                ],
                id="sidebar-header",
            ),
            # Navigation
            html.Div(
                [
                    # Dashboard (always visible at top)
                    _nav_link(_DASHBOARD[0], _DASHBOARD[1], _DASHBOARD[2]),

                    html.Div(className="sidebar-divider"),

                    # My Plan (collapsible via dbc.Collapse)
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Span("▸", className="collapse-arrow", id="sidebar-my-plan-arrow"),
                                    html.Span("My Plan", className="nav-section-label-inline"),
                                ],
                                className="nav-section-toggle",
                                id="sidebar-toggle-my-plan",
                                n_clicks=0,
                            ),
                            dbc.Collapse(
                                html.Div(
                                    [_nav_link(href, icon, label) for href, icon, label in _MY_PLAN],
                                    className="nav-section-items",
                                ),
                                id="sidebar-collapse-my-plan",
                                is_open=True,
                            ),
                        ],
                        className="nav-collapsible-section",
                    ),

                    html.Div(className="sidebar-divider"),

                    # Insights (collapsible via dbc.Collapse)
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Span("▸", className="collapse-arrow", id="sidebar-insights-arrow"),
                                    html.Span("Insights", className="nav-section-label-inline"),
                                ],
                                className="nav-section-toggle",
                                id="sidebar-toggle-insights",
                                n_clicks=0,
                            ),
                            dbc.Collapse(
                                html.Div(
                                    [_nav_link(href, icon, label) for href, icon, label in _INSIGHTS],
                                    className="nav-section-items",
                                ),
                                id="sidebar-collapse-insights",
                                is_open=False,
                            ),
                        ],
                        className="nav-collapsible-section",
                    ),

                    html.Div(className="sidebar-divider"),

                    # Settings at bottom
                    html.Div("SETTINGS", className="nav-section-label", style={"marginTop": "8px"}),
                    html.Div(
                        dbc.Select(
                            id="layout-density-select",
                            options=[
                                {"label": "Comfortable View", "value": "comfortable"},
                                {"label": "Compact View", "value": "compact"},
                                {"label": "Dense View", "value": "dense"},
                            ],
                            value="comfortable",
                            size="sm",
                            style={"backgroundColor": "var(--bg-input)", "color": "var(--text-secondary)", "borderColor": "var(--border-input)", "fontSize": "12px", "padding": "4px 8px"}
                        ),
                        style={"padding": "0 12px", "marginBottom": "12px"}
                    ),
                    html.Div(
                        dbc.Select(
                            id="layout-theme-select",
                            options=[
                                {"label": "Classic Dashboard", "value": "classic"},
                                {"label": "Notion (Minimalist)", "value": "notion"},
                                {"label": "Spreadsheet (Dense)", "value": "spreadsheet"},
                            ],
                            value="classic",
                            size="sm",
                            style={"backgroundColor": "var(--bg-input)", "color": "var(--text-secondary)", "borderColor": "var(--border-input)", "fontSize": "12px", "padding": "4px 8px"}
                        ),
                        style={"padding": "0 12px", "marginBottom": "12px"}
                    ),
                ],
                id="sidebar-nav",
            ),
            # Footer
            html.Div(
                html.Div(
                    "MVP v0.1 · Data stays local",
                    style={
                        "fontSize":   "10px",
                        "color":      "var(--text-muted)",
                        "padding":    "12px 16px",
                        "borderTop":  "1px solid var(--border-subtle)",
                    },
                )
            ),
        ],
        id="sidebar",
    )


def _topbar() -> html.Div:
    return html.Div(
        [
            # Left: breadcrumb / current-page title
            html.Div(id="topbar-title", style={"fontWeight": "600", "fontSize": "14px"}),
            # Right: actions
            html.Div(
                [
                    dcc.Download(id="download-plan"),
                    html.Button(
                        "💾  Save Plan",
                        id="btn-save-plan",
                        className="btn-ghost",
                        n_clicks=0,
                    ),
                    html.Button(
                        "💾✏️  Save Plan as ...",
                        id="btn-save-plan-as",
                        className="btn-ghost",
                        n_clicks=0,
                        style={"marginLeft": "8px"},
                    ),
                    dcc.Upload(
                        id="upload-plan",
                        children=html.Button(
                            "📂  Load Plan",
                            id="btn-load-plan",
                            className="btn-ghost",
                            n_clicks=0,
                            style={"marginLeft": "8px"},
                        ),
                        multiple=False,
                        accept=".json",
                        disable_click=True,
                    ),
                    html.Button(
                        "▶  Run Projections",
                        id="btn-run-projections",
                        className="btn-primary-custom",
                        n_clicks=0,
                        style={"marginLeft": "12px", "display": "flex", "alignItems": "center"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
        ],
        id="topbar",
    )


# ---------------------------------------------------------------------------
# Root layout
# ---------------------------------------------------------------------------
def create_layout() -> html.Div:
    """
    Build the top-level app layout.

    Includes URL router, global stores, sidebar, topbar, and the
    page-content container that the routing callback populates.
    """
    # Initialize stores with sample profile so charts render immediately
    initial_profile = PlanProfile.sample().to_dict()

    return html.Div(
        [
            # URL router
            dcc.Location(id="url", refresh=False),

            # Global data stores
            dcc.Store(id="profile-store",      data=initial_profile, storage_type="session"),
            dcc.Store(id="projection-store",   data=None,            storage_type="session"),
            dcc.Store(id="monte-carlo-store",  data=None,            storage_type="session"),
            dcc.Store(id="roth-conversion-store", data=None,         storage_type="session"),
            dcc.Store(id="plan-file-store",       data=None,         storage_type="session"),
            dcc.Store(id="density-store",                            storage_type="local"),
            dcc.Store(id="theme-store",                              storage_type="local"),

            # Sidebar collapse stores — placed here (outside sidebar) so they
            # persist across sidebar re-renders triggered by nav-link callbacks.
            dcc.Store(id="sidebar-my-plan-open",    data=True,  storage_type="session"),
            dcc.Store(id="sidebar-insights-open",   data=False, storage_type="session"),

            # Toast notification area
            html.Div(id="toast-container", style={
                "position": "fixed", "top": "70px", "right": "20px", "zIndex": "9999",
            }),

            # App shell
            html.Div(
                [
                    _sidebar(),
                    html.Div(
                        [
                            _topbar(),
                            dcc.Loading(
                                html.Div(id="page-content", style={"padding": "28px"}),
                                id="page-loading",
                                type="dot",
                                color="#4a7af7",
                                fullscreen=False,
                            ),
                        ],
                        id="main-content",
                    ),
                ],
                id="app-shell",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Routing callback
# ---------------------------------------------------------------------------
_ROUTES: dict[str, str] = {
    "/":             "dashboard",
    "/dashboard":    "dashboard",
    "/profile":      "profile",
    "/income":       "income",
    "/expenses":     "expenses",
    "/investments":  "investments",
    "/real-estate":  "real-estate",
    "/projections":  "projections",
    "/monte-carlo":  "monte-carlo",
    "/roth-conversion": "roth-conversion",
}

_PAGE_TITLES: dict[str, str] = {
    "dashboard":    "📊  Dashboard",
    "profile":      "👤  Profile & Settings",
    "income":       "💼  Income Sources",
    "expenses":     "🛒  Expenses",
    "investments":  "📈  Investments",
    "real-estate":  "🏠  Real Estate",
    "projections":  "🔮  Projections",
    "monte-carlo":  "🎲  Monte Carlo Simulation",
    "roth-conversion": "🔄  Roth Conversion",
}

_ALL_NAV_LINKS = [_DASHBOARD] + _MY_PLAN + _INSIGHTS


def register_routing_callback(app: dash.Dash) -> None:
    """
    Register the URL → page-content routing callback on ``app``.
    Also registers sidebar toggle and active-link callbacks.
    """

    # ── Page routing ──────────────────────────────────────────────────
    def _placeholder_page(key: str):
        return html.Div(
            [
                html.H1(f"Coming Soon: {key.title()}", className="page-title"),
                html.P("This page will be implemented in the next phase.", className="page-subtitle"),
            ],
            className="page-header"
        )

    @app.callback(
        Output("page-content",  "children"),
        Output("topbar-title",  "children"),
        Input("url", "pathname"),
        State("profile-store",      "data"),
        State("projection-store",   "data"),
        State("monte-carlo-store",  "data"),
        State("roth-conversion-store", "data"),
    )
    def render_page(pathname: str, profile_data: dict, projection_data: dict, mc_data: dict, roth_data: dict):
        try:
            from ui.pages.dashboard    import layout as dashboard_layout
            from ui.pages.profile      import layout as profile_layout
            from ui.pages.income       import layout as income_layout
            from ui.pages.expenses     import layout as expenses_layout
            from ui.pages.investments  import layout as investments_layout
            from ui.pages.real_estate  import layout as real_estate_layout
            from ui.pages.projections  import layout as projections_layout
            from ui.pages.monte_carlo  import layout as monte_carlo_layout
            from ui.pages.roth_conversion import layout as roth_conversion_layout

            page_key = _ROUTES.get(pathname or "/", "dashboard")
            title    = _PAGE_TITLES.get(page_key, "")

            if page_key == "dashboard":
                content = dashboard_layout(profile_data, projection_data, mc_data)
            elif page_key == "profile":
                content = profile_layout(profile_data)
            elif page_key == "income":
                content = income_layout(profile_data)
            elif page_key == "expenses":
                content = expenses_layout(profile_data)
            elif page_key == "investments":
                content = investments_layout(profile_data)
            elif page_key == "real-estate":
                content = real_estate_layout(profile_data)
            elif page_key == "projections":
                content = projections_layout(profile_data, projection_data)
            elif page_key == "monte-carlo":
                content = monte_carlo_layout(profile_data, mc_data)
            elif page_key == "roth-conversion":
                content = roth_conversion_layout(profile_data, roth_data)
            else:
                content = _placeholder_page(page_key)

            return content, title

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = html.Div(
                [
                    html.H3("🛠️  Page Rendering Error", className="text-danger"),
                    html.P(f"An error occurred while loading this page: {str(e)}"),
                    html.Pre(traceback.format_exc(), style={"fontSize": "11px", "color": "var(--text-muted)"})
                ],
                className="section-card"
            )
            return err_msg, "⚠️ Error"

    # ── All sidebar interactivity (client-side, zero server round-trips) ──
    # Combining nav highlighting, collapse toggle, and collapse sync into
    # a single clientside callback that runs entirely in the browser.
    # This ensures the sidebar DOM is NEVER recreated by the server,
    # preserving dbc.Collapse is_open state across page navigations.
    app.clientside_callback(
        """
        function(pathname, myPlanClicks, insightsClicks, myPlanOpen, insightsOpen) {
            var trigger = dash_clientside.callback_context.triggered;
            var triggerId = trigger && trigger.length > 0 ? trigger[0].prop_id : "";

            // Nav highlighting
            var navItems = [
                "/", "/profile", "/income", "/expenses",
                "/investments", "/real-estate",
                "/projections", "/monte-carlo", "/roth-conversion"
            ];
            var output = navItems.map(function(href) {
                var active = (pathname || "/") === href ||
                    (href === "/" && (pathname === "/dashboard" || pathname === "/" || pathname === null));
                return active ? "nav-item-link active" : "nav-item-link";
            });

            // Collapse toggle
            var newMyPlanOpen = myPlanOpen;
            var newInsightsOpen = insightsOpen;
            if (triggerId === "sidebar-toggle-my-plan.n_clicks") {
                newMyPlanOpen = !myPlanOpen;
            }
            if (triggerId === "sidebar-toggle-insights.n_clicks") {
                newInsightsOpen = !insightsOpen;
            }

            return output.concat([
                newMyPlanOpen,      // sidebar-my-plan-open data
                newInsightsOpen,     // sidebar-insights-open data
                newMyPlanOpen ? "collapse-arrow open" : "collapse-arrow",
                newInsightsOpen ? "collapse-arrow open" : "collapse-arrow",
                newMyPlanOpen,      // sidebar-collapse-my-plan is_open
                newInsightsOpen,     // sidebar-collapse-insights is_open
            ]);
        }
        """,
        # ── Outputs (all Outputs first) ──
        (
            [Output(f"nav-{label.lower().replace(' ', '-')}", "className") for _, _, label in _ALL_NAV_LINKS]
            + [Output("sidebar-my-plan-open", "data"),
               Output("sidebar-insights-open", "data"),
               Output("sidebar-my-plan-arrow", "className"),
               Output("sidebar-insights-arrow", "className"),
               Output("sidebar-collapse-my-plan", "is_open"),
               Output("sidebar-collapse-insights", "is_open")]
        ),
        # ── Inputs ──
        [Input("url", "pathname"),
         Input("sidebar-toggle-my-plan", "n_clicks"),
         Input("sidebar-toggle-insights", "n_clicks")],
        # ── States ──
        [State("sidebar-my-plan-open", "data"),
         State("sidebar-insights-open", "data")],
    )
