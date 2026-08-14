"""Importing `app.main` configures Logfire globally, so it runs in a subprocess."""

import os
import subprocess
import sys
from pathlib import Path

# `app` is only importable from the backend directory - it is not installed.
BACKEND_DIR = Path(__file__).parent.parent


def test_main_exposes_the_fastapi_app():
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOGFIRE_")}
    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print(type(app.main.app).__name__)"],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FastAPI"
