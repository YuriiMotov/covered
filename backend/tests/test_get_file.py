from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from tests.helpers import SITE_ID


def _mock_s3_body(mock_s3_client: AsyncMock, content: bytes) -> None:
    body = AsyncMock()
    body.read.return_value = content
    mock_s3_client.get_object.return_value = {"Body": body}


class TestGetFile:
    def test_returns_html_content(self, client: TestClient, mock_s3_client: AsyncMock):
        _mock_s3_body(mock_s3_client, b"<html>hello</html>")

        resp = client.get(f"/coverage/{SITE_ID}/index.html")

        assert resp.status_code == 200
        assert resp.content == b"<html>hello</html>"
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        mock_s3_client.get_object.assert_awaited_once_with(
            Bucket="test-bucket",
            Key=f"sites/{SITE_ID}/index.html",
        )

    def test_returns_css_content_type(
        self, client: TestClient, mock_s3_client: AsyncMock
    ):
        _mock_s3_body(mock_s3_client, b"body {}")

        resp = client.get(f"/coverage/{SITE_ID}/style.css")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/css; charset=utf-8"
        assert resp.content == b"body {}"

    def test_unknown_extension_returns_octet_stream(
        self, client: TestClient, mock_s3_client: AsyncMock
    ):
        _mock_s3_body(mock_s3_client, b"data")

        resp = client.get(f"/coverage/{SITE_ID}/file.unknownext")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.content == b"data"

    def test_missing_file_returns_404(
        self, client: TestClient, mock_s3_client: AsyncMock
    ):
        mock_s3_client.get_object.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchKey"}},
            operation_name="GetObject",
        )

        resp = client.get(f"/coverage/{SITE_ID}/missing.html")

        assert resp.status_code == 404

    def test_unhandled_s3_error_returns_503(
        self, client: TestClient, mock_s3_client: AsyncMock
    ):
        mock_s3_client.get_object.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="GetObject",
        )

        resp = client.get(f"/coverage/{SITE_ID}/index.html")

        assert resp.status_code == 503

    def test_empty_path_serves_index_html(
        self, client: TestClient, mock_s3_client: AsyncMock
    ):
        _mock_s3_body(mock_s3_client, b"<html>index</html>")

        client.get(f"/coverage/{SITE_ID}/")

        mock_s3_client.get_object.assert_awaited_once_with(
            Bucket="test-bucket",
            Key=f"sites/{SITE_ID}/index.html",
        )

    def test_directory_path_serves_index_html(
        self, client: TestClient, mock_s3_client: AsyncMock
    ):
        _mock_s3_body(mock_s3_client, b"<html>index</html>")

        client.get(f"/coverage/{SITE_ID}/subdir/")

        mock_s3_client.get_object.assert_awaited_once_with(
            Bucket="test-bucket",
            Key=f"sites/{SITE_ID}/subdir/index.html",
        )

    @pytest.mark.parametrize(
        "site_id",
        [
            pytest.param("short", id="too-short"),
            pytest.param("aabbccddeeff11", id="too-long"),
            pytest.param("AABBCCDDEEFF", id="uppercase-hex"),
            pytest.param("xxxxxxxxxxxx", id="non-hex-chars"),
        ],
    )
    def test_invalid_site_id_returns_422(self, client: TestClient, site_id: str):
        resp = client.get(f"/coverage/{site_id}/index.html")

        assert resp.status_code == 422
        resp_json = resp.json()
        assert resp_json["detail"][0]["loc"] == ["path", "site_id"]
        assert resp_json["detail"][0]["msg"].startswith("String should match pattern")

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("%2e%2e/etc/passwd", id="parent-traversal"),
            pytest.param("foo/%2e%2e/%2e%2e/etc/passwd", id="nested-traversal"),
            pytest.param("foo/%2e%2e/bar", id="mid-path-traversal"),
        ],
    )
    def test_path_traversal_returns_422(self, client: TestClient, path: str):
        resp = client.get(f"/coverage/{SITE_ID}/{path}")

        decoded_path = path.replace("%2e", ".")
        assert resp.request.url.path == f"/coverage/{SITE_ID}/{decoded_path}"

        assert resp.status_code == 422
        resp_json = resp.json()
        assert resp_json["detail"][0]["loc"] == ["path", "path"]
        assert resp_json["detail"][0]["msg"].startswith("String should match pattern")
