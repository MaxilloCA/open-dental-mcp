"""Bounded, read-only client for the Open Dental REST API.

No write switch exists. Only explicitly listed GET resources are allowed.
List offsets refer to raw API rows, including when a page is filtered locally.
"""

from __future__ import annotations

import asyncio
import json as json_module
import math
import os
import random
import re
from collections.abc import Callable
from datetime import date as calendar_date
from typing import Any

import httpx

from . import __version__
from .exceptions import (
    OpenDentalAPIError,
    OpenDentalAuthError,
    OpenDentalConnectionError,
    OpenDentalNotFoundError,
    OpenDentalRateLimitError,
)

DEFAULT_BASE_URL = "https://api.opendental.com/api/v1"
BASE_URL_ENV = "OPEN_DENTAL_BASE_URL"
ALLOWED_BASE_URLS = frozenset(
    {
        DEFAULT_BASE_URL,
        "http://127.0.0.1:30222/api/v1",
        "http://127.0.0.1:30223/api/v1",
    }
)
MAX_RESPONSE_BYTES = 2_000_000
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
READ_PATH = re.compile(
    r"/(?:patients(?:/[1-9][0-9]*)?|appointments(?:/[1-9][0-9]*)?|"
    r"providers|clinics|operatories|appointmenttypes|procedurelogs|recalls|commlogs)\Z"
)
PARAMETERS = {
    "patients": {"LName", "FName", "Phone", "Email", "Birthdate"},
    "appointments": {"date", "dateStart", "dateEnd", "PatNum", "ClinicNum"},
    "procedurelogs": {"PatNum"},
    "commlogs": {"PatNum"},
    "recalls": {"PatNum"},
    "operatories": {"ClinicNum"},
    "providers": set(),
    "clinics": set(),
    "appointmenttypes": set(),
}
PRIMARY_IDS = {
    "patients": "PatNum",
    "appointments": "AptNum",
    "providers": "ProvNum",
    "clinics": "ClinicNum",
    "operatories": "OperatoryNum",
    "appointmenttypes": "AppointmentTypeNum",
    "procedurelogs": "ProcNum",
    "commlogs": "CommlogNum",
    "recalls": "RecallNum",
}


class RecordPage(list[dict[str, Any]]):
    """Matches with pagination based on the unfiltered source page."""

    def __init__(
        self, rows: list[dict[str, Any]], *, offset: int, limit: int, scanned_count: int
    ) -> None:
        super().__init__(rows)
        self.offset = offset
        self.limit = limit
        self.scanned_count = scanned_count
        self.next_offset = offset + scanned_count if scanned_count == limit else None
        self.has_more: bool | None = None if self.next_offset is not None else False


def _integer(value: Any, name: str, minimum: int = 1, maximum: int = 2_147_483_647) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value


def _date(value: str, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} must use YYYY-MM-DD.")
    try:
        calendar_date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a valid calendar date.") from None
    return value


def _range(start: str | None, end: str | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("Provide both start and end dates.")
    if start is not None and end is not None:
        _date(start, "start")
        _date(end, "end")
        days = (calendar_date.fromisoformat(end) - calendar_date.fromisoformat(start)).days
        if not 0 <= days <= 366:
            raise ValueError("Date range must be ordered and span at most 366 days.")


def _private_fields_removed(value: Any, depth: int = 0) -> Any:
    """Exclude identifiers unnecessary for supported office read workflows."""
    if depth > 30:
        raise OpenDentalAPIError("API response exceeds the supported nesting depth.")
    if isinstance(value, dict):
        return {
            k: _private_fields_removed(v, depth + 1)
            for k, v in value.items()
            if k.casefold() not in {"ssn", "tin", "medicaidid"}
        }
    if isinstance(value, list):
        return [_private_fields_removed(v, depth + 1) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise OpenDentalAPIError("API returned a non-finite JSON number.")
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-standard JSON constant.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _field_integer(row: dict[str, Any], key: str, minimum: int = 1) -> int:
    value = row.get(key)
    if type(value) is not int or not minimum <= value <= 2_147_483_647:
        raise OpenDentalAPIError("API returned a missing or invalid identifier field.")
    return value


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def validate_base_url(value: str) -> str:
    base_url = value.removesuffix("/")
    if base_url not in ALLOWED_BASE_URLS:
        raise ValueError(
            "Use the official HTTPS Remote API or IPv4 loopback on port 30222 or 30223."
        )
    return base_url


class OpenDentalClient:
    """Use as an async context manager. Credentials never enter diagnostics."""

    def __init__(
        self,
        developer_key: str | None = None,
        customer_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        base_url = validate_base_url(
            os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL) if base_url is None else base_url
        )
        _integer(max_retries, "max_retries", 0, 3)
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("timeout must be greater than zero and at most 60 seconds.")
        developer_key = developer_key or os.environ.get("OPENDENTAL_DEVELOPER_KEY")
        customer_key = customer_key or os.environ.get("OPENDENTAL_CUSTOMER_KEY")
        for key in (developer_key, customer_key):
            if not key or not re.fullmatch(r"[A-Za-z0-9._~+\-=]{3,512}", key):
                raise OpenDentalAuthError("Open Dental API keys are missing or invalid.")
        self._auth_header = f"ODFHIR {developer_key}/{customer_key}"
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Authorization": self._auth_header,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": f"open-dental-mcp/{__version__}",
            },
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )

    async def __aenter__(self) -> OpenDentalClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Any:
        if method != "GET" or not READ_PATH.fullmatch(path) or json is not None:
            raise ValueError("Read-only policy: request blocked.")
        allowed = PARAMETERS[path.split("/")[1]] | {"Limit", "Offset"}
        if params and (set(params) - allowed):
            raise ValueError("Unsupported API parameters blocked.")
        for attempt in range(self._max_retries + 1):
            try:
                async with self._client.stream("GET", path, params=params) as response:
                    status = response.status_code
                    delay = _retry_after(response)
                    if (
                        status in RETRYABLE_STATUS_CODES
                        and attempt < self._max_retries
                        and (delay is None or delay <= 30)
                    ):
                        await asyncio.sleep(
                            delay if delay is not None else random.uniform(0.25, 0.5) * 2**attempt
                        )
                        continue
                    if status == 401:
                        raise OpenDentalAuthError(
                            "Open Dental rejected the credentials (HTTP 401).", http_status=status
                        )
                    if status == 403:
                        raise OpenDentalAuthError(
                            "Open Dental denied access. "
                            "Check Developer Portal permissions (HTTP 403).",
                            http_status=status,
                        )
                    if status == 404:
                        raise OpenDentalNotFoundError("Open Dental resource not found (HTTP 404).")
                    if status == 429:
                        raise OpenDentalRateLimitError(
                            "Open Dental rate limit reached.", retry_after=delay
                        )
                    if status != 200:
                        raise OpenDentalAPIError(
                            f"Open Dental returned HTTP {status}.", http_status=status
                        )
                    if (
                        response.headers.get("content-type", "").split(";")[0].strip()
                        != "application/json"
                    ):
                        raise OpenDentalAPIError("Expected an application/json API response.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise OpenDentalAPIError("API response exceeded the safe size limit.")
                    try:
                        result = json_module.loads(
                            body, parse_constant=_reject_constant, object_pairs_hook=_unique_object
                        )
                        return _private_fields_removed(result)
                    except (ValueError, UnicodeError, RecursionError):
                        raise OpenDentalAPIError("API returned invalid JSON.") from None
            except httpx.HTTPError:
                if attempt < self._max_retries:
                    await asyncio.sleep(random.uniform(0.25, 0.5) * 2**attempt)
                    continue
                raise OpenDentalConnectionError("Network failure contacting Open Dental.") from None
        raise OpenDentalConnectionError("Open Dental request did not complete.")

    async def _record(self, path: str, id_field: str, expected_id: int) -> dict[str, Any]:
        row = await self._request("GET", path)
        if (
            not isinstance(row, dict)
            or type(row.get(id_field)) is not int
            or row[id_field] != expected_id
        ):
            raise OpenDentalAPIError("API returned an unexpected record or identifier.")
        return row

    async def _page(
        self,
        path: str,
        params: dict[str, Any],
        limit: int,
        offset: int,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> RecordPage:
        _integer(limit, "limit", 1, 100)
        _integer(offset, "offset", 0, 10_000_000)
        rows = await self._request("GET", path, params={**params, "Limit": limit, "Offset": offset})
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise OpenDentalAPIError("API returned an unexpected list response.")
        if len(rows) > limit:
            raise OpenDentalAPIError("API did not respect the requested page limit.")
        identity = PRIMARY_IDS[path.split("/")[1]]
        identities = [_field_integer(row, identity) for row in rows]
        if len(set(identities)) != len(identities):
            raise OpenDentalAPIError("API returned duplicate records in one page.")
        matched = rows if predicate is None else [row for row in rows if predicate(row)]
        return RecordPage(matched, offset=offset, limit=limit, scanned_count=len(rows))

    async def get_patient(self, patnum: int) -> dict[str, Any]:
        _integer(patnum, "patnum")
        return await self._record(f"/patients/{patnum}", "PatNum", patnum)

    async def get_appointment(self, aptnum: int) -> dict[str, Any]:
        _integer(aptnum, "aptnum")
        return await self._record(f"/appointments/{aptnum}", "AptNum", aptnum)

    async def find_patients(
        self,
        *,
        lname: str | None = None,
        fname: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        birthdate: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> RecordPage:
        params: dict[str, Any] = {}
        for key, value in {
            "LName": lname,
            "FName": fname,
            "Phone": phone,
            "Email": email,
            "Birthdate": birthdate,
        }.items():
            if value is not None:
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > 200
                    or any(ord(c) < 32 for c in value)
                ):
                    raise ValueError(
                        "Patient search fields must be nonempty text of at most 200 characters."
                    )
                params[key] = value.strip()
        if not params:
            raise ValueError("At least one patient search criterion is required.")
        if birthdate is not None:
            _date(birthdate, "birthdate")
        return await self._page("/patients", params, limit, offset)

    async def find_appointments(
        self,
        *,
        date: str | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        patnum: int | None = None,
        provnum: int | None = None,
        clinicnum: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RecordPage:
        if date is not None and (date_start is not None or date_end is not None):
            raise ValueError("Use a single date or a date range, not both.")
        _range(date_start, date_end)
        params: dict[str, Any] = {}
        if date is not None:
            params["date"] = _date(date, "date")
        if date_start is not None:
            params.update(dateStart=date_start, dateEnd=date_end)
        if patnum is not None:
            params["PatNum"] = _integer(patnum, "patnum")
        if clinicnum is not None:
            params["ClinicNum"] = _integer(clinicnum, "clinicnum", 0)
        if date is None and date_start is None and patnum is None:
            raise ValueError("Appointments require a patient or date filter.")
        if provnum is not None:
            _integer(provnum, "provnum")

        def matches(row: dict[str, Any]) -> bool:
            if patnum is not None and _field_integer(row, "PatNum") != patnum:
                raise OpenDentalAPIError("API did not respect the patient filter.")
            if clinicnum is not None and _field_integer(row, "ClinicNum", 0) != clinicnum:
                raise OpenDentalAPIError("API did not respect the clinic filter.")
            start, end = date or date_start, date or date_end
            if (
                start is not None
                and end is not None
                and not start <= _row_date(row, "AptDateTime") <= end
            ):
                raise OpenDentalAPIError("API did not respect the appointment date filter.")
            return provnum is None or _field_integer(row, "ProvNum", 0) == provnum

        return await self._page("/appointments", params, limit, offset, matches)

    async def find_procedurelogs(
        self,
        *,
        patnum: int | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RecordPage:
        _integer(patnum, "patnum")
        _range(date_start, date_end)

        def matches(row: dict[str, Any]) -> bool:
            if _field_integer(row, "PatNum") != patnum:
                raise OpenDentalAPIError("API did not respect the patient filter.")
            return (
                date_start is None
                or date_end is None
                or date_start <= _row_date(row, "ProcDate") <= date_end
            )

        return await self._page("/procedurelogs", {"PatNum": patnum}, limit, offset, matches)

    async def find_commlogs(
        self,
        *,
        patnum: int,
        limit: int = 50,
        offset: int = 0,
    ) -> RecordPage:
        """Read one patient's communication records, preserving notes as untrusted data."""
        _integer(patnum, "patnum")

        def matches(row: dict[str, Any]) -> bool:
            if _field_integer(row, "PatNum") != patnum:
                raise OpenDentalAPIError("API did not respect the patient filter.")
            if not isinstance(row.get("Note"), str) or not isinstance(row.get("CommDateTime"), str):
                raise OpenDentalAPIError("API returned missing or invalid communication fields.")
            return True

        return await self._page("/commlogs", {"PatNum": patnum}, limit, offset, matches)

    async def find_recalls(
        self,
        *,
        patnum: int | None = None,
        due_date_start: str | None = None,
        due_date_end: str | None = None,
        recall_type: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RecordPage:
        _range(due_date_start, due_date_end)
        params: dict[str, Any] = {}
        if patnum is not None:
            params["PatNum"] = _integer(patnum, "patnum")
        if patnum is None and due_date_start is None:
            raise ValueError("Recalls require a patient or due-date range.")
        if recall_type is not None:
            _integer(recall_type, "recall_type")

        def matches(row: dict[str, Any]) -> bool:
            if patnum is not None and _field_integer(row, "PatNum") != patnum:
                raise OpenDentalAPIError("API did not respect the patient filter.")
            in_range = (
                due_date_start is None
                or due_date_end is None
                or due_date_start <= _row_date(row, "DateDue") <= due_date_end
            )
            return in_range and (
                recall_type is None or _field_integer(row, "RecallTypeNum") == recall_type
            )

        return await self._page("/recalls", params, limit, offset, matches)

    async def list_providers(self, *, limit: int = 100, offset: int = 0) -> RecordPage:
        return await self._page("/providers", {}, limit, offset)

    async def list_clinics(self, *, limit: int = 100, offset: int = 0) -> RecordPage:
        return await self._page("/clinics", {}, limit, offset)

    async def list_operatories(
        self, clinicnum: int | None = None, *, limit: int = 100, offset: int = 0
    ) -> RecordPage:
        params = {} if clinicnum is None else {"ClinicNum": _integer(clinicnum, "clinicnum", 0)}

        def matches(row: dict[str, Any]) -> bool:
            if clinicnum is not None and _field_integer(row, "ClinicNum", 0) != clinicnum:
                raise OpenDentalAPIError("API did not respect the clinic filter.")
            return True

        return await self._page("/operatories", params, limit, offset, matches)

    async def list_appointment_types(self, *, limit: int = 100, offset: int = 0) -> RecordPage:
        return await self._page("/appointmenttypes", {}, limit, offset)


def _row_date(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise OpenDentalAPIError("Required date field is missing from API response.")
    try:
        return _date(value[:10], "API date")
    except ValueError:
        raise OpenDentalAPIError("API returned an invalid date field.") from None
