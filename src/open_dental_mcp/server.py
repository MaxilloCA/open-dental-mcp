"""Open Dental MCP MCP server.

Exposes Open Dental MCP as MCP tools so Claude / Cursor / any MCP client can
read and write data through the Open Dental MCP API.

Quick start:
    pip install -e .
    export OPENDENTAL_USERNAME=...
    export OPENDENTAL_PASSWORD=...
    open_dental_mcp
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import OpenDentalAPIError, OpenDentalAuthError, OpenDentalClient, OpenDentalError


def _format_error(e: Exception) -> str:
    if isinstance(e, OpenDentalAuthError):
        return f"Authentication failed against Open Dental MCP. Check credentials. Details: {e}"
    if isinstance(e, OpenDentalAPIError):
        return f"Open Dental MCP API error (HTTP {e.status_code}): {e}"
    if isinstance(e, OpenDentalError):
        return f"Open Dental MCP error: {e}"
    return f"Unexpected error: {e!r}"


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


mcp = FastMCP(
    "open_dental_mcp",
    instructions=(
        "Tools for interacting with Open Dental MCP via its API. "
        "Use these to read and write data for any account the user has authorized."
    ),
)


def _client() -> OpenDentalClient:
    return OpenDentalClient()


# ----- Tools ----------------------------------------------------------------


@mcp.tool()
async def health_check() -> str:
    """Verify credentials are valid by hitting a known endpoint."""
    try:
        # TODO: replace with a real endpoint, e.g. self._request("GET", "/me")
        await _client()._request("GET", "/health")  # adjust to your SaaS
        return _json({"status": "ok"})
    except OpenDentalError as e:
        return _format_error(e)


# TODO: add your vertical-specific tools here. Example:
#
# @mcp.tool()
# async def list_things(limit: int = 25) -> str:
#     """List the user's things. Useful for browsing or finding IDs."""
#     try:
#         result = await _client().list_things()
#         return _json({"count": len(result), "items": result[:limit]})
#     except OpenDentalError as e:
#         return _format_error(e)


def main() -> None:
    """Run the MCP server over stdio."""
    try:
        mcp.run()
    except OpenDentalAuthError as e:
        print(f"[open_dental_mcp-mcp] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()