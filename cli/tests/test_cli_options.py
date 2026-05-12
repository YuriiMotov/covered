"""
Tests for the Typer `upload` command - argument validation, env-var wiring, and defaults.
"""

from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from covered.cli import app

from utils import COMMON_ENV

runner = CliRunner()


def test_upload_rejects_api_url_with_trailing_slash(
    mock_main: AsyncMock, tmp_path: Path
):
    """
    `--api-url https://x/` (with trailing slash) exits non-zero with a clear message.
    """
    env = {**COMMON_ENV, "COVERED_API_URL": "https://api.example.com/"}

    result = runner.invoke(app, [str(tmp_path)], env=env)

    assert result.exit_code != 0
    assert "must not end with a slash" in result.stderr
    assert "--api-url" in result.stderr
    mock_main.assert_not_awaited()


def test_upload_requires_existing_directory(mock_main: AsyncMock, tmp_path: Path):
    """
    A directory argument that does not exist fails Typer's `exists=True` validation.
    """
    missing = tmp_path / "does-not-exist"

    result = runner.invoke(app, [str(missing)], env=COMMON_ENV)

    assert result.exit_code != 0
    mock_main.assert_not_awaited()


def test_upload_rejects_file_as_directory_argument(
    mock_main: AsyncMock, tmp_path: Path
):
    """
    Passing a regular file (not a directory) fails validation because `file_okay=False`.
    """
    file_path = tmp_path / "report.html"
    file_path.write_text("<html></html>")

    result = runner.invoke(app, [str(file_path)], env=COMMON_ENV)

    assert result.exit_code != 0
    mock_main.assert_not_awaited()


def test_upload_reads_all_options_from_env_vars(mock_main: AsyncMock, tmp_path: Path):
    """
    Every `COVERED_*` env var is consumed and forwarded to `_main` as expected kwargs.
    """
    env = {
        **COMMON_ENV,
        "COVERED_COVERAGE_THRESHOLD": "75.5",
        "COVERED_PURGE_CACHE": "true",
    }

    result = runner.invoke(app, [str(tmp_path)], env=env)

    assert result.exit_code == 0, result.stderr
    kwargs = mock_main.call_args.kwargs
    assert kwargs["directory"] == tmp_path
    assert kwargs["api_url"] == "https://api.example.com"
    assert kwargs["api_key"] == "test_api_key"
    assert kwargs["repo_owner"] == "test_owner"
    assert kwargs["repo_name"] == "test_repo"
    assert kwargs["commit_sha"] == "test_commit_sha"
    assert kwargs["gh_token"] == "test_github_token"
    assert kwargs["coverage_threshold"] == 75.5
    assert kwargs["purge_cache"] is True


def test_upload_cli_flags_override_env_vars(mock_main: AsyncMock, tmp_path: Path):
    """
    Explicit `--api-url` (and other flags) take precedence over the corresponding env
    vars.
    """
    result = runner.invoke(
        app,
        [str(tmp_path), "--api-url", "https://override.example.com"],
        env=COMMON_ENV,
    )

    assert result.exit_code == 0, result.stderr
    assert mock_main.call_args.kwargs["api_url"] == "https://override.example.com"


def test_upload_missing_required_option_fails(mock_main: AsyncMock, tmp_path: Path):
    """
    Omitting a required option (e.g. `--api-key` / `COVERED_API_KEY`) exits non-zero
    with a clear message.
    """
    env = {k: v for k, v in COMMON_ENV.items() if k != "COVERED_API_KEY"}

    result = runner.invoke(app, [str(tmp_path)], env=env)

    assert result.exit_code != 0
    assert "--api-key" in result.stderr
    mock_main.assert_not_awaited()


def test_upload_default_concurrency_is_50(mock_main: AsyncMock, tmp_path: Path):
    """
    When `--concurrency` is not provided, `_main` receives `concurrency=50`.
    """
    result = runner.invoke(app, [str(tmp_path)], env=COMMON_ENV)

    assert result.exit_code == 0, result.stderr
    assert mock_main.call_args.kwargs["concurrency"] == 50


def test_upload_default_coverage_threshold_is_100(mock_main: AsyncMock, tmp_path: Path):
    """
    `--coverage-threshold` defaults to 100.0.
    """
    result = runner.invoke(app, [str(tmp_path)], env=COMMON_ENV)

    assert result.exit_code == 0, result.stderr
    assert mock_main.call_args.kwargs["coverage_threshold"] == 100.0


def test_upload_purge_cache_flag_propagates_true(mock_main: AsyncMock, tmp_path: Path):
    """
    `--purge-cache` results in `purge_cache=True` being passed to `_main`.
    """
    result = runner.invoke(app, [str(tmp_path), "--purge-cache"], env=COMMON_ENV)

    assert result.exit_code == 0, result.stderr
    assert mock_main.call_args.kwargs["purge_cache"] is True


def test_upload_no_purge_cache_default_is_false(mock_main: AsyncMock, tmp_path: Path):
    """
    Without `--purge-cache` (and no env override), `_main` receives `purge_cache=False`.
    """
    result = runner.invoke(app, [str(tmp_path)], env=COMMON_ENV)

    assert result.exit_code == 0, result.stderr
    assert mock_main.call_args.kwargs["purge_cache"] is False


def test_upload_help_lists_all_options():
    """
    `--help` output mentions each documented option - sanity check against accidental
    removal.
    """
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    for option in [
        "DIRECTORY",
        "--api-url",
        "--api-key",
        "--concurrency",
        "--repo-owner",
        "--repo-name",
        "--commit-sha",
        "--coverage-threshold",
        "--gh-token",
        "--is-default-branch",
        "--purge-cache",
    ]:
        assert option in result.output, f"missing {option} in help output"
