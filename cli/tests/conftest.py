import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
import stamina


@pytest.fixture
def mock_main() -> Iterator[AsyncMock]:
    with patch("covered.cli._main", AsyncMock(return_value=None)) as m:
        yield m


@pytest.fixture(autouse=True)
def _isolate_covered_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Clear `COVERED_*` env vars from the parent shell so tests aren't polluted.
    """
    for var in [k for k in os.environ if k.startswith("COVERED_")]:
        monkeypatch.delenv(var)  # pragma: no cover


@pytest.fixture(autouse=True, scope="session")
def _set_stamina_testing():
    stamina.set_testing(True, attempts=10, cap=True)
