"""
Tests for `_upload_files` - concurrent S3 uploads via aiobotocore.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from covered.cli import _upload_files

BUCKET = "test-bucket"
SITE_ID = "site-abc"
SESSION = {
    "site_id": SITE_ID,
    "bucket": BUCKET,
    "region": "us-east-1",
    "access_key_id": "testing-key-id",
    "secret_access_key": "testing-secret",
    "session_token": "test-token",
}


class FakeS3Client:
    """
    Aiobotocore-compatible stub: its own async context manager, exposes `put_object`.
    """

    def __init__(self, put_object: Any) -> None:
        self.put_object = put_object

    async def __aenter__(self) -> "FakeS3Client":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@contextmanager
def patched_aiobotocore(put_object: Any) -> Iterator[MagicMock]:
    """
    Patch `covered.cli.get_session` so the s3 client's `put_object` is `put_object`.
    Yields the session mock so callers can inspect `create_client` args.
    """
    fake_session = MagicMock()
    fake_session.create_client = MagicMock(
        return_value=FakeS3Client(put_object=put_object)
    )
    with patch("covered.cli.get_session", return_value=fake_session):
        yield fake_session


async def test_upload_files_uploads_every_file_recursively(tmp_path: Path):
    """
    Every regular file in a nested directory tree is uploaded to S3.
    """
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "sub" / "deep" / "b.txt").write_text("bbb")
    (tmp_path / "sub" / "c.txt").write_text("ccc")

    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object):
        count = await _upload_files(tmp_path, SESSION, concurrency=2)

    assert count == 3
    keys = {c.kwargs["Key"] for c in put_object.call_args_list}
    assert keys == {
        f"sites/{SITE_ID}/a.txt",
        f"sites/{SITE_ID}/sub/deep/b.txt",
        f"sites/{SITE_ID}/sub/c.txt",
    }


async def test_upload_files_skips_directories(tmp_path: Path):
    """
    Directory entries are not uploaded as objects (only files are).
    """
    (tmp_path / "empty_subdir").mkdir()
    (tmp_path / "another").mkdir()
    (tmp_path / "another" / "file.txt").write_text("x")

    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object):
        count = await _upload_files(tmp_path, SESSION, concurrency=2)

    assert count == 1
    keys = {c.kwargs["Key"] for c in put_object.call_args_list}
    assert keys == {f"sites/{SITE_ID}/another/file.txt"}


async def test_upload_files_uses_relative_key_under_site_prefix(tmp_path: Path):
    """
    S3 object key is `sites/{site_id}/{path-relative-to-directory}`.
    """
    (tmp_path / "report.html").write_text("html")

    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object):
        await _upload_files(tmp_path, SESSION, concurrency=1)

    put_object.assert_called_once_with(
        Bucket=BUCKET,
        Key=f"sites/{SITE_ID}/report.html",
        Body=b"html",
    )


async def test_upload_files_returns_uploaded_count(tmp_path: Path):
    """
    Return value equals the number of files in the tree.
    """
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(str(i))

    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object):
        count = await _upload_files(tmp_path, SESSION, concurrency=2)

    assert count == 5
    assert put_object.call_count == 5


async def test_upload_files_empty_directory_returns_zero(tmp_path: Path):
    """
    Empty directory results in no S3 calls and a return value of 0.
    """
    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object):
        count = await _upload_files(tmp_path, SESSION, concurrency=2)

    assert count == 0
    put_object.assert_not_called()


async def test_upload_files_preserves_file_bytes(tmp_path: Path):
    """
    The body sent to S3 matches `file_path.read_bytes()` exactly (binary-safe).
    """
    binary = bytes(range(256))
    (tmp_path / "blob.bin").write_bytes(binary)

    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object):
        await _upload_files(tmp_path, SESSION, concurrency=1)

    put_object.assert_called_once_with(
        Bucket=BUCKET,
        Key=f"sites/{SITE_ID}/blob.bin",
        Body=binary,
    )


async def test_upload_files_passes_session_credentials_to_s3_client(tmp_path: Path):
    """
    Region, access key, secret, token come from session dict.
    """
    (tmp_path / "f.txt").write_text("x")

    put_object = AsyncMock(return_value={})
    with patched_aiobotocore(put_object) as fake_session:
        await _upload_files(tmp_path, SESSION, concurrency=1)

    fake_session.create_client.assert_called_once_with(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="testing-key-id",
        aws_secret_access_key="testing-secret",
        aws_session_token="test-token",
    )
    put_object.assert_called_once_with(
        Bucket=BUCKET,
        Key=f"sites/{SITE_ID}/f.txt",
        Body=b"x",
    )


async def test_upload_files_respects_concurrency_limit(tmp_path: Path):
    """
    The semaphore caps the number of in-flight uploads at the configured concurrency.
    """
    n_files = 10
    for i in range(n_files):
        (tmp_path / f"f{i}.txt").write_text(str(i))

    in_flight = 0
    peak = 0

    async def put_object(**kwargs: Any) -> dict:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {}

    with patched_aiobotocore(put_object):
        await _upload_files(tmp_path, SESSION, concurrency=3)

    assert peak == 3


async def test_upload_files_propagates_s3_errors(tmp_path: Path):
    """
    If `put_object` raises, the exception is surfaced to the caller.
    """
    (tmp_path / "f1.txt").write_text("x")
    (tmp_path / "f2.txt").write_text("y")

    async def put_object(**kwargs: Any) -> dict:
        raise RuntimeError("upload failed")

    with (
        patched_aiobotocore(put_object),
        pytest.raises(RuntimeError, match="upload failed"),
    ):
        await _upload_files(tmp_path, SESSION, concurrency=2)
