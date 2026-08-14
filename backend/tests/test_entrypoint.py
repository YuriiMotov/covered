import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import application, main

# `app` is only importable from the backend directory - it is not installed.
BACKEND_DIR = Path(__file__).parent.parent


def serve_smoke_request() -> None:  # pragma: no cover - runs in a subprocess
    """Real telemetry conflicts with capfire, so this runs in a subprocess."""

    assert getattr(main.app, "_is_instrumented_by_opentelemetry", False)

    with TestClient(main.app) as client:
        print(client.get("/coverage/not-a-site-id/index.html").status_code)


def test_main_exposes_the_fastapi_app():
    assert main.app is application.app


def test_instrumented_app_serves_requests():
    env = {
        **os.environ,
        "LOGFIRE_TOKEN": "fake-token",
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
        "LOGFIRE_CONSOLE": "false",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.test_entrypoint import serve_smoke_request; serve_smoke_request()",
        ],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "422"
