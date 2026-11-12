"""Read-only safety and documented API contract regressions; all traffic mocked."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from open_dental_mcp.client import DEFAULT_BASE_URL, OpenDentalClient
from open_dental_mcp.exceptions import (
    OpenDentalAPIError,
    OpenDentalAuthError,
    OpenDentalConnectionError,
    OpenDentalError,
    OpenDentalRateLimitError,
)

DEV_KEY = "test-developer-only"
CUSTOMER_KEY = "test-customer-only"
PRIVATE_VALUE = "PRIVATE-PATIENT-SENTINEL"


@pytest.fixture
async def client() -> AsyncIterator[OpenDentalClient]:
    async with OpenDentalClient(DEV_KEY, CUSTOMER_KEY, max_retries=0) as instance:
        yield instance


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def test_non_get_methods_never_reach_transport(client: OpenDentalClient, method: str) -> None:
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match="Read-only"):
            await client._request(method, "/patients")
        assert not router.calls


@pytest.mark.parametrize(
    "path",
    [
        "/queries",
        "/patients/1/../2",
        "//evil.example/patients",
        "https://evil.example/patients",
        "/patients?LName=Smith",
        "/patients/0",
        "/patients/-1",
        "/patients/1%2f..",
        "/patients/1\n",
        "/appointments/1/Confirm",
        "/recalls/List",
    ],
)
async def test_unapproved_paths_never_reach_transport(client: OpenDentalClient, path: str) -> None:
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match="Read-only"):
            await client._request("GET", path)
        assert not router.calls


async def test_get_body_and_unapproved_query_are_blocked(client: OpenDentalClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match="Read-only"):
            await client._request("GET", "/patients", json={"LName": "Changed"})
        with pytest.raises(ValueError, match="Unsupported"):
            await client._request("GET", "/appointments", params={"ProvNum": 1})
        assert not router.calls


@pytest.mark.parametrize(
    "url",
    [
        "http://api.opendental.com/api/v1",
        "https://evil.example/api/v1",
        "https://api.opendental.com.evil.example/api/v1",
        "https://api.opendental.com@evil.example/api/v1",
        "http://localhost:30222/api/v1",
    ],
)
def test_credentials_cannot_be_sent_to_alternate_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="official HTTPS"):
        OpenDentalClient(DEV_KEY, CUSTOMER_KEY, base_url=url)


@pytest.mark.parametrize("key", ["ab", "injected\r\nX-Header: value", "key/other", "key key"])
def test_invalid_keys_fail_without_echoing_input(key: str) -> None:
    with pytest.raises(OpenDentalAuthError) as caught:
        OpenDentalClient(key, CUSTOMER_KEY)
    assert key not in str(caught.value)


async def test_get_headers_and_patient_query_encoding(client: OpenDentalClient) -> None:
    with respx.mock as router:
        route = router.get(f"{DEFAULT_BASE_URL}/patients").respond(200, json=[])
        await client.find_patients(
            lname=" De Angelo & Sons ", birthdate="2008-02-29", limit=7, offset=14
        )
        request = route.calls.last.request
        assert request.headers["Authorization"] == f"ODFHIR {DEV_KEY}/{CUSTOMER_KEY}"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["Accept"] == "application/json"
        assert dict(request.url.params) == {
            "LName": "De Angelo & Sons",
            "Birthdate": "2008-02-29",
            "Limit": "7",
            "Offset": "14",
        }


async def test_appointment_dates_correct_case_inclusive_and_provider_local(
    client: OpenDentalClient,
) -> None:
    rows = [
        {"AptNum": 1, "AptDateTime": "2026-10-01 00:00:00", "ProvNum": 2, "ClinicNum": 0},
        {"AptNum": 2, "AptDateTime": "2026-10-31 23:59:59", "ProvNum": 2, "ClinicNum": 0},
        {"AptNum": 3, "AptDateTime": "2026-10-15 09:00:00", "ProvNum": 3, "ClinicNum": 0},
    ]
    with respx.mock as router:
        route = router.get(f"{DEFAULT_BASE_URL}/appointments").respond(200, json=rows)
        page = await client.find_appointments(
            date_start="2026-10-01",
            date_end="2026-10-31",
            provnum=2,
            clinicnum=0,
        )
        assert [row["AptNum"] for row in page] == [1, 2]
        assert dict(route.calls.last.request.url.params) == {
            "dateStart": "2026-10-01",
            "dateEnd": "2026-10-31",
            "ClinicNum": "0",
            "Limit": "50",
            "Offset": "0",
        }
        assert page.scanned_count == 3
        assert page.next_offset is None


async def test_single_day_uses_lowercase_date(client: OpenDentalClient) -> None:
    with respx.mock as router:
        route = router.get(f"{DEFAULT_BASE_URL}/appointments").respond(200, json=[])
        await client.find_appointments(date="2026-10-17")
        assert route.calls.last.request.url.params["date"] == "2026-10-17"
        assert "Date" not in route.calls.last.request.url.params


async def test_filtered_empty_full_page_keeps_raw_next_offset(client: OpenDentalClient) -> None:
    with respx.mock as router:
        route = router.get(f"{DEFAULT_BASE_URL}/procedurelogs").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=[
                        {"ProcNum": 1, "PatNum": 12, "ProcDate": "2025-01-01"},
                        {"ProcNum": 2, "PatNum": 12, "ProcDate": "2025-01-02"},
                    ],
                ),
                httpx.Response(
                    200,
                    json=[
                        {"ProcNum": 3, "PatNum": 12, "ProcDate": "2026-10-01"},
                    ],
                ),
            ]
        )
        first = await client.find_procedurelogs(
            patnum=12,
            date_start="2026-10-01",
            date_end="2026-10-17",
            limit=2,
            offset=4,
        )
        assert first == []
        assert first.scanned_count == 2
        assert first.next_offset == 6
        assert first.has_more is None
        second = await client.find_procedurelogs(
            patnum=12,
            date_start="2026-10-01",
            date_end="2026-10-17",
            limit=2,
            offset=first.next_offset,
        )
        assert [row["ProcNum"] for row in second] == [3]
        assert second.next_offset is None
        assert second.has_more is False
        assert dict(route.calls.last.request.url.params) == {
            "PatNum": "12",
            "Limit": "2",
            "Offset": "6",
        }


async def test_procedure_and_recall_ranges_are_inclusive_local_filters(
    client: OpenDentalClient,
) -> None:
    with respx.mock as router:
        procedures = router.get(f"{DEFAULT_BASE_URL}/procedurelogs").respond(
            200,
            json=[
                {"ProcNum": 1, "PatNum": 2, "ProcDate": "2026-10-01", "ProcStatus": "TP"},
                {"ProcNum": 2, "PatNum": 2, "ProcDate": "2026-10-17", "ProcStatus": "C"},
                {"ProcNum": 3, "PatNum": 2, "ProcDate": "2026-10-18", "ProcStatus": "C"},
            ],
        )
        recalls = router.get(f"{DEFAULT_BASE_URL}/recalls").respond(
            200,
            json=[
                {
                    "RecallNum": 1,
                    "PatNum": 2,
                    "DateDue": "2026-10-01",
                    "RecallTypeNum": 6,
                    "IsDisabled": "false",
                },
                {"RecallNum": 2, "PatNum": 2, "DateDue": "2026-10-17", "RecallTypeNum": 6},
                {"RecallNum": 3, "PatNum": 2, "DateDue": "2026-10-10", "RecallTypeNum": 7},
                {"RecallNum": 4, "PatNum": 2, "DateDue": "2026-09-30", "RecallTypeNum": 6},
            ],
        )
        page = await client.find_procedurelogs(
            patnum=2, date_start="2026-10-01", date_end="2026-10-17"
        )
        assert [row["ProcNum"] for row in page] == [1, 2]
        assert page[0]["ProcStatus"] == "TP"
        page = await client.find_recalls(
            patnum=2,
            due_date_start="2026-10-01",
            due_date_end="2026-10-17",
            recall_type=6,
        )
        assert [row["RecallNum"] for row in page] == [1, 2]
        assert page[0]["IsDisabled"] == "false"
        for route in (procedures, recalls):
            assert dict(route.calls.last.request.url.params) == {
                "PatNum": "2",
                "Limit": "50",
                "Offset": "0",
            }


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get_patient", {"patnum": True}),
        ("get_patient", {"patnum": -1}),
        ("get_appointment", {"aptnum": "1"}),
        ("find_patients", {}),
        ("find_patients", {"lname": " "}),
        ("find_patients", {"lname": "Smith\n"}),
        ("find_patients", {"birthdate": "2025-02-29"}),
        ("find_patients", {"lname": "Smith", "limit": 101}),
        ("find_patients", {"lname": "Smith", "offset": -1}),
        ("find_appointments", {}),
        ("find_appointments", {"provnum": 1}),
        (
            "find_appointments",
            {"date": "2026-10-17", "date_start": "2026-10-01", "date_end": "2026-10-31"},
        ),
        ("find_appointments", {"date_start": "2026-10-17"}),
        ("find_appointments", {"date_start": "2026-10-17", "date_end": "2026-10-16"}),
        ("find_appointments", {"date_start": "2024-01-01", "date_end": "2026-10-17"}),
        ("find_procedurelogs", {}),
        ("find_recalls", {}),
        ("find_recalls", {"patnum": 1, "recall_type": "Prophy"}),
        ("list_operatories", {"clinicnum": -1}),
    ],
)
async def test_invalid_arguments_block_before_network(
    client: OpenDentalClient, method: str, kwargs: dict[str, Any]
) -> None:
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match=r"must|require|Provide|Use"):
            await getattr(client, method)(**kwargs)
        assert not router.calls


@pytest.mark.parametrize("body", [{"error": "not an array"}, ["bad row"], None, [[], {}]])
async def test_invalid_list_response_fails_closed(client: OpenDentalClient, body: Any) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/providers").respond(
            200,
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(OpenDentalAPIError, match="unexpected list"):
            await client.list_providers()


async def test_server_ignoring_page_limit_rejected(client: OpenDentalClient) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/providers").respond(
            200, json=[{"ProvNum": 1}, {"ProvNum": 2}]
        )
        with pytest.raises(OpenDentalAPIError, match="page limit"):
            await client.list_providers(limit=1)


@pytest.mark.parametrize(
    "body", [{"PatNum": 2}, [{"PatNum": 1}], {}, {"PatNum": "1"}, {"PatNum": True}]
)
async def test_wrong_patient_record_or_identity_rejected(
    client: OpenDentalClient, body: Any
) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/patients/1").respond(200, json=body)
        with pytest.raises(OpenDentalAPIError, match="record or identifier"):
            await client.get_patient(1)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"PatNum": 2, "ClinicNum": 0, "AptDateTime": "2026-10-17 09:00:00"}, "patient filter"),
        ({"PatNum": 1, "ClinicNum": 2, "AptDateTime": "2026-10-17 09:00:00"}, "clinic filter"),
        ({"PatNum": 1, "ClinicNum": 0, "AptDateTime": "2026-10-18 09:00:00"}, "date filter"),
        ({"PatNum": 1, "ClinicNum": 0, "AptDateTime": "invalid"}, "invalid date"),
        ({"PatNum": 1, "ClinicNum": 0}, "date field"),
    ],
)
async def test_ignored_appointment_filters_and_malformed_dates_rejected(
    client: OpenDentalClient, row: dict[str, Any], message: str
) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/appointments").respond(200, json=[{"AptNum": 1, **row}])
        with pytest.raises(OpenDentalAPIError, match=message):
            await client.find_appointments(patnum=1, clinicnum=0, date="2026-10-17")


async def test_ignored_operatory_clinic_filter_rejected(client: OpenDentalClient) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/operatories").respond(
            200, json=[{"OperatoryNum": 1, "ClinicNum": 2}]
        )
        with pytest.raises(OpenDentalAPIError, match="clinic filter"):
            await client.list_operatories(clinicnum=0)


async def test_sensitive_identifiers_removed_recursively(client: OpenDentalClient) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/patients/1").respond(
            200,
            json={
                "PatNum": 1,
                "SSN": PRIVATE_VALUE,
                "TIN": PRIVATE_VALUE,
                "MedicaidID": PRIVATE_VALUE,
                "Note": "Retain clinical note",
                "Nested": [{"ssn": PRIVATE_VALUE, "Other": "Kept"}],
            },
        )
        result = await client.get_patient(1)
        assert PRIVATE_VALUE not in json.dumps(result)
        assert result == {
            "PatNum": 1,
            "Note": "Retain clinical note",
            "Nested": [{"Other": "Kept"}],
        }


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
async def test_error_body_and_headers_never_leak(
    client: OpenDentalClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    status: int,
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/patients").respond(
            status,
            text=f"{PRIVATE_VALUE} {DEV_KEY} {CUSTOMER_KEY}",
            headers={"x-request-id": PRIVATE_VALUE},
        )
        with pytest.raises(OpenDentalError) as caught:
            await client.find_patients(lname=PRIVATE_VALUE)
        captured = capsys.readouterr()
        diagnostic = (
            str(caught.value)
            + repr(caught.value)
            + repr(vars(caught.value))
            + caplog.text
            + captured.out
            + captured.err
        )
        for secret in (PRIVATE_VALUE, DEV_KEY, CUSTOMER_KEY):
            assert secret not in diagnostic


async def test_network_exception_sanitized(
    client: OpenDentalClient, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/patients").mock(
            side_effect=httpx.ConnectError(f"{PRIVATE_VALUE} {DEV_KEY}"),
        )
        with pytest.raises(OpenDentalConnectionError) as caught:
            await client.find_patients(lname=PRIVATE_VALUE)
        captured = capsys.readouterr()
        diagnostic = str(caught.value) + caplog.text + captured.out + captured.err
        assert PRIVATE_VALUE not in diagnostic
        assert DEV_KEY not in diagnostic
        assert caught.value.__suppress_context__


async def test_redirect_not_followed(client: OpenDentalClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        source = router.get(f"{DEFAULT_BASE_URL}/providers").respond(
            302, headers={"Location": "https://evil.example/collect"}
        )
        destination = router.get("https://evil.example/collect").respond(200, json=[])
        with pytest.raises(OpenDentalAPIError, match="302"):
            await client.list_providers()
        assert source.call_count == 1
        assert destination.call_count == 0


@pytest.mark.parametrize(
    ("content", "content_type", "message"),
    [
        ("<html>login</html>", "text/html", "application/json"),
        ("{broken", "application/json", "invalid JSON"),
        ("", "application/json", "invalid JSON"),
    ],
)
async def test_invalid_response_content_rejected(
    client: OpenDentalClient, content: str, content_type: str, message: str
) -> None:
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/providers").respond(
            200, text=content, headers={"Content-Type": content_type}
        )
        with pytest.raises(OpenDentalAPIError, match=message):
            await client.list_providers()


async def test_oversized_response_rejected(
    client: OpenDentalClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("open_dental_mcp.client.MAX_RESPONSE_BYTES", 64)
    with respx.mock as router:
        router.get(f"{DEFAULT_BASE_URL}/providers").respond(200, json=[{"Note": "X" * 100}])
        with pytest.raises(OpenDentalAPIError, match="size limit"):
            await client.list_providers()


async def test_retry_get_preserves_offset_and_honors_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("open_dental_mcp.client.asyncio.sleep", sleep)
    async with OpenDentalClient(DEV_KEY, CUSTOMER_KEY, max_retries=1) as instance:
        with respx.mock as router:
            route = router.get(f"{DEFAULT_BASE_URL}/providers").mock(
                side_effect=[
                    httpx.Response(429, headers={"Retry-After": "1.5"}),
                    httpx.Response(200, json=[]),
                ]
            )
            result = await instance.list_providers(limit=5, offset=10)
            assert result == []
            assert route.call_count == 2
            assert all(call.request.method == "GET" for call in route.calls)
            assert all(
                dict(call.request.url.params) == {"Limit": "5", "Offset": "10"}
                for call in route.calls
            )
            sleep.assert_awaited_once_with(1.5)


async def test_long_retry_after_returns_error_without_early_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("open_dental_mcp.client.asyncio.sleep", sleep)
    async with OpenDentalClient(DEV_KEY, CUSTOMER_KEY, max_retries=2) as instance:
        with respx.mock as router:
            route = router.get(f"{DEFAULT_BASE_URL}/providers").respond(
                429, headers={"Retry-After": "120"}
            )
            with pytest.raises(OpenDentalRateLimitError) as caught:
                await instance.list_providers()
            assert caught.value.retry_after == 120
            assert route.call_count == 1
            sleep.assert_not_awaited()


async def test_retry_budget_exhaustion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("open_dental_mcp.client.asyncio.sleep", sleep)
    async with OpenDentalClient(DEV_KEY, CUSTOMER_KEY, max_retries=2) as instance:
        with respx.mock as router:
            route = router.get(f"{DEFAULT_BASE_URL}/providers").respond(503)
            with pytest.raises(OpenDentalAPIError, match="503"):
                await instance.list_providers()
            assert route.call_count == 3
            assert sleep.await_count == 2


async def test_empty_clinics_is_valid_and_client_closes() -> None:
    async with OpenDentalClient(DEV_KEY, CUSTOMER_KEY, max_retries=0) as instance:
        with respx.mock as router:
            router.get(f"{DEFAULT_BASE_URL}/clinics").respond(200, json=[])
            result = await instance.list_clinics()
            assert result == []
            assert result.has_more is False
    assert instance._client.is_closed
