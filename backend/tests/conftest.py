import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
import stamina

from app.config import get_settings
from app.dependencies.redis_client import get_redis_client
from app.application import app

TEST_API_KEY = "test-api-key"


@pytest.fixture(scope="module", autouse=True)
def _use_test_settings() -> None:
    """Env vars must be set before the lifespan runs."""
    os.environ["API_KEY"] = TEST_API_KEY
    os.environ["AWS_ACCESS_KEY_ID"] = "fake-access-key"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "fake-secret-key"
    os.environ["AWS_BUCKET"] = "test-bucket"
    os.environ["AWS_REGION"] = "us-east-1"
    os.environ["AWS_UPLOAD_ROLE_ARN"] = "arn:aws:iam::123456789012:role/fake"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["GITHUB_TOKEN"] = "fake-github-token"
    get_settings.cache_clear()


@pytest.fixture
def api_key() -> str:
    return TEST_API_KEY


@pytest.fixture(scope="module")
def mock_s3_client() -> AsyncMock:
    """Mock for aiobotocore S3 client (AWSStorage._client)."""
    return AsyncMock()


@pytest.fixture(scope="module")
def mock_sts_client() -> AsyncMock:
    """Mock for aiobotocore STS client used by AWSStorage.create_upload_session."""
    return AsyncMock()


@pytest.fixture(scope="module", autouse=True)
def _patch_aiobotocore(
    mock_s3_client: AsyncMock, mock_sts_client: AsyncMock
) -> Iterator[None]:
    """Patch aiobotocore so the lifespan-created AWSStorage gets mocked clients."""

    @asynccontextmanager
    async def _s3_ctx():
        yield mock_s3_client

    @asynccontextmanager
    async def _sts_ctx():
        yield mock_sts_client

    def fake_create_client(self, service, **kwargs):
        if service == "s3":
            return _s3_ctx()
        if service == "sts":
            return _sts_ctx()
        raise ValueError(  # pragma: no cover
            f"Unexpected AWS service in tests: {service}"
        )

    with patch("aiobotocore.session.AioSession.create_client", fake_create_client):
        yield


@pytest.fixture(autouse=True)
def _reset_aws_mocks(mock_s3_client: AsyncMock, mock_sts_client: AsyncMock) -> None:
    """Clear mock state between tests."""
    mock_s3_client.reset_mock(return_value=True, side_effect=True)
    mock_sts_client.reset_mock(return_value=True, side_effect=True)


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture(scope="module")
def client(_patch_aiobotocore: None) -> Iterator[TestClient]:
    """Lifespan runs once per module; aiobotocore is already patched."""
    with TestClient(app) as tc:
        yield tc


@pytest.fixture(name="override_redis", autouse=True)
def _override_redis(mock_redis: AsyncMock) -> Iterator[None]:
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def disable_redis(
    override_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Disable Redis for a test."""
    old_dep = app.dependency_overrides.get(get_redis_client)
    app.dependency_overrides[get_redis_client] = lambda: None
    yield
    if old_dep is not None:
        app.dependency_overrides[get_redis_client] = old_dep


@pytest.fixture(autouse=True, scope="session")
def set_stamina_testing():
    stamina.set_testing(True, attempts=10, cap=True)
