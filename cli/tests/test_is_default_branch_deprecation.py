from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from covered.cli import app
from utils import COMMON_ENV

runner = CliRunner()


def test_is_default_branch_true(mock_main: AsyncMock):
    with (
        pytest.warns(
            DeprecationWarning,
            match=(
                "The `--is-default-branch` option is deprecated and will be removed in "
                "a future release. Please use `--purge-cache` instead."
            ),
        ),
    ):
        result = runner.invoke(app, [".", "--is-default-branch"], env=COMMON_ENV)

    assert result.exit_code == 0, result.stderr

    mock_main.assert_awaited_once()

    call_args = mock_main.call_args_list[0]
    purge_cache_arg = call_args.kwargs.get("purge_cache")
    assert purge_cache_arg is True


def test_is_default_branch_false(mock_main: AsyncMock):
    with (
        pytest.warns(
            DeprecationWarning,
            match=(
                "The `--is-default-branch` option is deprecated and will be removed in "
                "a future release. Please use `--purge-cache` instead."
            ),
        ),
    ):
        result = runner.invoke(app, [".", "--no-is-default-branch"], env=COMMON_ENV)

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
def test_is_default_branch_and_purge_cache(
    is_default_branch_flag: str, mock_main: AsyncMock
):
    with (
        pytest.warns(
            DeprecationWarning,
            match=(
                "The `--is-default-branch` option is deprecated and will be removed in "
                "a future release. Please use `--purge-cache` instead."
            ),
        ),
    ):
        result = runner.invoke(
            app, [".", is_default_branch_flag, "--purge-cache"], env=COMMON_ENV
        )

    assert result.exit_code == 0, result.stderr

    mock_main.assert_awaited_once()

    call_args = mock_main.call_args_list[0]
    purge_cache_arg = call_args.kwargs.get("purge_cache")
    assert purge_cache_arg is True
