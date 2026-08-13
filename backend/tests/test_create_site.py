from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.utils.aws_storage import AWSStorageError
from tests.helpers import STS_CREDENTIALS


class TestCreateSiteAuth:
    def test_missing_token_returns_401(self, client: TestClient):
        resp = client.post("/coverage/create-site/")
        assert resp.status_code == 401

    def test_invalid_token_returns_403(self, client: TestClient):
        resp = client.post("/coverage/create-site/", headers={"token": "wrong-key"})
        assert resp.status_code == 403

    def test_valid_token_returns_200(
        self,
        client: TestClient,
        mock_sts_client: AsyncMock,
        api_key: str,
    ):
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        resp = client.post("/coverage/create-site/", headers={"token": api_key})

        assert resp.status_code == 200


class TestCreateSite:
    def test_returns_session(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        mock_sts_client: AsyncMock,
        api_key: str,
    ):
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        resp = client.post("/coverage/create-site/", headers={"token": api_key})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["site_id"]) == 12
        assert all(c in "0123456789abcdef" for c in data["site_id"])
        assert data["bucket"] == "test-bucket"
        assert data["region"] == "us-east-1"
        assert data["access_key_id"] == STS_CREDENTIALS["AccessKeyId"]
        assert data["secret_access_key"] == STS_CREDENTIALS["SecretAccessKey"]
        assert data["session_token"] == STS_CREDENTIALS["SessionToken"]

        # S3 put_object called to reserve the site_id prefix
        mock_s3_client.put_object.assert_awaited_once()
        put_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert put_kwargs["Bucket"] == "test-bucket"
        assert put_kwargs["Key"] == f"sites/{data['site_id']}/.keep"

    def test_retries_on_site_id_collision(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        mock_sts_client: AsyncMock,
        api_key: str,
    ):
        collision = ClientError(
            error_response={"Error": {"Code": "PreconditionFailed"}},
            operation_name="PutObject",
        )
        mock_s3_client.put_object.side_effect = [collision, collision, {}]
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        resp = client.post("/coverage/create-site/", headers={"token": api_key})

        assert resp.status_code == 200
        assert mock_s3_client.put_object.await_count == 3

    def test_fails_after_max_attempts_on_site_id_collision(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        mock_sts_client: AsyncMock,
        api_key: str,
    ):
        collision = ClientError(
            error_response={"Error": {"Code": "PreconditionFailed"}},
            operation_name="PutObject",
        )
        mock_s3_client.put_object.side_effect = [collision, collision, collision]
        mock_sts_client.assume_role.return_value = {"Credentials": STS_CREDENTIALS}

        with pytest.raises(
            AWSStorageError,
            match="Failed to generate unique site ID after multiple attempts",
        ):
            client.post("/coverage/create-site/", headers={"token": api_key})

        assert mock_s3_client.put_object.await_count == 3

    def test_s3_error_propagates(
        self,
        client: TestClient,
        mock_s3_client: AsyncMock,
        api_key: str,
    ):
        mock_s3_client.put_object.side_effect = ClientError(
            error_response={"Error": {"Code": "InternalError"}},
            operation_name="PutObject",
        )

        with pytest.raises(AWSStorageError, match="Failed to create site directory"):
            client.post("/coverage/create-site/", headers={"token": api_key})
