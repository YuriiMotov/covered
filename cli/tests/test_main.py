"""
Tests for `_main` - orchestration of session creation, upload, status update, and cache
purge.
"""

from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import typer

from covered.cli import _main

API_URL = "https://api.example.com"
API_KEY = "test_api_key"
REPO_OWNER = "owner"
REPO_NAME = "repo"
COMMIT_SHA = "abc123"
GH_TOKEN = "gh_xyz"
SITE_ID = "site-xyz"

SESSION_RESP = {
    "site_id": SITE_ID,
    "bucket": "test-bucket",
    "region": "us-east-1",
    "access_key_id": "id",
    "secret_access_key": "secret",
    "session_token": "tok",
}

CAMO_URL = "https://camo.githubusercontent.com/abc123def/xyz789ABC"
BADGE_HTML = (
    f'<img alt="cov" src="{CAMO_URL}" '
    f'data-canonical-src="{API_URL}/coverage/badge.svg">'
)


class PatchedMain(TypedDict):
    """
    Handles to the three mocks installed by the `patched_main` fixture.
    """

    request: AsyncMock
    upload: AsyncMock
    coverage: MagicMock


def _main_kwargs(directory: Path, **overrides: Any) -> dict:
    """
    Build a kwargs dict for `_main`; override only what each test cares about.
    """
    return {
        "directory": directory,
        "api_url": API_URL,
        "api_key": API_KEY,
        "concurrency": 50,
        "repo_owner": REPO_OWNER,
        "repo_name": REPO_NAME,
        "commit_sha": COMMIT_SHA,
        "coverage_threshold": 90.0,
        "gh_token": GH_TOKEN,
        "purge_cache": False,
        **overrides,
    }


@pytest.fixture
def patched_main(monkeypatch: pytest.MonkeyPatch) -> PatchedMain:
    """
    Patch `_main`'s three collaborators; defaults: 95% coverage, 5 files uploaded.
    """
    request = AsyncMock()
    upload = AsyncMock(return_value=5)
    coverage = MagicMock(return_value=95.0)

    monkeypatch.setattr("covered.cli._request", request)
    monkeypatch.setattr("covered.cli._upload_files", upload)
    monkeypatch.setattr("covered.cli._get_coverage_info", coverage)

    return {"request": request, "upload": upload, "coverage": coverage}


async def test_main_creates_session_then_uploads_then_sets_status(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    Happy path: session is created, files uploaded, GitHub status posted.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path))

    calls = patched_main["request"].call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("POST", f"{API_URL}/coverage/create-site/")
    patched_main["upload"].assert_awaited_once_with(tmp_path, SESSION_RESP, 50)
    assert calls[1].args == (
        "POST",
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/statuses/{COMMIT_SHA}",
    )


async def test_main_sets_success_status_when_coverage_meets_threshold(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    When coverage > threshold, the posted status `state` is `success`.
    """
    patched_main["coverage"].return_value = 95.0
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path, coverage_threshold=90.0))

    assert (
        patched_main["request"].call_args_list[1].kwargs["json"]["state"] == "success"
    )


async def test_main_sets_failure_status_when_coverage_below_threshold(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    When coverage < threshold, the posted status `state` is `failure`.
    """
    patched_main["coverage"].return_value = 80.0
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path, coverage_threshold=90.0))

    assert (
        patched_main["request"].call_args_list[1].kwargs["json"]["state"] == "failure"
    )


async def test_main_status_at_threshold_is_success(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    Boundary: coverage exactly equal to threshold yields `success`, not `failure`.
    """
    patched_main["coverage"].return_value = 90.0
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path, coverage_threshold=90.0))

    assert (
        patched_main["request"].call_args_list[1].kwargs["json"]["state"] == "success"
    )


async def test_main_skips_status_update_when_coverage_missing(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    If `_get_coverage_info` returns None, no GitHub status call is made and `_main`
    returns early.
    """
    patched_main["coverage"].return_value = None
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
    ]

    await _main(**_main_kwargs(tmp_path))

    assert patched_main["request"].call_count == 1


async def test_main_exits_with_error_when_status_post_fails(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    Non-201 response from the GitHub status endpoint raises `typer.Exit(1)`.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(500, text="oops"),  # github status failed
    ]

    with pytest.raises(typer.Exit) as exc_info:
        await _main(**_main_kwargs(tmp_path))

    assert exc_info.value.exit_code == 1


async def test_main_status_target_url_points_at_site(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    The status payload's `target_url` is `{api_url}/coverage/{site_id}/`.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path))

    payload = patched_main["request"].call_args_list[1].kwargs["json"]
    assert payload["target_url"] == f"{API_URL}/coverage/{SITE_ID}/"


async def test_main_status_request_headers(patched_main: PatchedMain, tmp_path: Path):
    """
    The status request uses `Authorization: Bearer <gh_token>` and
    `Accept: application/vnd.github+json`.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path))

    headers = patched_main["request"].call_args_list[1].kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {GH_TOKEN}"
    assert headers["Accept"] == "application/vnd.github+json"


async def test_main_skips_cache_purge_when_purge_cache_false(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    When `purge_cache=False`, no invalidate-cache or README calls are made.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=False))

    assert patched_main["request"].call_count == 2


async def test_main_invalidates_cache_when_purge_cache_true(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    When `purge_cache=True`, POST to /invalidate-cache/{owner}/{repo}/ with the token
    header.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(200, text=""),  # README (no badge match -> early return)
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    invalidate = patched_main["request"].call_args_list[2]
    assert invalidate.args == (
        "POST",
        f"{API_URL}/coverage/invalidate-cache/{REPO_OWNER}/{REPO_NAME}/",
    )
    assert invalidate.kwargs["headers"] == {"token": API_KEY}


async def test_main_logs_failure_but_continues_when_invalidate_cache_fails(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    A non-200 invalidate-cache response is logged but the flow continues to Camo purge.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(500, text="oops"),  # invalidate-cache failed
        httpx.Response(200, text=""),  # README still attempted
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    # 4 calls: create-site, status, invalidate (failed), README
    assert patched_main["request"].call_count == 4


async def test_main_fetches_readme_with_html_accept_header(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    The README request uses `Accept: application/vnd.github.html+json`.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(200, text=""),  # README (no badge match -> early return)
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    readme = patched_main["request"].call_args_list[3]
    assert readme.args == (
        "GET",
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/readme",
    )
    assert readme.kwargs["headers"]["Accept"] == "application/vnd.github.html+json"
    assert readme.kwargs["headers"]["Authorization"] == f"Bearer {GH_TOKEN}"


async def test_main_purge_aborts_when_readme_fetch_fails(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    A non-200 README response is logged and `_main` returns without sending PURGE.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(404),  # README fetch failed
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    assert patched_main["request"].call_count == 4


async def test_main_purge_aborts_when_badge_not_found_in_readme(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    If the README has no matching `<img>`, `_main` logs and returns - no PURGE call.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(200, text="<html>no badge here</html>"),  # README (no badge)
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    assert patched_main["request"].call_count == 4


async def test_main_purges_camo_url_extracted_from_readme(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    PURGE is sent to the Camo URL captured by the badge regex from the README.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(200, text=BADGE_HTML),  # README with matching badge
        httpx.Response(200),  # PURGE
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    purge = patched_main["request"].call_args_list[4]
    assert purge.args == ("PURGE", CAMO_URL)


async def test_main_badge_regex_only_matches_camo_with_matching_canonical_src(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    A Camo `<img>` whose `data-canonical-src` does not match `api_url` is ignored.
    """
    other_html = (
        f'<img src="{CAMO_URL}" data-canonical-src="https://other.example.com/badge">'
    )
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(200, text=other_html),  # README - badge points elsewhere
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    assert patched_main["request"].call_count == 4


async def test_main_logs_failure_when_camo_purge_returns_non_200(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    A non-200 PURGE response is logged; `_main` does not raise or exit non-zero.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
        httpx.Response(200),  # invalidate-cache
        httpx.Response(200, text=BADGE_HTML),  # README with matching badge
        httpx.Response(500),  # PURGE failed
    ]

    await _main(**_main_kwargs(tmp_path, purge_cache=True))

    assert patched_main["request"].call_count == 5


async def test_main_create_site_uses_api_key_header_and_120s_timeout(
    patched_main: PatchedMain, tmp_path: Path
):
    """
    The create-site request sends the `token` header and uses the longer 120s timeout.
    """
    patched_main["request"].side_effect = [
        httpx.Response(200, json=SESSION_RESP),  # create-site
        httpx.Response(201),  # github status
    ]

    await _main(**_main_kwargs(tmp_path))

    create_call = patched_main["request"].call_args_list[0]
    assert create_call.kwargs["headers"] == {"token": API_KEY}
    assert create_call.kwargs["timeout"] == 120
