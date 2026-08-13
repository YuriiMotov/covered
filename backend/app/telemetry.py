import logging
from importlib.metadata import PackageNotFoundError, version

import logfire
from fastapi import FastAPI
from logfire.integrations.logging import LogfireLoggingHandler


def _service_version() -> str:  # pragma: no cover - deployment only
    try:
        return version("covered-server")
    except PackageNotFoundError:
        return "unknown"


def setup_telemetry(app: FastAPI) -> None:  # pragma: no cover - deployment only
    """Configure Logfire and switch on instrumentation."""
    logfire.configure(
        service_name="covered-backend",
        service_version=_service_version(),
        send_to_logfire="if-token-present",
    )
    logfire.instrument_system_metrics()
    logfire.instrument_fastapi(app)
    logfire.instrument_httpx()
    logfire.instrument_redis()
    logging.getLogger().addHandler(LogfireLoggingHandler())
