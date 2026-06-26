"""Async HTTP client for Open Dental.

Uses Open Dental's REST API. Authentication is two API keys (developer +
customer) joined with a slash in the ``Authorization: ODFHIR`` header.

Docs: https://www.opendental.com/site/apispecification.html
"""
from __future__ import annotations

import os
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://api.opendental.com/api/v1"
DEFAULT_TIMEOUT = 30.0


class OpenDentalError(RuntimeError):
    """Base exception for Open Dental client errors."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OpenDentalAuthError(OpenDentalError):
    """Raised when credentials are missing, invalid, or unauthorized."""


class OpenDentalAPIError(OpenDentalError):
    """Raised on non-2xx API responses other than auth failures."""


class OpenDentalClient:
    """Async client for Open Dental's REST API.

    Open Dental uses two API keys in a single ``Authorization`` header:

        Authorization: ODFHIR {DeveloperKey}/{CustomerKey}

    Set ``OPENDENTAL_DEVELOPER_KEY`` and ``OPENDENTAL_CUSTOMER_KEY`` env vars,
    or pass them to the constructor.
    """

    def __init__(
        self,
        developer_key: str | None = None,
        customer_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        developer_key = developer_key or os.environ.get("OPENDENTAL_DEVELOPER_KEY")
        customer_key = customer_key or os.environ.get("OPENDENTAL_CUSTOMER_KEY")
        if not developer_key or not customer_key:
            raise OpenDentalAuthError(
                "Open Dental credentials missing. Set OPENDENTAL_DEVELOPER_KEY and "
                "OPENDENTAL_CUSTOMER_KEY environment variables, or pass them to the client."
            )
        self._auth_header = f"ODFHIR {developer_key}/{customer_key}"
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header,
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.request(
                method, url, params=params, json=json, headers=self._headers()
            )

        if response.status_code == 401:
            raise OpenDentalAuthError(
                "Open Dental rejected the credentials (HTTP 401).", 401
            )
        if response.status_code == 403:
            raise OpenDentalAuthError(
                "Open Dental denied access (HTTP 403). Check your key permissions in the Developer Portal.",
                403,
            )
        if not 200 <= response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise OpenDentalAPIError(
                f"Open Dental returned HTTP {response.status_code}",
                status_code=response.status_code,
                body=body,
            )

        text = response.text
        if not text:
            return None
        try:
            return response.json()
        except ValueError:
            return text

    # ----- Patient workflows -------------------------------------------------

    async def get_patient(self, patnum: int) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        """Search for patients. At least one criterion should be provided.

        Query params: LName, FName, Phone, Email, Birthdate.
        """
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

    async def update_patient(self, patnum: int, updates: dict[str, Any]) -> dict[str, Any]:
        """Update fields on an existing patient record."""
        return await self._request("PUT", f"/patients/{patnum}", json=updates)

    # ----- Appointment workflows ---------------------------------------------

    async def get_appointment(self, aptnum: int) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        """List appointments with optional filters.

        Pass either ``date`` (single day, YYYY-MM-DD) or ``date_start`` +
        ``date_end`` (range).
        """
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

    async def create_appointment(self, appointment: dict[str, Any]) -> dict[str, Any]:
        """Create a new appointment."""
        return await self._request("POST", "/appointments", json=appointment)

    # ----- Providers --------------------------------------------------------

    async def list_providers(self) -> list[dict[str, Any]]:
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
    ) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any]:
        """Log a communication (phone call, email, etc.) to a patient record.

        Open Dental uses integer codes for comm types and modes. See:
        https://www.opendental.com/site/apicommlogs.html for the full table.
        """
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
    ) -> list[dict[str, Any]]:
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

    async def list_clinics(self) -> list[dict[str, Any]]:
        """List clinics (offices) under this developer account."""
        return await self._request("GET", "/clinics")

    async def list_operatories(self, clinicnum: int | None = None) -> list[dict[str, Any]]:
        """List operatories (treatment rooms), optionally filtered by clinic."""
        params: dict[str, Any] = {}
        if clinicnum is not None:
            params["ClinicNum"] = clinicnum
        return await self._request("GET", "/operatories", params=params)

    # ----- Definitions / reference data -------------------------------------

    async def list_appointment_types(self) -> list[dict[str, Any]]:
        """List appointment type definitions (cleaning, filling, etc.)."""
        return await self._request("GET", "/appointmenttypes")