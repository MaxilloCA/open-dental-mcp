"""Second-pass adversarial checks: credential boundaries and API response integrity."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from open_dental_mcp.client import DEFAULT_BASE_URL, OpenDentalClient
from open_dental_mcp.exceptions import OpenDentalAPIError, OpenDentalConnectionError


@pytest.fixture
async def secure_client() -> AsyncIterator[OpenDentalClient]:
    async with OpenDentalClient("dev-test", "customer-test", max_retries=0) as client:
        yield client


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.opendental.com/api/v1",
        "https://api.opendental.com.evil.invalid/api/v1",
        "https://evil.invalid/api/v1",
        "https://user:secret@api.opendental.com/api/v1",
        DEFAULT_BASE_URL + "?redirect=evil",
        "http://192.168.1.1:30223/api/v1",
        "http://127.0.0.1:30224/api/v1",
        "http://127.0.0.1:30223@evil.invalid/api/v1",
        "http://127.0.0.1:30223/api/v1/../patients",
        "http://127.0.0.1:30223/api/v1/#fragment",
        "http://127.0.0.1:30223/api/v1//",
    ],
)
def test_untrusted_base_url_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match="official HTTPS Remote API or IPv4 loopback"):
        OpenDentalClient("dev-test", "customer-test", base_url=base_url)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.invalid/patients",
        "//evil.invalid/patients",
        "/patients/../commlogs",
        "/patients/%2e%2e/commlogs",
        "/patients?method=DELETE",
        "/patients/1#fragment",
        "/patients/1\n",
        "/appointments/1/",
        "/database",
        "/queries",
    ],
)
@respx.mock
async def test_path_escape_blocked_before_transport(
    secure_client: OpenDentalClient, path: str
) -> None:
    with pytest.raises(ValueError, match="Read-only policy"):
        await secure_client._request("GET", path)
    assert not respx.calls


@respx.mock
async def test_redirect_never_sends_credentials_to_second_host(
    secure_client: OpenDentalClient,
) -> None:
    respx.get(DEFAULT_BASE_URL + "/providers").respond(
        302, headers={"Location": "https://evil.invalid/providers"}
    )
    with pytest.raises(OpenDentalAPIError):
        await secure_client.list_providers()
    assert len(respx.calls) == 1
    assert respx.calls[0].request.url.host == "api.opendental.com"


@respx.mock
async def test_transport_exception_details_never_escape(
    secure_client: OpenDentalClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private = "PRIVATE_PATIENT dev-test customer-test private@example.invalid"
    respx.get(DEFAULT_BASE_URL + "/patients").mock(side_effect=httpx.ConnectError(private))
    with pytest.raises(OpenDentalConnectionError) as caught:
        await secure_client.find_patients(email="private@example.invalid")
    captured = capsys.readouterr()
    assert private not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert captured.out == ""
    assert "private@example.invalid" not in captured.err


@pytest.mark.parametrize("response_id", [True, 1.0])
@respx.mock
async def test_record_identifier_requires_integer_type(
    secure_client: OpenDentalClient,
    response_id: Any,
) -> None:
    respx.get(DEFAULT_BASE_URL + "/patients/1").respond(json={"PatNum": response_id})
    with pytest.raises(OpenDentalAPIError):
        await secure_client.get_patient(1)


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity"])
@respx.mock
async def test_nonstandard_json_numbers_rejected(
    secure_client: OpenDentalClient,
    nonfinite: str,
) -> None:
    respx.get(DEFAULT_BASE_URL + "/providers").respond(
        content='[{"ProvNum":1,"Amount":' + nonfinite + "}]",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(OpenDentalAPIError):
        await secure_client.list_providers()


@respx.mock
async def test_sensitive_identifiers_removed_at_all_depths(secure_client: OpenDentalClient) -> None:
    respx.get(DEFAULT_BASE_URL + "/patients/1").respond(
        json={
            "PatNum": 1,
            "SSN": "PRIVATE_SSN",
            "nested": [{"TIN": "PRIVATE_TIN"}],
            "MedicaidID": "PRIVATE_MEDICAID",
        }
    )
    result = await secure_client.get_patient(1)
    assert "PRIVATE" not in json.dumps(result)
    assert result["PatNum"] == 1
