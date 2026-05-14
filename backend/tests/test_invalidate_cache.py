from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from redis import RedisError


class TestInvalidateCacheAuth:
    def test_missing_token_returns_401(self, client: TestClient):
        resp = client.post("/coverage/invalidate-cache/owner/repo/")
        assert resp.status_code == 401

    def test_invalid_token_returns_403(self, client: TestClient):
        resp = client.post(
            "/coverage/invalidate-cache/owner/repo/",
            headers={"token": "wrong-key"},
        )
        assert resp.status_code == 403

    def test_valid_token_returns_200(
        self,
        client: TestClient,
        mock_redis: AsyncMock,
        api_key: str,
    ):
        resp = client.post(
            "/coverage/invalidate-cache/owner/repo/",
            headers={"token": api_key},
        )
        assert resp.status_code == 200


class TestInvalidateCache:
    def test_deletes_badge_cache_key(
        self,
        client: TestClient,
        mock_redis: AsyncMock,
        api_key: str,
    ):
        resp = client.post(
            "/coverage/invalidate-cache/owner/repo/",
            headers={"token": api_key},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "success"}
        mock_redis.delete.assert_awaited_once_with("cache:badge:owner:repo")

    def test_returns_500_on_redis_error(
        self,
        client: TestClient,
        mock_redis: AsyncMock,
        api_key: str,
    ):
        mock_redis.delete.side_effect = RedisError("connection refused")

        resp = client.post(
            "/coverage/invalidate-cache/owner/repo/",
            headers={"token": api_key},
        )

        assert resp.status_code == 500

    def test_invalidates_cache_key_per_repo(
        self,
        client: TestClient,
        mock_redis: AsyncMock,
        api_key: str,
    ):
        client.post(
            "/coverage/invalidate-cache/some-org/some-repo/",
            headers={"token": api_key},
        )

        mock_redis.delete.assert_awaited_once_with("cache:badge:some-org:some-repo")

    def test_no_redis_configured_does_not_error(
        self,
        client: TestClient,
        mock_redis: AsyncMock,
        disable_redis: None,
        api_key: str,
    ):
        resp = client.post(
            "/coverage/invalidate-cache/owner/repo/",
            headers={"token": api_key},
        )

        assert resp.status_code == 200
        mock_redis.delete.assert_not_called()
