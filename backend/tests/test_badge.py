from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from redis import RedisError

from tests.helpers import (
    COVERAGE_STATUS,
    FAILED_COVERAGE_STATUS,
    NON_COVERAGE_STATUS,
    get_commit,
)

pytestmark = pytest.mark.respx(base_url="https://api.github.com")


class TestBadge:
    def test_returns_green_svg_for_successful_coverage(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/svg+xml"
        assert "coverage: 87%" in resp.text
        assert "#34D058" in resp.text  # green

    def test_returns_red_svg_for_failed_coverage(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[FAILED_COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: 42%" in resp.text
        assert "#CB2431" in resp.text  # red

    def test_returns_gray_badge_when_no_coverage_status(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(json=[])

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: ??%" in resp.text
        assert "#9F9F9F" in resp.text  # gray

    def test_returns_gray_badge_when_no_commits(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        respx_mock.get("/repos/owner/repo/commits").respond(json=[])

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: ??%" in resp.text

    @pytest.mark.respx(base_url="https://api.github.com", assert_all_called=False)
    def test_serves_cached_badge_from_redis(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = b"<svg>cached</svg>"
        commits_route = respx_mock.get("/repos/owner/repo/commits")

        resp = client.get("/badge/owner/repo.svg")

        assert resp.text == "<svg>cached</svg>"
        assert not commits_route.called

    def test_caches_generated_badge_in_redis(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        client.get("/badge/owner/repo.svg")

        mock_redis.set.assert_awaited_once()
        key, svg_bytes = mock_redis.set.call_args.args
        assert key == "cache:badge:owner:repo"
        assert b"coverage: 87%" in svg_bytes
        ex = mock_redis.set.call_args.kwargs.get("ex")
        assert ex == 60

    def test_no_store_headers_for_github_camo(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get(
            "/badge/owner/repo.svg",
            headers={"user-agent": "github-camo/abc123"},
        )

        assert resp.headers["cache-control"] == "private, no-store"
        assert resp.headers["cdn-cache-control"] == "no-store"

    def test_public_cache_headers_for_regular_clients(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert resp.headers["cache-control"] == "public, max-age=10"
        assert resp.headers["cdn-cache-control"] == "max-age=10"

    def test_redis_read_error_falls_through_to_github(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.side_effect = RedisError("connection refused")
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert resp.status_code == 200
        assert "coverage: 87%" in resp.text

    def test_redis_write_error_still_returns_badge(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        mock_redis.set.side_effect = RedisError("connection refused")
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert resp.status_code == 200
        assert "coverage: 87%" in resp.text

    @respx.mock(assert_all_called=False)
    def test_skips_skip_ci_commits(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        skip_ci_commit = get_commit(skip_ci=True)
        commit_2 = get_commit()

        commits_route = respx_mock.get("/repos/owner/repo/commits").respond(
            json=[skip_ci_commit, commit_2]
        )
        commit_with_status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit_2['sha']}"
        ).respond(json=[COVERAGE_STATUS])
        # The route below should not be called since the commit has "skip ci"
        skip_ci_commit_status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{skip_ci_commit['sha']}"
        )

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: 87%" in resp.text

        assert commits_route.called
        assert commit_with_status_route.called
        assert not skip_ci_commit_status_route.called

    @respx.mock(assert_all_called=False)
    def test_check_up_to_5_commits(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None

        commits = [get_commit(skip_ci=True) for _ in range(4)]
        commits.append(get_commit())

        respx_mock.get("/repos/owner/repo/commits").respond(json=commits)

        # Fifth commit has coverage status, the first 4 have "skip ci" and should be skipped
        respx_mock.get(f"/repos/owner/repo/statuses/{commits[4]['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        # The routes below should not be called since those commits have "skip ci"
        paths = tuple(
            f"/repos/owner/repo/statuses/{commit['sha']}" for commit in commits[:4]
        )
        skip_ci_commits_routes = respx_mock.route(path__in=paths)

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: 87%" in resp.text

        assert not skip_ci_commits_routes.called

    def test_commit_without_coverage_status(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[NON_COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: ??%" in resp.text

    @respx.mock(assert_all_called=False)
    def test_stop_on_first_commit_without_skip_ci(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None

        commit_without_coverage_status = get_commit()
        commit_with_coverage_status = get_commit()

        respx_mock.get("/repos/owner/repo/commits").respond(
            json=[commit_without_coverage_status, commit_with_coverage_status]
        )
        respx_mock.get(
            f"/repos/owner/repo/statuses/{commit_without_coverage_status['sha']}"
        ).respond(json=[NON_COVERAGE_STATUS])

        # The route below should not be called
        commit_2_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit_with_coverage_status['sha']}"
        )

        resp = client.get("/badge/owner/repo.svg")

        assert "coverage: ??%" in resp.text

        assert not commit_2_route.called

    def test_retry_on_github_api_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        commits_route = respx_mock.get("/repos/owner/repo/commits").mock(
            side_effect=[
                httpx.ConnectError("boom"),  # 1st call: network error
                httpx.Response(  # 2nd call: server error
                    500, json={"message": "server error"}
                ),
                httpx.Response(200, json=[commit]),  # 3rd call: success
            ]
        )

        status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit['sha']}"
        ).mock(
            side_effect=[
                httpx.ConnectError("boom"),  # 1st call: network error
                httpx.Response(  # 2nd call: server error
                    500, json={"message": "server error"}
                ),
                httpx.Response(200, json=[COVERAGE_STATUS]),  # 3rd call: success
            ]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert resp.status_code == 200
        assert "coverage: 87%" in resp.text

        assert commits_route.call_count == 3
        assert status_route.call_count == 3

    def test_server_error_on_github_api_commit_route_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commits_route = respx_mock.get("/repos/owner/repo/commits").respond(
            status_code=500
        )

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/owner/repo.svg")

        assert commits_route.call_count == 3  # Retries up to 3 times on failure

    def test_server_error_on_github_api_status_route_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit['sha']}"
        ).respond(status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/owner/repo.svg")

        assert status_route.call_count == 3  # Retries up to 3 times on failure

    def test_dont_write_cache_on_github_api_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
    ):
        mock_redis.get.return_value = None
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            status_code=500
        )

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/owner/repo.svg")

        assert not mock_redis.set.called  # Don't cache failed responses

    def test_get_badge_without_redis(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
        mock_redis: AsyncMock,
        disable_redis: None,
    ):
        # Test that the badge endpoint works even if Redis is not configured
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get("/badge/owner/repo.svg")

        assert resp.status_code == 200
        assert "coverage: 87%" in resp.text
        mock_redis.get.assert_not_called()
        mock_redis.set.assert_not_called()


class TestBadgeRedirect:
    def test_redirects_to_target_url(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        resp = client.get("/badge/redirect/owner/repo/", follow_redirects=False)

        assert resp.status_code == 307
        assert resp.headers["location"] == COVERAGE_STATUS["target_url"]

    def test_redirects_for_failed_coverage(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[FAILED_COVERAGE_STATUS]
        )

        resp = client.get("/badge/redirect/owner/repo/", follow_redirects=False)

        assert resp.status_code == 307
        assert resp.headers["location"] == FAILED_COVERAGE_STATUS["target_url"]

    def test_returns_404_when_no_coverage_status(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(json=[])

        resp = client.get("/badge/redirect/owner/repo/")

        assert resp.status_code == 404

    def test_returns_404_when_no_commits(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        respx_mock.get("/repos/owner/repo/commits").respond(json=[])

        resp = client.get("/badge/redirect/owner/repo/")

        assert resp.status_code == 404

    def test_returns_404_when_commit_has_only_non_coverage_status(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        respx_mock.get(f"/repos/owner/repo/statuses/{commit['sha']}").respond(
            json=[NON_COVERAGE_STATUS]
        )

        resp = client.get("/badge/redirect/owner/repo/")

        assert resp.status_code == 404

    @respx.mock(assert_all_called=False)
    def test_skips_skip_ci_commits(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        skip_ci_commit = get_commit(skip_ci=True)
        commit_2 = get_commit()

        commits_route = respx_mock.get("/repos/owner/repo/commits").respond(
            json=[skip_ci_commit, commit_2]
        )
        commit_with_status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit_2['sha']}"
        ).respond(json=[COVERAGE_STATUS])
        # The route below should not be called since the commit has "skip ci"
        skip_ci_commit_status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{skip_ci_commit['sha']}"
        )

        resp = client.get("/badge/redirect/owner/repo/", follow_redirects=False)

        assert resp.status_code == 307
        assert resp.headers["location"] == COVERAGE_STATUS["target_url"]

        assert commits_route.called
        assert commit_with_status_route.called
        assert not skip_ci_commit_status_route.called

    @respx.mock(assert_all_called=False)
    def test_check_up_to_5_commits(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commits = [get_commit(skip_ci=True) for _ in range(4)]
        commits.append(get_commit())

        respx_mock.get("/repos/owner/repo/commits").respond(json=commits)

        # Fifth commit has coverage status, the first 4 have "skip ci" and should be skipped
        respx_mock.get(f"/repos/owner/repo/statuses/{commits[4]['sha']}").respond(
            json=[COVERAGE_STATUS]
        )

        # The routes below should not be called since those commits have "skip ci"
        paths = tuple(
            f"/repos/owner/repo/statuses/{commit['sha']}" for commit in commits[:4]
        )
        skip_ci_commits_routes = respx_mock.route(path__in=paths)

        resp = client.get("/badge/redirect/owner/repo/", follow_redirects=False)

        assert resp.status_code == 307
        assert resp.headers["location"] == COVERAGE_STATUS["target_url"]

        assert not skip_ci_commits_routes.called

    @respx.mock(assert_all_called=False)
    def test_stop_on_first_commit_without_skip_ci(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit_without_coverage_status = get_commit()
        commit_with_coverage_status = get_commit()

        respx_mock.get("/repos/owner/repo/commits").respond(
            json=[commit_without_coverage_status, commit_with_coverage_status]
        )
        respx_mock.get(
            f"/repos/owner/repo/statuses/{commit_without_coverage_status['sha']}"
        ).respond(json=[NON_COVERAGE_STATUS])

        # The route below should not be called
        commit_2_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit_with_coverage_status['sha']}"
        )

        resp = client.get("/badge/redirect/owner/repo/")

        assert resp.status_code == 404
        assert not commit_2_route.called

    def test_retry_on_github_api_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit = get_commit()
        commits_route = respx_mock.get("/repos/owner/repo/commits").mock(
            side_effect=[
                httpx.ConnectError("boom"),  # 1st call: network error
                httpx.Response(  # 2nd call: server error
                    500, json={"message": "server error"}
                ),
                httpx.Response(200, json=[commit]),  # 3rd call: success
            ]
        )

        status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit['sha']}"
        ).mock(
            side_effect=[
                httpx.ConnectError("boom"),  # 1st call: network error
                httpx.Response(  # 2nd call: server error
                    500, json={"message": "server error"}
                ),
                httpx.Response(200, json=[COVERAGE_STATUS]),  # 3rd call: success
            ]
        )

        resp = client.get("/badge/redirect/owner/repo/", follow_redirects=False)

        assert resp.status_code == 307
        assert resp.headers["location"] == COVERAGE_STATUS["target_url"]

        assert commits_route.call_count == 3
        assert status_route.call_count == 3

    def test_server_error_on_github_api_commit_route_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commits_route = respx_mock.get("/repos/owner/repo/commits").respond(
            status_code=500
        )

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/redirect/owner/repo/")

        assert commits_route.call_count == 3  # Retries up to 3 times on failure

    def test_server_error_on_github_api_status_route_failure(
        self,
        client: TestClient,
        respx_mock: respx.MockRouter,
    ):
        commit = get_commit()
        respx_mock.get("/repos/owner/repo/commits").respond(json=[commit])
        status_route = respx_mock.get(
            f"/repos/owner/repo/statuses/{commit['sha']}"
        ).respond(status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            client.get("/badge/redirect/owner/repo/")

        assert status_route.call_count == 3  # Retries up to 3 times on failure
