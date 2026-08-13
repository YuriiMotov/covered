"""Deployment entrypoint: the application plus telemetry.

The app itself lives in `app.application`, which stays free of telemetry so tests can
import it without `logfire.configure()` running.
"""

from app.application import app
from app.telemetry import setup_telemetry

setup_telemetry(app)

__all__ = ["app"]
