"""Tests for the telemetry (except auto-instrumentation)."""

import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from logfire.testing import CaptureLogfire
from redis import RedisError

from tests.helpers import (
    COVERAGE_STATUS,
    SITE_ID,
    STS_CREDENTIALS,
    collect_metrics,
    counter_value,
    find_span,
    find_spans,
    get_commit,
    histogram_sum,
)

FILE_CONTENT = b"<html>hello</html>"

NO_SUCH_KEY = ClientError(
    error_response={"Error": {"Code": "NoSuchKey"}},
    operation_name="GetObject",
)


def s3_response(content: bytes) -> dict[str, AsyncMock]:
    """A fake `get_object` response with a readable body."""
    body = AsyncMock()
    body.read.return_value = content
    return {"Body": body}


def github_span(capfire: CaptureLogfire, endpoint: str) -> dict:
    """The single `github.request` span for this endpoint."""
    spans = [
        span
        for span in find_spans(capfire, "github.request")
        if span["attributes"]["endpoint"] == endpoint
    ]
    assert len(spans) == 1
    return spans[0]


class TestS3Spans:
    def test_serving_file_describes_it_on_the_span(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_s3_client.get_object.return_value = s3_response(FILE_CONTENT)

        resp = client.get(f"/coverage/{SITE_ID}/index.html")

        assert resp.status_code == 200

        span = find_span(capfire, "s3.get_object")
        assert span is not None
        assert span["attributes"]["aws.s3.bucket"] == "test-bucket"
        assert span["attributes"]["aws.s3.key"] == f"sites/{SITE_ID}/index.html"
        assert span["attributes"]["site_id"] == SITE_ID
        assert span["attributes"]["result"] == "ok"
        assert span["attributes"]["file_size"] == len(FILE_CONTENT)

    def test_missing_file_is_marked_not_found(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_s3_client.get_object.side_effect = NO_SUCH_KEY

        resp = client.get(f"/coverage/{SITE_ID}/missing.html")

        assert resp.status_code == 404

        span = find_span(capfire, "s3.get_object")
        assert span is not None
        assert span["attributes"]["result"] == "not_found"
        assert "file_size" not in span["attributes"]


class TestUploadSessionSpans:
    def test_assume_role_nests_under_the_session_span(
        self,
        client: TestClient,
        mock_sts_client: AsyncMock,
        api_key: str,
        capfire: CaptureLogfire,
    ):
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        resp = client.post("/coverage/create-site/", headers={"token": api_key})

        assert resp.status_code == 200

        session_span = find_span(capfire, "s3.create_upload_session")
        assume_span = find_span(capfire, "sts.assume_role")
        assert session_span is not None
        assert assume_span is not None
        assert assume_span["parent"]["span_id"] == session_span["context"]["span_id"]
        assert (
            assume_span["attributes"]["aws.role_arn"]
            == "arn:aws:iam::123456789012:role/fake"
        )
        assert session_span["attributes"]["site_id"] == resp.json()["site_id"]

    def test_first_attempt_is_recorded_on_the_span(
        self,
        client: TestClient,
        mock_sts_client: AsyncMock,
        api_key: str,
        capfire: CaptureLogfire,
    ):
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        resp = client.post("/coverage/create-site/", headers={"token": api_key})

        assert resp.status_code == 200

        span = find_span(capfire, "s3.generate_site_id")
        assert span is not None
        assert span["attributes"]["attempts"] == 1

    def test_site_id_collisions_are_counted_on_the_span(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        mock_sts_client: AsyncMock,
        api_key: str,
        capfire: CaptureLogfire,
    ):
        collision = ClientError(
            error_response={"Error": {"Code": "PreconditionFailed"}},
            operation_name="PutObject",
        )
        mock_s3_client.put_object.side_effect = [collision, collision, {}]
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        resp = client.post("/coverage/create-site/", headers={"token": api_key})

        assert resp.status_code == 200

        span = find_span(capfire, "s3.generate_site_id")
        assert span is not None
        assert span["attributes"]["attempts"] == 3


class TestS3Metrics:
    def test_every_served_file_is_counted(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_s3_client.get_object.return_value = s3_response(FILE_CONTENT)

        for _ in range(3):
            assert client.get(f"/coverage/{SITE_ID}/index.html").status_code == 200

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.s3.files_served", result="ok") == 3

    def test_missing_files_are_counted_apart_from_served_ones(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_s3_client.get_object.side_effect = [
            s3_response(FILE_CONTENT),
            NO_SUCH_KEY,
            s3_response(FILE_CONTENT),
            NO_SUCH_KEY,
            NO_SUCH_KEY,
        ]

        for path in ("a.html", "b.html", "c.html", "d.html", "e.html"):
            client.get(f"/coverage/{SITE_ID}/{path}")

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.s3.files_served", result="ok") == 2
        assert counter_value(points, "covered.s3.files_served", result="not_found") == 3

    def test_file_sizes_add_up(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        capfire: CaptureLogfire,
    ):
        contents = [b"a", b"bb", b"cccc"]
        mock_s3_client.get_object.side_effect = [s3_response(c) for c in contents]

        for name in ("one.html", "two.html", "three.html"):
            assert client.get(f"/coverage/{SITE_ID}/{name}").status_code == 200

        points = collect_metrics(capfire)
        assert histogram_sum(points, "covered.s3.file_size") == sum(
            len(c) for c in contents
        )

    def test_nothing_is_recorded_when_the_site_id_is_rejected(
        self,
        client: TestClient,
        capfire: CaptureLogfire,
    ):
        resp = client.get("/coverage/not-a-site-id/index.html")

        assert resp.status_code == 422

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.s3.files_served", result="ok") == 0
        assert counter_value(points, "covered.s3.files_served", result="not_found") == 0


class TestUploadSessionMetrics:
    def test_every_session_is_counted(
        self,
        client: TestClient,
        mock_sts_client: AsyncMock,
        api_key: str,
        capfire: CaptureLogfire,
    ):
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        for _ in range(3):
            resp = client.post("/coverage/create-site/", headers={"token": api_key})
            assert resp.status_code == 200

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.upload_sessions.created") == 3

    def test_rejected_requests_are_not_counted(
        self,
        client: TestClient,
        capfire: CaptureLogfire,
    ):
        assert client.post("/coverage/create-site/").status_code == 401
        assert (
            client.post(
                "/coverage/create-site/", headers={"token": "wrong-key"}
            ).status_code
            == 403
        )

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.upload_sessions.created") == 0


class TestGithubSpans:
    pytestmark = pytest.mark.respx(base_url="https://api.github.com")

    def test_successful_request_describes_the_call(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert client.get("/badge/owner/repo.svg").status_code == 200

        span = github_span(capfire, "commits")
        assert span["attributes"]["owner"] == "owner"
        assert span["attributes"]["repo"] == "repo"
        assert span["attributes"]["attempts"] == 1
        assert span["attributes"]["http.status_code"] == 200

        assert github_span(capfire, "statuses")["attributes"]["attempts"] == 1

    def test_retry_shows_up_as_a_second_attempt(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").mock(
            side_effect=[
                httpx.Response(500, json={"message": "server error"}),
                httpx.Response(200, json=[commit]),
            ]
        )
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert client.get("/badge/owner/repo.svg").status_code == 200

        assert github_span(capfire, "commits")["attributes"]["attempts"] == 2
        assert github_span(capfire, "statuses")["attributes"]["attempts"] == 1

    def test_giving_up_records_every_attempt(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        respx_mock.get("/repos/owner/repo/commits").respond(status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/owner/repo.svg")

        span = github_span(capfire, "commits")
        assert span["attributes"]["attempts"] == 3
        assert "http.status_code" not in span["attributes"]


class TestGithubMetrics:
    pytestmark = pytest.mark.respx(base_url="https://api.github.com")

    def test_every_call_is_counted_per_endpoint(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        for _ in range(3):
            assert client.get("/badge/owner/repo.svg").status_code == 200

        points = collect_metrics(capfire)
        for endpoint in ("commits", "statuses"):
            requests_cnt = counter_value(
                points, "covered.github.requests", endpoint=endpoint, outcome="ok"
            )
            assert requests_cnt == 3
        assert counter_value(points, "covered.github.retries", endpoint="commits") == 0

    def test_retries_are_counted_separately_from_requests(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        server_error = httpx.Response(500, json={"message": "server error"})
        respx_mock.get("/repos/owner/repo/commits").mock(
            side_effect=[
                server_error,
                httpx.Response(200, json=[commit]),
                server_error,
                server_error,
                httpx.Response(200, json=[commit]),
            ]
        )
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        for _ in range(2):
            assert client.get("/badge/owner/repo.svg").status_code == 200

        points = collect_metrics(capfire)
        # Two requests, succeeding on the 2nd and the 3rd attempt: 3 retries.
        assert counter_value(points, "covered.github.retries", endpoint="commits") == 3
        requests_cnt = counter_value(
            points, "covered.github.requests", endpoint="commits", outcome="ok"
        )
        assert requests_cnt == 2
        assert counter_value(points, "covered.github.retries", endpoint="statuses") == 0

    def test_giving_up_is_counted_as_an_error(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        respx_mock.get("/repos/owner/repo/commits").respond(status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/owner/repo.svg")

        points = collect_metrics(capfire)
        requests_cnt = counter_value(
            points, "covered.github.requests", endpoint="commits", outcome="error"
        )
        assert requests_cnt == 1

        requests_cnt = counter_value(
            points, "covered.github.requests", endpoint="commits", outcome="ok"
        )
        assert requests_cnt == 0
        assert counter_value(points, "covered.github.retries", endpoint="commits") == 2

    @pytest.mark.respx(base_url="https://api.github.com", assert_all_called=False)
    def test_cached_badge_calls_github_not_at_all(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = b"<svg>cached</svg>"
        respx_mock.get("/repos/owner/repo/commits")

        assert client.get("/badge/owner/repo.svg").status_code == 200

        points = collect_metrics(capfire)
        requests_cnt = counter_value(
            points, "covered.github.requests", endpoint="commits", outcome="ok"
        )
        assert requests_cnt == 0


class TestBadgeSpans:
    pytestmark = pytest.mark.respx(base_url="https://api.github.com")

    @pytest.mark.respx(base_url="https://api.github.com", assert_all_called=False)
    def test_cache_hit_is_recorded_on_the_request_span(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = b"<svg>cached</svg>"
        respx_mock.get("/repos/owner/repo/commits")

        resp = client.get("/badge/owner/repo.svg")

        assert resp.text == "<svg>cached</svg>"

        span = find_span(capfire, "badge.request")
        assert span is not None
        assert span["attributes"]["org"] == "owner"
        assert span["attributes"]["repo"] == "repo"
        assert span["attributes"]["badge.cache"] == "hit"

    def test_resolved_coverage_is_described_on_its_own_span(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert "coverage: 87%" in client.get("/badge/owner/repo.svg").text

        request_span = find_span(capfire, "badge.request")
        span = find_span(capfire, "badge.resolve_coverage")
        assert request_span is not None
        assert span is not None
        assert request_span["attributes"]["badge.cache"] == "miss"
        assert span["parent"]["span_id"] == request_span["context"]["span_id"]
        assert span["attributes"]["commits_fetched"] == 1
        assert span["attributes"]["coverage_found"] is True
        assert span["attributes"]["coverage_percent"] == 87.0
        assert span["attributes"]["commit_sha"] == commit["sha"]
        assert span["attributes"]["commit_index"] == 0

    def test_skipped_commits_show_up_as_a_later_index(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        skipped = get_commit(skip_ci=True)
        used = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[skipped, used])
        respx_mock.get(f"/repos/owner/repo/statuses/{used['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert "coverage: 87%" in client.get("/badge/owner/repo.svg").text

        span = find_span(capfire, "badge.resolve_coverage")
        assert span is not None
        assert span["attributes"]["commits_fetched"] == 2
        assert span["attributes"]["commit_index"] == 1
        assert span["attributes"]["commit_sha"] == used["sha"]

    def test_no_usable_commit_leaves_the_index_unset(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commits = [get_commit(skip_ci=True) for _ in range(3)]
        respx_mock.get("/repos/owner/repo/commits").respond(json=commits)

        assert "coverage: ??%" in client.get("/badge/owner/repo.svg").text

        span = find_span(capfire, "badge.resolve_coverage")
        assert span is not None
        assert span["attributes"]["commits_fetched"] == 3
        assert span["attributes"]["coverage_found"] is False
        # Every commit was skipped, so no commit was examined at all.
        assert "commit_index" not in span["attributes"]
        assert "commit_sha" not in span["attributes"]

    def test_the_whole_request_is_one_trace(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert client.get("/badge/owner/repo.svg").status_code == 200

        request_span = find_span(capfire, "badge.request")
        resolve_span = find_span(capfire, "badge.resolve_coverage")
        assert request_span is not None
        assert resolve_span is not None

        # badge.request > badge.resolve_coverage > github.request (x2)
        assert resolve_span["parent"]["span_id"] == request_span["context"]["span_id"]
        for endpoint in ("commits", "statuses"):
            span = github_span(capfire, endpoint)
            assert span["parent"]["span_id"] == resolve_span["context"]["span_id"]
            assert span["context"]["trace_id"] == request_span["context"]["trace_id"]

    def test_badge_without_coverage_says_so(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(json=[])

        assert "coverage: ??%" in client.get("/badge/owner/repo.svg").text

        span = find_span(capfire, "badge.resolve_coverage")
        assert span is not None
        assert span["attributes"]["coverage_found"] is False
        assert span["attributes"]["commits_fetched"] == 1
        assert "coverage_percent" not in span["attributes"]


class TestBadgeMetrics:
    pytestmark = pytest.mark.respx(base_url="https://api.github.com")

    def test_hits_and_misses_are_counted_separately(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        cached = b"<svg>cached</svg>"
        mock_redis.get.side_effect = [None, cached, cached, None, None]
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        for _ in range(5):
            assert client.get("/badge/owner/repo.svg").status_code == 200

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.badge.cache_lookups", result="hit") == 2
        assert counter_value(points, "covered.badge.cache_lookups", result="miss") == 3
        # Only the three misses render a badge.
        assert counter_value(points, "covered.badge.rendered", found=True) == 3

    def test_rendered_badges_are_split_by_outcome(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.return_value = None
        with_coverage = get_commit()
        without_coverage = get_commit()
        respx_mock.get("/repos/owner/repo/commits").mock(
            side_effect=[
                httpx.Response(200, json=[with_coverage]),
                httpx.Response(200, json=[without_coverage]),
            ]
        )
        respx_mock.get(f"/repos/owner/repo/statuses/{with_coverage['sha']}").respond(
            json=[COVERAGE_STATUS]
        )
        respx_mock.get(f"/repos/owner/repo/statuses/{without_coverage['sha']}").respond(
            json=[]
        )

        assert "coverage: 87%" in client.get("/badge/owner/repo.svg").text
        assert "coverage: ??%" in client.get("/badge/owner/repo.svg").text

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.badge.rendered", found=True) == 1
        assert counter_value(points, "covered.badge.rendered", found=False) == 1

    def test_redis_errors_are_counted(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        capfire: CaptureLogfire,
    ):
        mock_redis.get.side_effect = RedisError("connection refused")
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        for _ in range(2):
            assert client.get("/badge/owner/repo.svg").status_code == 200

        points = collect_metrics(capfire)
        assert counter_value(points, "covered.badge.cache_lookups", result="error") == 2
        assert counter_value(points, "covered.badge.cache_lookups", result="miss") == 0

    def test_missing_redis_is_counted_as_unavailable(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        disable_redis: None,
        capfire: CaptureLogfire,
    ):
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        for _ in range(3):
            assert client.get("/badge/owner/repo.svg").status_code == 200

        points = collect_metrics(capfire)
        assert (
            counter_value(points, "covered.badge.cache_lookups", result="unavailable")
            == 3
        )
        mock_redis.get.assert_not_called()


class TestLogging:
    pytestmark = pytest.mark.respx(base_url="https://api.github.com")

    def test_failed_cache_read_is_logged(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ):
        caplog.set_level(logging.WARNING)
        mock_redis.get.side_effect = RedisError("connection refused")
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert client.get("/badge/owner/repo.svg").status_code == 200

        record = next(
            r for r in caplog.records if r.message == "badge cache read failed"
        )
        assert record.levelno == logging.WARNING
        assert getattr(record, "cache_key") == "cache:badge:owner:repo"

    def test_failed_cache_write_is_logged(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ):
        caplog.set_level(logging.WARNING)
        mock_redis.get.return_value = None
        mock_redis.set.side_effect = RedisError("connection refused")
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        assert client.get("/badge/owner/repo.svg").status_code == 200

        record = next(
            r for r in caplog.records if r.message == "badge cache write failed"
        )
        assert getattr(record, "cache_key") == "cache:badge:owner:repo"

    @pytest.mark.respx(base_url="https://api.github.com", assert_all_called=False)
    def test_failed_cache_invalidation_is_logged_with_the_traceback(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        api_key: str,
        caplog: pytest.LogCaptureFixture,
    ):
        caplog.set_level(logging.WARNING)
        mock_redis.delete.side_effect = RedisError("connection refused")

        resp = client.post(
            "/coverage/invalidate-cache/owner/repo/", headers={"token": api_key}
        )

        assert resp.status_code == 500

        record = next(
            r for r in caplog.records if r.message == "cache invalidation failed"
        )
        assert record.levelno == logging.ERROR
        assert getattr(record, "cache_key") == "cache:badge:owner:repo"
        assert record.exc_info is not None
