"""Test data shared across test modules."""

import uuid
from typing import Any

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
