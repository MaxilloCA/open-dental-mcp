"""Async HTTP client for Open Dental.

Uses Open Dental's REST API. Authentication is two API keys (developer +
customer) joined with a slash in the ``Authorization: ODFHIR`` header.

Built on industry-leading patterns (encode/httpx, stripe-python):
- **Shared ``httpx.AsyncClient``** with connection pooling + transport retries.
- **Typed exception hierarchy** with structured fields. See ``exceptions.py``.
- **Application-level retry** with exponential backoff + full jitter.

Docs: https://www.opendental.com/site/apispecification.html
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import random
import time
from typing import Any

import httpx
import structlog

from . import __version__
from .exceptions import (
    OpenDentalAPIError,
    OpenDentalAuthError,
    OpenDentalConnectionError,
    OpenDentalError,
    OpenDentalNotFoundError,
    OpenDentalRateLimitError,
)

log = structlog.get_logger(__name__)


# --- Configuration constants -----------------------------------------------

DEFAULT_BASE_URL = "https://api.opendental.com/api/v1"
DEFAULT_TIMEOUT = 30.0

# Connection pool sizing
DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_KEEPALIVE_EXPIRY = 30.0

# Application-level retry
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_RETRY_DELAY = 0.5
DEFAULT_MAX_RETRY_DELAY = 30.0

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


# --- Internal helpers ------------------------------------------------------


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with full jitter, clamped to [0.5, 30] seconds."""
    if retry_after is not None:
        return min(float(retry_after), DEFAULT_MAX_RETRY_DELAY)
    delay = min(DEFAULT_BASE_RETRY_DELAY * (2 ** attempt), DEFAULT_MAX_RETRY_DELAY)
    return float(delay * random.uniform(0.5, 1.0))  # full jitter


class OpenDentalClient:
    """Async client for Open Dental's REST API.

    Open Dental uses two API keys in a single ``Authorization`` header:

        Authorization: ODFHIR {DeveloperKey}/{CustomerKey}

    Set ``OPENDENTAL_DEVELOPER_KEY`` and ``OPENDENTAL_CUSTOMER_KEY`` env vars,
    or pass them to the constructor.

    Use as an async context manager:

        async with OpenDentalClient() as client:
            await client.get_patient(12345)
    """

    def __init__(
        self,
        developer_key: str | None = None,
        customer_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        developer_key = developer_key or os.environ.get("OPENDENTAL_DEVELOPER_KEY")
        customer_key = customer_key or os.environ.get("OPENDENTAL_CUSTOMER_KEY")
        if not developer_key or not customer_key:
            raise OpenDentalAuthError(
                "Open Dental credentials missing. Set OPENDENTAL_DEVELOPER_KEY and "
                "OPENDENTAL_CUSTOMER_KEY environment variables, or pass them to the client."
            )
        assert developer_key is not None
        assert customer_key is not None
        self._auth_header = f"ODFHIR {developer_key}/{customer_key}"
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        # Build shared httpx.AsyncClient with pooling + transport retries.
        transport = httpx.AsyncHTTPTransport(retries=3)
        limits = httpx.Limits(
            max_connections=DEFAULT_MAX_CONNECTIONS,
            max_keepalive_connections=DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=DEFAULT_KEEPALIVE_EXPIRY,
        )
        timeout_obj = httpx.Timeout(
            timeout,
            connect=10.0,
            read=timeout,
            write=10.0,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_obj,
            limits=limits,
            transport=transport,
            headers={
                "Authorization": self._auth_header,
                "Accept": "application/json",
                "User-Agent": f"open-dental-mcp/{__version__}",
            },
            follow_redirects=False,
        )

    # --- Context manager ------------------------------------------------------

    async def __aenter__(self) -> OpenDentalClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Request execution ----------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("x-amzn-requestid")
            or response.headers.get("request-id")
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text

        if response.status_code == 401:
            raise OpenDentalAuthError(
                "Open Dental rejected the credentials (HTTP 401).",
                http_status=401,
                request_id=request_id,
                body=body,
            )
        if response.status_code == 403:
            raise OpenDentalAuthError(
                "Open Dental denied access (HTTP 403). "
                "Check your key permissions in the Developer Portal.",
                http_status=403,
                request_id=request_id,
                body=body,
            )
        if response.status_code == 404:
            raise OpenDentalNotFoundError(
                f"Open Dental resource not found: {response.url}",
                http_status=404,
                request_id=request_id,
                body=body,
            )
        if response.status_code == 429:
            retry_after: float | None = None
            with contextlib.suppress(ValueError):
                ra_header = response.headers.get("retry-after")
                if ra_header:
                    retry_after = float(ra_header)
            raise OpenDentalRateLimitError(
                "Open Dental rate limit hit (HTTP 429). Slow down.",
                retry_after=retry_after,
                request_id=request_id,
                body=body,
            )
        if 500 <= response.status_code < 600:
            raise OpenDentalAPIError(
                f"Open Dental server error (HTTP {response.status_code})",
                http_status=response.status_code,
                request_id=request_id,
                body=body,
            )
        raise OpenDentalAPIError(
            f"Open Dental returned HTTP {response.status_code}",
            http_status=response.status_code,
            request_id=request_id,
            body=body,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        last_exc: OpenDentalError | None = None
        for attempt in range(self._max_retries + 1):
            log.info("request.start", method=method, path=path, attempt=attempt)
            t0 = time.monotonic()
            try:
                response = await self._client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                log.warning(
                    "request.connection_error",
                    method=method, path=path, error=str(exc),
                    duration_ms=round(duration_ms, 1),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise OpenDentalConnectionError(
                    f"Network failure calling Open Dental {method} {path}: {exc}",
                ) from exc

            duration_ms = (time.monotonic() - t0) * 1000
            log.info(
                "request.end",
                method=method, path=path, status=response.status_code,
                duration_ms=round(duration_ms, 1),
            )

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                retry_after: float | None = None
                with contextlib.suppress(ValueError):
                    ra_header = response.headers.get("retry-after")
                    if ra_header:
                        retry_after = float(ra_header)
                delay = _retry_delay(attempt, retry_after)
                log.warning(
                    "request.retry",
                    method=method, path=path, status=response.status_code,
                    attempt=attempt, delay=round(delay, 2),
                )
                await asyncio.sleep(delay)
                continue

            if 200 <= response.status_code < 300:
                text = response.text
                if not text:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return text

            try:
                self._raise_for_status(response)
            except OpenDentalRateLimitError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = _retry_delay(attempt, exc.retry_after)
                    await asyncio.sleep(delay)
                    continue
                raise
            except (OpenDentalAPIError, OpenDentalAuthError, OpenDentalNotFoundError):
                raise
            except OpenDentalError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    # ----- Patient workflows -------------------------------------------------

    async def get_patient(self, patnum: int) -> Any:
        """Fetch a single patient by PatNum."""
        return await self._request("GET", f"/patients/{patnum}")

    async def find_patients(
        self,
        *,
        lname: str | None = None,
        fname: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        birthdate: str | None = None,
        limit: int = 25,
    ) -> Any:
        """Search for patients. At least one criterion should be provided."""
        params: dict[str, Any] = {"Limit": limit}
        if lname:
            params["LName"] = lname
        if fname:
            params["FName"] = fname
        if phone:
            params["Phone"] = phone
        if email:
            params["Email"] = email
        if birthdate:
            params["Birthdate"] = birthdate
        return await self._request("GET", "/patients", params=params)

    async def update_patient(self, patnum: int, updates: dict[str, Any]) -> Any:
        """Update fields on an existing patient record."""
        return await self._request("PUT", f"/patients/{patnum}", json=updates)

    # ----- Appointment workflows ---------------------------------------------

    async def get_appointment(self, aptnum: int) -> Any:
        """Fetch a single appointment."""
        return await self._request("GET", f"/appointments/{aptnum}")

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
    ) -> Any:
        """List appointments with optional filters."""
        params: dict[str, Any] = {"Limit": limit}
        if date:
            params["Date"] = date
        if date_start:
            params["DateStart"] = date_start
        if date_end:
            params["DateEnd"] = date_end
        if patnum is not None:
            params["PatNum"] = patnum
        if provnum is not None:
            params["ProvNum"] = provnum
        if clinicnum is not None:
            params["ClinicNum"] = clinicnum
        return await self._request("GET", "/appointments", params=params)

    async def create_appointment(self, appointment: dict[str, Any]) -> Any:
        """Create a new appointment."""
        return await self._request("POST", "/appointments", json=appointment)

    # ----- Providers --------------------------------------------------------

    async def list_providers(self) -> Any:
        """List all providers."""
        return await self._request("GET", "/providers")

    # ----- Procedures --------------------------------------------------------

    async def find_procedurelogs(
        self,
        *,
        patnum: int | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        limit: int = 50,
    ) -> Any:
        """List procedure log entries."""
        params: dict[str, Any] = {"Limit": limit}
        if patnum is not None:
            params["PatNum"] = patnum
        if date_start:
            params["DateStart"] = date_start
        if date_end:
            params["DateEnd"] = date_end
        return await self._request("GET", "/procedurelogs", params=params)

    # ----- Commlogs (call / message log) ------------------------------------

    async def create_commlog(
        self,
        patnum: int,
        comm_date: str,
        comm_type: int,
        note: str,
        *,
        comm_mode: int | None = None,
        sent_or_received: int | None = None,
    ) -> Any:
        """Log a communication (phone call, email, etc.) to a patient record."""
        payload: dict[str, Any] = {
            "PatNum": patnum,
            "CommDateTime": comm_date,
            "CommType": comm_type,
            "Note": note,
        }
        if comm_mode is not None:
            payload["CommMode"] = comm_mode
        if sent_or_received is not None:
            payload["SentOrReceived"] = sent_or_received
        return await self._request("POST", "/commlogs", json=payload)

    # ----- Recalls -----------------------------------------------------------

    async def find_recalls(
        self,
        *,
        patnum: int | None = None,
        due_date_start: str | None = None,
        due_date_end: str | None = None,
        recall_type: int | None = None,
        limit: int = 50,
    ) -> Any:
        """List recalls due for patients — useful for hygiene follow-up workflows."""
        params: dict[str, Any] = {"Limit": limit}
        if patnum is not None:
            params["PatNum"] = patnum
        if due_date_start:
            params["DueDateStart"] = due_date_start
        if due_date_end:
            params["DueDateEnd"] = due_date_end
        if recall_type is not None:
            params["RecallType"] = recall_type
        return await self._request("GET", "/recalls", params=params)

    # ----- Clinics / Operatories --------------------------------------------

    async def list_clinics(self) -> Any:
        """List clinics (offices) under this developer account."""
        return await self._request("GET", "/clinics")

    async def list_operatories(self, clinicnum: int | None = None) -> Any:
        """List operatories (treatment rooms), optionally filtered by clinic."""
        params: dict[str, Any] = {}
        if clinicnum is not None:
            params["ClinicNum"] = clinicnum
        return await self._request("GET", "/operatories", params=params)

    # ----- Definitions / reference data -------------------------------------

    async def list_appointment_types(self) -> Any:
        """List appointment type definitions (cleaning, filling, etc.)."""
        return await self._request("GET", "/appointmenttypes")
