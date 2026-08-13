"""Importing `app.main` configures Logfire globally, so it runs in a subprocess."""

import subprocess
import sys
from pathlib import Path

# `app` is only importable from the backend directory - it is not installed.
BACKEND_DIR = Path(__file__).parent.parent


def test_main_exposes_the_fastapi_app():
    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print(type(app.main.app).__name__)"],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FastAPI"
