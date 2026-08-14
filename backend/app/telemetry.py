import logging
import os
from importlib.metadata import PackageNotFoundError, version

import logfire
from fastapi import FastAPI
from logfire.integrations.logging import LogfireLoggingHandler
from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)

INSTRUMENTATION_NAME = "covered.backend"

tracer = trace.get_tracer(INSTRUMENTATION_NAME)
_meter = metrics.get_meter(INSTRUMENTATION_NAME)

S3_FILES_SERVED = _meter.create_counter(
    "covered.s3.files_served",
    description="Files served from S3, by result",
)
S3_FILE_SIZE = _meter.create_histogram(
    "covered.s3.file_size",
    unit="By",
    description="Size of files served from S3",
)
UPLOAD_SESSIONS_CREATED = _meter.create_counter(
    "covered.upload_sessions.created",
    description="Upload sessions created",
)
GITHUB_REQUESTS = _meter.create_counter(
    "covered.github.requests",
    description="GitHub API requests, by endpoint and outcome",
)
GITHUB_RETRIES = _meter.create_counter(
    "covered.github.retries",
    description="GitHub API attempts that followed a failed one",
)
BADGE_CACHE_LOOKUPS = _meter.create_counter(
    "covered.badge.cache_lookups",
    description="Badge cache lookups, by result",
)
BADGE_RENDERED = _meter.create_counter(
    "covered.badge.rendered",
    description="Badges rendered from scratch, by whether coverage was found",
)


def _service_version() -> str:  # pragma: no cover - deployment only
    try:
        return version("covered-server")
    except PackageNotFoundError:
        return "unknown"


def setup_telemetry(app: FastAPI) -> None:
    """Configure Logfire and switch on instrumentation. No-op without a token."""

    if not os.environ.get("LOGFIRE_TOKEN", "").strip():
        logger.warning("LOGFIRE_TOKEN is not set, telemetry is disabled")
        return

    _configure_logfire(app)  # pragma: no cover - needs a real token
    logger.info(  # pragma: no cover - needs a real token
        "telemetry enabled, environment %r",
        os.environ.get("LOGFIRE_ENVIRONMENT", "<unset>"),
    )


def _configure_logfire(app: FastAPI) -> None:  # pragma: no cover - needs a token
    logfire.configure(
        service_name="covered-backend",
        service_version=_service_version(),
        distributed_tracing=False,
    )
    logfire.instrument_system_metrics()
    logfire.instrument_fastapi(app)
    logfire.instrument_httpx()
    logfire.instrument_redis()
    logging.getLogger().addHandler(LogfireLoggingHandler())
