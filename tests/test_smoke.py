"""Smoke tests for open-dental-mcp — no live API calls required."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from open_dental_mcp import OpenDentalAPIError, OpenDentalAuthError, OpenDentalClient
from open_dental_mcp.server import _format_error, _json


# ----- Client construction --------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(OpenDentalAuthError):
            OpenDentalClient()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev-key")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust-key")
    client = OpenDentalClient()
    # Open Dental uses "ODFHIR {dev}/{cust}" in the Authorization header
    assert client._auth_header == "ODFHIR dev-key/cust-key"


# ----- Async request mock ---------------------------------------------------


@pytest.mark.asyncio
async def test_request_uses_correct_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev-key")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust-key")

    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.text = "[1, 2]"
    fake_response.json = lambda: [1, 2]

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)

    with patch("open_dental_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = OpenDentalClient()
        await client.list_providers()
        args, kwargs = fake_http.request.call_args
        assert kwargs["headers"]["Authorization"] == "ODFHIR dev-key/cust-key"


@pytest.mark.asyncio
async def test_401_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust")
    fake_response = AsyncMock()
    fake_response.status_code = 401
    fake_response.text = ""
    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)
    with patch("open_dental_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = OpenDentalClient()
        with pytest.raises(OpenDentalAuthError):
            await client.list_providers()


@pytest.mark.asyncio
async def test_403_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust")
    fake_response = AsyncMock()
    fake_response.status_code = 403
    fake_response.text = "permission denied"
    fake_response.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)
    with patch("open_dental_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = OpenDentalClient()
        with pytest.raises(OpenDentalAuthError):
            await client.list_providers()


@pytest.mark.asyncio
async def test_500_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust")
    fake_response = AsyncMock()
    fake_response.status_code = 500
    fake_response.text = "kaboom"
    fake_response.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)
    with patch("open_dental_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = OpenDentalClient()
        with pytest.raises(OpenDentalAPIError):
            await client.list_providers()


@pytest.mark.asyncio
async def test_find_patients_passes_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "dev")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "cust")
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.text = "[]"
    fake_response.json = lambda: []
    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)
    with patch("open_dental_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = OpenDentalClient()
        await client.find_patients(lname="Smith", phone="5551234")
        _, kwargs = fake_http.request.call_args
        params = kwargs["params"]
        assert params["LName"] == "Smith"
        assert params["Phone"] == "5551234"
        assert params["Limit"] == 25  # default


# ----- Server helpers -------------------------------------------------------


def test_format_error_auth() -> None:
    msg = _format_error(OpenDentalAuthError("nope", 401))
    assert "Authentication" in msg


def test_format_error_api() -> None:
    msg = _format_error(OpenDentalAPIError("kaboom", 500, "body"))
    assert "API error" in msg


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_json_serializes() -> None:
    import json
    out = _json({"a": 1, "b": [1, 2]})
    assert json.loads(out) == {"a": 1, "b": [1, 2]}