import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import application, main
from app.telemetry import setup_telemetry

# `app` is only importable from the backend directory - it is not installed.
BACKEND_DIR = Path(__file__).parent.parent


# A caller-supplied trace context with the sampled flag off. Proxies and CDNs
# send these, and the badge endpoint is public, so anyone can.
UNSAMPLED_TRACEPARENT = {
    "traceparent": "00-11111111111111111111111111111111-2222222222222222-00"
}


def serve_smoke_request() -> None:  # pragma: no cover - runs in a subprocess
    """Real telemetry conflicts with capfire, so this runs in a subprocess."""

    assert getattr(main.app, "_is_instrumented_by_opentelemetry", False)

    with TestClient(main.app) as client:
        resp = client.get(
            "/coverage/not-a-site-id/index.html", headers=UNSAMPLED_TRACEPARENT
        )
        print(resp.status_code)


def test_main_exposes_the_fastapi_app():
    assert main.app is application.app


@pytest.mark.parametrize("token", [None, "", "   "])
def test_a_blank_token_disables_telemetry_out_loud(
    token: str | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Silence here would look exactly like working telemetry in the deploy logs."""
    if token is None:
        monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("LOGFIRE_TOKEN", token)
    caplog.set_level(logging.WARNING)

    setup_telemetry(FastAPI())

    assert "LOGFIRE_TOKEN is not set" in caplog.text


def test_instrumented_app_serves_requests():
    """The console output doubles as proof that the span survived.

    With the default `distributed_tracing`, an incoming `sampled=0` traceparent
    silently drops the whole trace, and only a warning is printed.
    """
    env = {
        **os.environ,
        "LOGFIRE_TOKEN": "fake-token",
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
        "LOGFIRE_CONSOLE": "true",
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
    output = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert "422" in result.stdout
    assert "GET /coverage/" in output
    assert "Found propagated trace context" not in output
