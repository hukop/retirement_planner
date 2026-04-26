"""
Callback registry.
"""
import dash

# Import specific callback registrars
from ui.callbacks.runner_cb import register_runner_callbacks
from ui.callbacks.dynamic_cb import register_dynamic_callbacks

def register_all_callbacks(app: dash.Dash):
    """Call this from app.py to hook up all interactivity."""
    register_runner_callbacks(app)
    register_dynamic_callbacks(app)
