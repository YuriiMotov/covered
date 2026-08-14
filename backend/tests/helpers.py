"""Test data and telemetry capture helpers shared across test modules."""

import uuid
from typing import Any

from logfire.testing import CaptureLogfire

SITE_ID = "aabbccddeeff"

STS_CREDENTIALS = {
    "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
    "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "SessionToken": "FwoGZXIvYXdzEBY...",
}

COVERAGE_STATUS = {
    "state": "success",
    "description": "87% coverage",
    "target_url": "https://example.com/coverage/report",
    "context": "coverage/project",
}

NON_COVERAGE_STATUS = {
    "state": "success",
    "description": "example status",
    "target_url": "https://example.com/",
    "context": "other/status",
}

FAILED_COVERAGE_STATUS = {
    "state": "failure",
    "description": "42% coverage",
    "target_url": "https://example.com/coverage/report",
    "context": "coverage/project",
}


def get_commit(sha: str | None = None, skip_ci: bool = False) -> dict[str, Any]:
    if sha is None:
        sha = uuid.uuid4().hex
    message = uuid.uuid4().hex
    if skip_ci:
        message += "\n\n[skip ci]"
    return {"sha": sha, "commit": {"message": message}}


MetricPoint = tuple[str, dict[str, Any], dict[str, Any]]


def collect_metrics(capfire: CaptureLogfire) -> list[MetricPoint]:
    """Read every recorded metric point, as (name, attributes, data point).

    The reader empties itself when read, so call this once per test.
    """
    try:
        collected = capfire.get_collected_metrics()
    except AttributeError:  # logfire raises when no metric was recorded at all
        return []
    return [
        (metric["name"], point["attributes"], point)
        for metric in collected
        for point in metric["data"]["data_points"]
    ]


def counter_value(points: list[MetricPoint], name: str, **attributes: Any) -> int:
    """Value of the counter series with exactly these attributes, or 0."""
    for metric_name, point_attributes, point in points:
        if metric_name == name and point_attributes == attributes:
            return point["value"]
    return 0


def histogram_sum(points: list[MetricPoint], name: str) -> float:
    """Sum of everything recorded into a histogram, or 0."""
    return sum(point["sum"] for metric_name, _, point in points if metric_name == name)


def find_spans(capfire: CaptureLogfire, name: str) -> list[dict[str, Any]]:
    """Every exported span with this name, in export order."""
    return [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["name"] == name
    ]


def find_span(capfire: CaptureLogfire, name: str) -> dict[str, Any] | None:
    """The first exported span with this name, or None."""
    spans = find_spans(capfire, name)
    return spans[0] if spans else None
