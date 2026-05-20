"""
Finance Planner — Personal retirement planning web app.
Built with Python + Dash. All data stays local.
"""

import diskcache
import dash
from dash import Dash, DiskcacheManager
import dash_bootstrap_components as dbc

from ui.layout import create_layout, register_routing_callback


# ---------------------------------------------------------------------------
# Background callback manager (required for progress-reporting callbacks)
# ---------------------------------------------------------------------------
import os
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
_cache = diskcache.Cache(_CACHE_DIR)
background_callback_manager = DiskcacheManager(_cache)


# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    ],
    suppress_callback_exceptions=True,  # Required: pattern-matching callbacks (ALL/MATCH)
                                        # reference component IDs that don't exist at startup
                                        # (dynamic lists render IDs only after data loads).
    title="Finance Planner",
    update_title="Calculating...",
    background_callback_manager=background_callback_manager,
)

server = app.server  # Expose for production WSGI servers if ever needed

# ---------------------------------------------------------------------------
# Layout + routing
# ---------------------------------------------------------------------------
app.layout = create_layout()
register_routing_callback(app)

# ---------------------------------------------------------------------------
# Import callbacks (Phase 13)
# ---------------------------------------------------------------------------
from ui.callbacks import register_all_callbacks
register_all_callbacks(app)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=8050)
