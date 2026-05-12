"""
Tests for `_request` - HTTP helper with retries via `stamina` (mocked with `respx`).
"""

import json
from unittest.mock import patch

import httpx
import pytest
import respx

from covered.cli import _request


async def test_request_returns_response_on_first_success(respx_mock: respx.MockRouter):
    """
    Single attempt, no retry; response object is returned.
    """
    route = respx_mock.get("https://example.com").mock(
        return_value=httpx.Response(200, text="ok")
    )

    resp = await _request("GET", "https://example.com")

    assert resp.status_code == 200
    assert resp.text == "ok"
    assert route.call_count == 1


async def test_request_retries_on_transport_error_then_succeeds(
    respx_mock: respx.MockRouter,
):
    """
    First call raises `httpx.TransportError`; retry succeeds and returns the response.
    """
    route = respx_mock.get("https://example.com").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200)]
    )

    resp = await _request("GET", "https://example.com")

    assert resp.status_code == 200
    assert route.call_count == 2


async def test_request_retries_on_5xx_then_succeeds(respx_mock: respx.MockRouter):
    """
    First response is 503; retry returns 200 and that response is returned.
    """
    route = respx_mock.get("https://example.com").mock(
        side_effect=[httpx.Response(503), httpx.Response(200)]
    )

    resp = await _request("GET", "https://example.com")

    assert resp.status_code == 200
    assert route.call_count == 2


async def test_request_does_not_retry_on_4xx(respx_mock: respx.MockRouter):
    """
    A 404 response is returned without retry - only 5xx status triggers `raise_for_status`.
    """
    route = respx_mock.get("https://example.com").mock(return_value=httpx.Response(404))

    resp = await _request("GET", "https://example.com")

    assert resp.status_code == 404
    assert route.call_count == 1


async def test_request_gives_up_after_three_attempts(respx_mock: respx.MockRouter):
    """
    Three consecutive failures raise.
    """
    route = respx_mock.get("https://example.com").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await _request("GET", "https://example.com")

    assert route.call_count == 3


async def test_request_passes_parameters(respx_mock: respx.MockRouter):
    """
    Parameters like `headers` and `json` are forwarded to the underlying httpx request.
    """
    route = respx_mock.post("https://example.com/api").mock(
        return_value=httpx.Response(200)
    )

    headers = {"X-Custom": "value", "Authorization": "Bearer abc"}
    body = {"key": "value", "num": 42}

    await _request("POST", "https://example.com/api", headers=headers, json=body)

    request = route.calls.last.request
    assert request.headers["X-Custom"] == "value"
    assert request.headers["Authorization"] == "Bearer abc"
    assert json.loads(request.content) == body


async def test_request_uses_configured_timeout(respx_mock: respx.MockRouter):
    """
    The custom `timeout` value is propagated to the underlying `AsyncClient`.
    """
    respx_mock.get("https://example.com").mock(return_value=httpx.Response(200))

    with patch.object(httpx, "AsyncClient", wraps=httpx.AsyncClient) as spy:
        await _request("GET", "https://example.com", timeout=5)

    spy.assert_called_once_with(timeout=5)
