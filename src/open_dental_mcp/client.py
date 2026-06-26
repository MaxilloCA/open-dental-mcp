"""Async HTTP client for Open Dental MCP."""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://api.example.com"
DEFAULT_TIMEOUT = 30.0


class OpenDentalError(RuntimeError):
    """Base exception for Open Dental MCP client errors."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OpenDentalAuthError(OpenDentalError):
    """Raised when credentials are missing, invalid, or unauthorized."""


class OpenDentalAPIError(OpenDentalError):
    """Raised on non-2xx API responses other than auth failures."""


class OpenDentalClient:
    """Async client for Open Dental MCP.

    Authentication: HTTP Basic. Pass credentials explicitly OR set
    ``OPENDENTAL_USERNAME`` and ``OPENDENTAL_PASSWORD`` in the environment.
    """

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        username = username or os.environ.get("OPENDENTAL_USERNAME")
        password = password or os.environ.get("OPENDENTAL_PASSWORD")
        if not username or not password:
            raise OpenDentalAuthError(
                "Open Dental MCP credentials missing. Set OPENDENTAL_USERNAME and "
                "OPENDENTAL_PASSWORD environment variables, or pass them to the client."
            )
        self._basic_auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Basic {self._basic_auth}",
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
                "Open Dental MCP rejected the credentials (HTTP 401).", 401
            )
        if response.status_code == 403:
            raise OpenDentalAuthError(
                "Open Dental MCP denied access (HTTP 403).", 403
            )
        if not 200 <= response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise OpenDentalAPIError(
                f"Open Dental MCP returned HTTP {response.status_code}",
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

    # TODO: Add your API methods here. Example:
    #
    # async def list_things(self) -> list[dict[str, Any]]:
    #     """List things. GET /things"""
    #     return await self._request("GET", "/things")
    #
    # async def get_thing(self, thing_id: int) -> dict[str, Any]:
    #     """Get a single thing. GET /things/{id}"""
    #     return await self._request("GET", f"/things/{thing_id}")
    #
    # async def create_thing(self, name: str, **kwargs) -> dict[str, Any]:
    #     """Create a thing. POST /things"""
    #     return await self._request("POST", "/things", json={"name": name, **kwargs})