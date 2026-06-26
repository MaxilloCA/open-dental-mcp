"""Open Dental MCP server.

Exposes Open Dental's REST API as MCP tools so Claude / Cursor / any MCP client
can read patient, appointment, and clinical data, and write back communications.

Quick start:
    pip install -e .
    export OPENDENTAL_DEVELOPER_KEY=...
    export OPENDENTAL_CUSTOMER_KEY=...
    open_dental_mcp
"""
from __future__ import annotations

import json
import sys
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from .client import OpenDentalClient
from .exceptions import (
    OpenDentalAPIError,
    OpenDentalAuthError,
    OpenDentalConnectionError,
    OpenDentalError,
    OpenDentalNotFoundError,
    OpenDentalRateLimitError,
)

log = structlog.get_logger(__name__)


def _format_error(e: Exception) -> str:
    if isinstance(e, OpenDentalAuthError):
        return (
            "Authentication failed against Open Dental. Verify OPENDENTAL_DEVELOPER_KEY "
            f"and OPENDENTAL_CUSTOMER_KEY. Details: {e}"
        )
    if isinstance(e, OpenDentalNotFoundError):
        return f"Resource not found: {e}"
    if isinstance(e, OpenDentalRateLimitError):
        wait = f" Retry in {e.retry_after}s." if e.retry_after else ""
        return f"Open Dental rate limit hit.{wait} Slow down."
    if isinstance(e, OpenDentalConnectionError):
        return f"Network failure talking to Open Dental: {e}"
    if isinstance(e, OpenDentalAPIError):
        request_id = f" (request_id: {e.request_id})" if e.request_id else ""
        return f"Open Dental API error (HTTP {e.http_status}){request_id}: {e}"
    if isinstance(e, OpenDentalError):
        return f"Open Dental error: {e}"
    return f"Unexpected error: {e!r}"


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


mcp = FastMCP(
    "open_dental_mcp",
    instructions=(
        "Tools for talking to Open Dental — dental practice management software. "
        "Read patient records, appointments, providers, procedures, recalls. "
        "Write commlogs (phone calls, messages) back to the patient chart. "
        "Always pass through authentication via the OPENDENTAL_DEVELOPER_KEY and "
        "OPENDENTAL_CUSTOMER_KEY environment variables."
    ),
)


def _client() -> OpenDentalClient:
    return OpenDentalClient()


# ----- Patient tools --------------------------------------------------------


@mcp.tool()
async def get_patient(patnum: int) -> str:
    """Fetch a single patient by PatNum (Open Dental's internal patient ID)."""
    try:
        return _json(await _client().get_patient(patnum))
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def find_patients(
    lname: str | None = None,
    fname: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    birthdate: str | None = None,
    limit: int = 25,
) -> str:
    """Search for patients by name / phone / email / DOB. Returns matching records.

    At least one criterion must be provided. Use this to find a patient's
    PatNum before calling ``get_patient`` or other record-specific tools.
    """
    try:
        results = await _client().find_patients(
            lname=lname, fname=fname, phone=phone, email=email,
            birthdate=birthdate, limit=limit,
        )
        return _json({"count": len(results), "patients": results})
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def update_patient(patnum: int, updates_json: str) -> str:
    """Update fields on an existing patient record.

    ``updates_json`` is a JSON object string of fields to change, e.g.
    ``'{"Email": "new@example.com", "WkPhone": "5551234567"}'``. Only fields
    included in the object will be modified.
    """
    try:
        updates = json.loads(updates_json)
        if not isinstance(updates, dict):
            return "updates_json must decode to a JSON object (e.g. '{}')."
        return _json(await _client().update_patient(patnum, updates))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in updates_json: {e}"
    except OpenDentalError as e:
        return _format_error(e)


# ----- Appointment tools ----------------------------------------------------


@mcp.tool()
async def find_appointments(
    date: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    patnum: int | None = None,
    provnum: int | None = None,
    clinicnum: int | None = None,
    limit: int = 50,
) -> str:
    """List appointments with optional filters.

    Use ``date=YYYY-MM-DD`` for a single day, or ``date_start`` + ``date_end``
    for a range. Filter by ``patnum``, ``provnum``, or ``clinicnum``.
    """
    try:
        results = await _client().find_appointments(
            date=date, date_start=date_start, date_end=date_end,
            patnum=patnum, provnum=provnum, clinicnum=clinicnum, limit=limit,
        )
        return _json({"count": len(results), "appointments": results})
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def get_appointment(aptnum: int) -> str:
    """Fetch a single appointment by AptNum."""
    try:
        return _json(await _client().get_appointment(aptnum))
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def create_appointment(appointment_json: str) -> str:
    """Create a new appointment.

    ``appointment_json`` is a JSON object string with at minimum:
    ``PatNum``, ``AptDateTime``, ``ProvNum``, ``Op``, ``AppointmentTypeNum``.
    See Open Dental API docs for the full schema.
    """
    try:
        data = json.loads(appointment_json)
        if not isinstance(data, dict):
            return "appointment_json must decode to a JSON object."
        return _json(await _client().create_appointment(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in appointment_json: {e}"
    except OpenDentalError as e:
        return _format_error(e)


# ----- Provider / clinic tools ---------------------------------------------


@mcp.tool()
async def list_providers() -> str:
    """List all providers (dentists + hygienists) in the practice."""
    try:
        return _json(await _client().list_providers())
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def list_clinics() -> str:
    """List clinics (offices) accessible to this developer account."""
    try:
        return _json(await _client().list_clinics())
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def list_operatories(clinicnum: int | None = None) -> str:
    """List operatories (treatment rooms). Optionally filter by clinic number."""
    try:
        return _json(await _client().list_operatories(clinicnum=clinicnum))
    except OpenDentalError as e:
        return _format_error(e)


@mcp.tool()
async def list_appointment_types() -> str:
    """List appointment type definitions (cleaning, filling, exam, etc.)."""
    try:
        return _json(await _client().list_appointment_types())
    except OpenDentalError as e:
        return _format_error(e)


# ----- Procedure tools ------------------------------------------------------


@mcp.tool()
async def find_procedurelogs(
    patnum: int | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    limit: int = 50,
) -> str:
    """List procedure log entries (treatments performed). Filterable."""
    try:
        results = await _client().find_procedurelogs(
            patnum=patnum, date_start=date_start, date_end=date_end, limit=limit,
        )
        return _json({"count": len(results), "procedurelogs": results})
    except OpenDentalError as e:
        return _format_error(e)


# ----- Communication log tools ---------------------------------------------


@mcp.tool()
async def log_communication(
    patnum: int,
    comm_datetime: str,
    comm_type: int,
    note: str,
    comm_mode: int | None = None,
    sent_or_received: int | None = None,
) -> str:
    """Log a phone call / email / message to a patient chart (commlog).

    Open Dental uses integer codes — see:
    https://www.opendental.com/site/apicommlogs.html

    Common values:
      comm_type: 1=ApptRelated, 2=Insurance, 3=Recalls/Statements, 4=General,
                 5=Lab, 6=Financial, 7=Medical, 8=Other
      comm_mode: 1=None, 2=Phone, 3=Email, 4=TextMessage, 5=Mail
      sent_or_received: 0=Sent, 1=Received
    """
    try:
        result = await _client().create_commlog(
            patnum=patnum,
            comm_date=comm_datetime,
            comm_type=comm_type,
            note=note,
            comm_mode=comm_mode,
            sent_or_received=sent_or_received,
        )
        return _json({"status": "ok", "commlog": result})
    except OpenDentalError as e:
        return _format_error(e)


# ----- Recall tools ---------------------------------------------------------


@mcp.tool()
async def find_recalls(
    patnum: int | None = None,
    due_date_start: str | None = None,
    due_date_end: str | None = None,
    recall_type: int | None = None,
    limit: int = 50,
) -> str:
    """List recalls due for patients. Use ``due_date_start`` + ``due_date_end``
    to get everyone due for a hygiene visit in a date window."""
    try:
        results = await _client().find_recalls(
            patnum=patnum, due_date_start=due_date_start,
            due_date_end=due_date_end, recall_type=recall_type, limit=limit,
        )
        return _json({"count": len(results), "recalls": results})
    except OpenDentalError as e:
        return _format_error(e)


# ----- Health check ---------------------------------------------------------


@mcp.tool()
async def health_check() -> str:
    """Verify credentials are valid by hitting /providers (always accessible)."""
    try:
        await _client().list_providers()
        return _json({"status": "ok"})
    except OpenDentalError as e:
        return _format_error(e)


def main() -> None:
    try:
        mcp.run()
    except OpenDentalAuthError as e:
        log.error("server.auth_failed_on_start", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
