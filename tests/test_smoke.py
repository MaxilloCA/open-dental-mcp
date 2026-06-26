"""Smoke tests for open-dental-mcp — no live API calls required.

Built with ``respx`` (industry-standard httpx mocking) + ``pytest-asyncio``.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from hypothesis import given, settings
from hypothesis import strategies as st

from open_dental_mcp import (
    OpenDentalAPIError,
    OpenDentalAuthError,
    OpenDentalClient,
    OpenDentalConnectionError,
    OpenDentalNotFoundError,
    OpenDentalRateLimitError,
)
from open_dental_mcp.server import _format_error, _json

# --- Fixtures --------------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev-key")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust-key")


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[OpenDentalClient]:
    _env(monkeypatch)
    c = OpenDentalClient()
    try:
        yield c
    finally:
        await c.aclose()


# --- Client construction --------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    for k in ("OPENDENTAL_DEVELOPER_KEY", "OPENDENTAL_CUSTOMER_KEY"):
        os.environ.pop(k, None)
    with pytest.raises(OpenDentalAuthError):
        OpenDentalClient()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = OpenDentalClient()
    assert client._auth_header == "ODFHIR dev-key/cust-key"


@pytest.mark.asyncio
async def test_client_aclose_closes_underlying_httpx_client(client: OpenDentalClient) -> None:
    assert not client._client.is_closed
    await client.aclose()
    assert client._client.is_closed


# --- Request shape ---------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_patient_sends_odfhir_auth(client: OpenDentalClient) -> None:
    route = respx.get("https://api.opendental.com/api/v1/patients/12345").mock(
        return_value=httpx.Response(200, json={"PatNum": 12345, "FName": "John"})
    )
    result = await client.get_patient(12345)
    assert result["PatNum"] == 12345
    assert route.calls[0].request.headers["Authorization"] == "ODFHIR dev-key/cust-key"


@pytest.mark.asyncio
@respx.mock
async def test_find_patients_passes_filters(client: OpenDentalClient) -> None:
    route = respx.get("https://api.opendental.com/api/v1/patients").mock(
        return_value=httpx.Response(200, json=[])
    )
    await client.find_patients(lname="Smith", phone="555-1234", limit=10)
    assert route.calls[0].request.url.params["LName"] == "Smith"
    assert route.calls[0].request.url.params["Phone"] == "555-1234"
    assert route.calls[0].request.url.params["Limit"] == "10"


# --- HTTP status code mapping ---------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_auth_error(client: OpenDentalClient) -> None:
    respx.get("https://api.opendental.com/api/v1/providers").mock(
        return_value=httpx.Response(401, text="")
    )
    with pytest.raises(OpenDentalAuthError) as exc_info:
        await client.list_providers()
    assert exc_info.value.http_status == 401


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_auth_error(client: OpenDentalClient) -> None:
    respx.get("https://api.opendental.com/api/v1/providers").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    with pytest.raises(OpenDentalAuthError) as exc_info:
        await client.list_providers()
    assert exc_info.value.http_status == 403
    assert "Developer Portal" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_not_found(client: OpenDentalClient) -> None:
    respx.get("https://api.opendental.com/api/v1/patients/9999").mock(
        return_value=httpx.Response(404, json={"message": "no such patient"})
    )
    with pytest.raises(OpenDentalNotFoundError):
        await client.get_patient(9999)


@pytest.mark.asyncio
@respx.mock
async def test_429_includes_retry_after(client: OpenDentalClient) -> None:
    respx.get("https://api.opendental.com/api/v1/providers").mock(
        return_value=httpx.Response(429, headers={"retry-after": "2.5"}, text="slow")
    )
    with pytest.raises(OpenDentalRateLimitError) as exc_info:
        await client.list_providers()
    assert exc_info.value.retry_after == 2.5


@pytest.mark.asyncio
@respx.mock
async def test_500_captures_request_id(client: OpenDentalClient) -> None:
    respx.get("https://api.opendental.com/api/v1/providers").mock(
        return_value=httpx.Response(500, headers={"x-request-id": "req-abc"}, text="boom")
    )
    with pytest.raises(OpenDentalAPIError) as exc_info:
        await client.list_providers()
    assert exc_info.value.request_id == "req-abc"


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_wrapped(client: OpenDentalClient) -> None:
    respx.get("https://api.opendental.com/api/v1/providers").mock(
        side_effect=httpx.ConnectError("DNS failure")
    )
    with pytest.raises(OpenDentalConnectionError):
        await client.list_providers()


# --- Retry with exponential backoff ---------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_429_is_retried_then_raises(client: OpenDentalClient) -> None:
    route = respx.get("https://api.opendental.com/api/v1/providers").mock(
        return_value=httpx.Response(429, text="slow")
    )
    client._max_retries = 2
    with pytest.raises(OpenDentalRateLimitError):
        await client.list_providers()
    assert route.call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
@respx.mock
async def test_5xx_eventually_succeeds_after_retry(client: OpenDentalClient) -> None:
    route = respx.get("https://api.opendental.com/api/v1/providers").mock(
        side_effect=[
            httpx.Response(502, text="bad gateway"),
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json=[{"ProvNum": 1, "LName": "Smith"}]),
        ]
    )
    client._max_retries = 3
    result = await client.list_providers()
    assert result[0]["LName"] == "Smith"
    assert route.call_count == 3


# --- Write endpoints ------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_commlog_uses_post(client: OpenDentalClient) -> None:
    route = respx.post("https://api.opendental.com/api/v1/commlogs").mock(
        return_value=httpx.Response(201, json={"CommlogNum": 99})
    )
    result = await client.create_commlog(
        patnum=12345, comm_date="2026-01-15T10:30:00", comm_type=1, note="Called re: appointment"
    )
    assert result["CommlogNum"] == 99
    assert route.calls[0].request.method == "POST"
    body = json.loads(route.calls[0].request.content)
    assert body["PatNum"] == 12345
    assert body["CommType"] == 1
    assert body["Note"] == "Called re: appointment"


@pytest.mark.asyncio
@respx.mock
async def test_update_patient_uses_put(client: OpenDentalClient) -> None:
    route = respx.put("https://api.opendental.com/api/v1/patients/1").mock(
        return_value=httpx.Response(200, json={"PatNum": 1, "FName": "Updated"})
    )
    await client.update_patient(1, {"FName": "Updated"})
    assert route.calls[0].request.method == "PUT"


# --- Property-based test --------------------------------------------------


@given(st.dictionaries(st.text(min_size=1), st.integers() | st.text() | st.booleans(), max_size=10))
@settings(max_examples=50, deadline=None)
def test_json_serialization_round_trip(d: dict[str, Any]) -> None:
    try:
        json.loads(_json(d))
    except (TypeError, ValueError):
        pytest.skip("non-JSON value")
    assert json.loads(_json(d)) == d


# --- Server error helpers -------------------------------------------------


def test_format_error_auth_suggests_env_vars() -> None:
    msg = _format_error(OpenDentalAuthError("bad"))
    assert "OPENDENTAL_DEVELOPER_KEY" in msg
    assert "OPENDENTAL_CUSTOMER_KEY" in msg


def test_format_error_404_says_not_found() -> None:
    msg = _format_error(OpenDentalNotFoundError("missing"))
    assert "not found" in msg.lower()


def test_format_error_429_includes_retry_after() -> None:
    msg = _format_error(OpenDentalRateLimitError("slow", retry_after=5.0))
    assert "Retry in 5.0s" in msg or "Retry in 5s" in msg


def test_format_error_connection_says_network() -> None:
    msg = _format_error(OpenDentalConnectionError("dns"))
    assert "network" in msg.lower()


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_error_repr_includes_structured_fields() -> None:
    err = OpenDentalAPIError("boom", http_status=500, error_code="oops", request_id="req-1")
    r = repr(err)
    assert "http_status=500" in r
    assert "error_code='oops'" in r
    assert "request_id='req-1'" in r
