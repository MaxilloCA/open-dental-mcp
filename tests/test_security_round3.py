"""Third-pass adversarial regressions for response and credential validation."""

from __future__ import annotations

import io
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import respx

from open_dental_mcp.client import DEFAULT_BASE_URL, OpenDentalClient
from open_dental_mcp.config import load_project_env
from open_dental_mcp.exceptions import OpenDentalAPIError


@pytest.fixture
async def secure_client() -> AsyncIterator[OpenDentalClient]:
    async with OpenDentalClient("dev-test", "customer-test", max_retries=0) as client:
        yield client


@respx.mock
async def test_deep_json_is_reported_as_api_error(secure_client: OpenDentalClient) -> None:
    respx.get(DEFAULT_BASE_URL + "/providers").respond(
        content="[" * 1500 + "0" + "]" * 1500,
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(OpenDentalAPIError):
        await secure_client.list_providers()


@pytest.mark.parametrize("identifier", [True, 1.0, "1", None, 0, -1])
@respx.mock
async def test_patient_search_rejects_invalid_record_ids(
    secure_client: OpenDentalClient,
    identifier: Any,
) -> None:
    respx.get(DEFAULT_BASE_URL + "/patients").respond(json=[{"PatNum": identifier}])
    with pytest.raises(OpenDentalAPIError):
        await secure_client.find_patients(lname="Example")


@pytest.mark.parametrize("body", [[{}], [{"unexpected": "success"}]])
@respx.mock
async def test_provider_page_requires_provider_identifier(
    secure_client: OpenDentalClient,
    body: list[dict[str, Any]],
) -> None:
    respx.get(DEFAULT_BASE_URL + "/providers").respond(json=body)
    with pytest.raises(OpenDentalAPIError):
        await secure_client.list_providers()


@pytest.mark.parametrize("identifier", [True, 1.0])
@respx.mock
async def test_patient_filter_rejects_boolean_and_float_ids(
    secure_client: OpenDentalClient,
    identifier: Any,
) -> None:
    respx.get(DEFAULT_BASE_URL + "/procedurelogs").respond(
        json=[{"ProcNum": 5, "PatNum": identifier}]
    )
    with pytest.raises(OpenDentalAPIError):
        await secure_client.find_procedurelogs(patnum=1)


@respx.mock
async def test_conflicting_duplicate_identity_keys_rejected(
    secure_client: OpenDentalClient,
) -> None:
    respx.get(DEFAULT_BASE_URL + "/patients/1").respond(
        content='{"PatNum":2,"PatNum":1}',
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(OpenDentalAPIError):
        await secure_client.get_patient(1)


@pytest.mark.parametrize("customer", ["bad key", "cust\x00PRIVATE", "bad/key", "ab"])
def test_invalid_credentials_rejected_before_any_environment_change(
    monkeypatch: pytest.MonkeyPatch,
    customer: str,
) -> None:
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "original-dev")
    monkeypatch.setenv("OPENDENTAL_CUSTOMER_KEY", "original-customer")
    content = f"OPENDENTAL_DEVELOPER_KEY=changed-dev\nOPENDENTAL_CUSTOMER_KEY='{customer}'\n"

    def open_fake(_path: Path, *args: Any, **kwargs: Any) -> io.StringIO:
        return io.StringIO(content)

    monkeypatch.setattr(Path, "open", open_fake)
    with pytest.raises(ValueError, match=r".") as caught:
        load_project_env("test-only-credentials.env")
    assert "PRIVATE" not in str(caught.value)
    assert customer not in str(caught.value)
    assert os.environ["OPENDENTAL_DEVELOPER_KEY"] == "original-dev"
    assert os.environ["OPENDENTAL_CUSTOMER_KEY"] == "original-customer"
