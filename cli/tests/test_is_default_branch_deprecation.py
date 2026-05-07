from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from covered.cli import app

runner = CliRunner()

common_env = {
    "COVERED_API_KEY": "test_api_key",
    "COVERED_API_URL": "https://api.example.com",
    "COVERED_GH_TOKEN": "test_github_token",
    "COVERED_REPO_OWNER": "test_owner",
    "COVERED_REPO_NAME": "test_repo",
    "COVERED_COMMIT_SHA": "test_commit_sha",
}


def test_is_default_branch_true():
    with (
        patch("covered.cli._main", AsyncMock(return_value=None)) as mock_main,
        pytest.warns(
            DeprecationWarning,
            match=(
                "The `--is-default-branch` option is deprecated and will be removed in "
                "a future release. Please use `--purge-cache` instead."
            ),
        ),
    ):
        result = runner.invoke(app, [".", "--is-default-branch"], env=common_env)

    assert result.exit_code == 0, result.stderr

    mock_main.assert_awaited_once()

    call_args = mock_main.call_args_list[0]
    purge_cache_arg = call_args.kwargs.get("purge_cache")
    assert purge_cache_arg is True


def test_is_default_branch_false():
    with (
        patch("covered.cli._main", AsyncMock(return_value=None)) as mock_main,
        pytest.warns(
            DeprecationWarning,
            match=(
                "The `--is-default-branch` option is deprecated and will be removed in "
                "a future release. Please use `--purge-cache` instead."
            ),
        ),
    ):
        result = runner.invoke(app, [".", "--no-is-default-branch"], env=common_env)

    assert result.exit_code == 0, result.stderr

    mock_main.assert_awaited_once()

    call_args = mock_main.call_args_list[0]
    purge_cache_arg = call_args.kwargs.get("purge_cache")
    assert purge_cache_arg is False


@pytest.mark.parametrize(
    "is_default_branch_flag",
    [
        "--is-default-branch",
        "--no-is-default-branch",
    ],
)
def test_is_default_branch_and_purge_cache(is_default_branch_flag: str):
    with (
        patch("covered.cli._main", AsyncMock(return_value=None)) as mock_main,
        pytest.warns(
            DeprecationWarning,
            match=(
                "The `--is-default-branch` option is deprecated and will be removed in "
                "a future release. Please use `--purge-cache` instead."
            ),
        ),
    ):
        result = runner.invoke(
            app, [".", is_default_branch_flag, "--purge-cache"], env=common_env
        )

    assert result.exit_code == 0, result.stderr

    mock_main.assert_awaited_once()

    call_args = mock_main.call_args_list[0]
    purge_cache_arg = call_args.kwargs.get("purge_cache")
    assert purge_cache_arg is True
