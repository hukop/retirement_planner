"""
Retirement Planner — Personal retirement planning web app.
Built with Python + Dash. All data stays local.
"""

import dash
from dash import Dash
import dash_bootstrap_components as dbc

from ui.layout import create_layout, register_routing_callback


# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    ],
    suppress_callback_exceptions=True,
    title="Retirement Planner",
    update_title="Calculating...",
)

server = app.server  # Expose for production WSGI servers if ever needed

# ---------------------------------------------------------------------------
# Layout + routing
# ---------------------------------------------------------------------------
app.layout = create_layout()
register_routing_callback(app)

# ---------------------------------------------------------------------------
# Import callbacks (uncomment as they are implemented in later phases)
# ---------------------------------------------------------------------------
# from ui.callbacks import profile_cb
# from ui.callbacks import income_cb
# from ui.callbacks import expenses_cb
# from ui.callbacks import investments_cb
# from ui.callbacks import real_estate_cb
# from ui.callbacks import projections_cb
# from ui.callbacks import persistence_cb


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=8050)

