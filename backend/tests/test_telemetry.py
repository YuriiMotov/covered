"""Tests for the telemetry (except auto-instrumentation)."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from logfire.testing import CaptureLogfire

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
    def test_serving_a_file_describes_it_on_the_span(
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

    def test_a_retry_shows_up_as_a_second_attempt(
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
    def test_a_cached_badge_calls_github_not_at_all(
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
