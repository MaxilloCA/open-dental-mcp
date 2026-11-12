"""Read-only MCP boundary, privacy, pagination and client lifetime regressions."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp import types
from mcp.server.fastmcp.exceptions import ToolError

from open_dental_mcp import server
from open_dental_mcp.exceptions import OpenDentalAPIError, OpenDentalAuthError

READ_TOOLS = {
    "get_patient",
    "find_patients",
    "get_appointment",
    "find_appointments",
    "list_providers",
    "list_clinics",
    "list_operatories",
    "list_appointment_types",
    "find_procedurelogs",
    "find_recalls",
    "find_commlogs",
    "health_check",
}
WRITE_TOOLS = ("update_patient", "create_appointment", "log_communication")


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    monkeypatch.setattr(server, "_client", lambda: client)
    return client


async def test_only_annotated_read_tools_are_registered() -> None:
    tools = await server.mcp.list_tools()
    assert {tool.name for tool in tools} == READ_TOOLS
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is True


@pytest.mark.parametrize("tool", WRITE_TOOLS)
async def test_write_tools_cannot_be_called(tool: str, fake_client: AsyncMock) -> None:
    assert not hasattr(server, tool)
    with pytest.raises(ToolError):
        await server.mcp.call_tool(tool, {"patnum": 123})
    fake_client.__aenter__.assert_not_awaited()


@pytest.mark.parametrize(
    ("name", "args", "method", "result"),
    [
        ("get_patient", {"patnum": 1}, "get_patient", {"PatNum": 1}),
        ("find_patients", {"lname": "TEST"}, "find_patients", []),
        ("get_appointment", {"aptnum": 1}, "get_appointment", {"AptNum": 1}),
        ("find_appointments", {"patnum": 1}, "find_appointments", []),
        ("list_providers", {}, "list_providers", []),
        ("list_clinics", {}, "list_clinics", []),
        ("list_operatories", {}, "list_operatories", []),
        ("list_appointment_types", {}, "list_appointment_types", []),
        ("find_procedurelogs", {"patnum": 1}, "find_procedurelogs", []),
        ("find_recalls", {"patnum": 1}, "find_recalls", []),
        ("find_commlogs", {"patnum": 1}, "find_commlogs", []),
        ("health_check", {}, "list_providers", []),
    ],
)
async def test_all_tools_close_clients(
    fake_client: AsyncMock,
    name: str,
    args: dict[str, Any],
    method: str,
    result: Any,
) -> None:
    getattr(fake_client, method).return_value = result
    await server.mcp.call_tool(name, args)
    fake_client.__aenter__.assert_awaited_once()
    fake_client.__aexit__.assert_awaited_once()


async def test_exception_is_safe_and_client_closed(fake_client: AsyncMock, capsys: Any) -> None:
    private = "secret-key PatientName 12345 https://private.example/?birthdate=2001-01-01"
    fake_client.get_patient.side_effect = OpenDentalAPIError(
        private, request_id=private, body=private
    )
    with pytest.raises(ToolError) as err:
        await server.mcp.call_tool("get_patient", {"patnum": 12345})
    fake_client.__aexit__.assert_awaited_once()
    assert private not in str(err.value)
    assert "API request failed" in str(err.value)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "12345" not in captured.err
    assert "PatientName" not in captured.err
    assert "secret-key" not in captured.err
    assert '"is_error":true' in captured.err


async def test_client_construction_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> Any:
        raise OpenDentalAuthError("DO_NOT_DISCLOSE")

    monkeypatch.setattr(server, "_client", fail)
    with pytest.raises(ToolError) as err:
        await server.mcp.call_tool("health_check", {})
    assert "DO_NOT_DISCLOSE" not in str(err.value)
    assert "OPENDENTAL_CUSTOMER_KEY" in str(err.value)


@pytest.mark.parametrize("value", ["PRIVATE_PATIENT", -1, 0, True, 1.5])
async def test_schema_errors_never_echo_inputs(value: Any, fake_client: AsyncMock) -> None:
    with pytest.raises(ToolError) as err:
        await server.mcp.call_tool("get_patient", {"patnum": value})
    assert "PRIVATE_PATIENT" not in str(err.value)
    assert "invalid arguments" in str(err.value)
    fake_client.__aenter__.assert_not_awaited()


@pytest.mark.parametrize(
    "args", [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"offset": 10_000_001}]
)
async def test_pagination_bounds_reject_before_connection(
    args: dict[str, int],
    fake_client: AsyncMock,
) -> None:
    with pytest.raises(ToolError):
        await server.mcp.call_tool("list_providers", args)
    fake_client.__aenter__.assert_not_awaited()


async def test_full_page_does_not_claim_total(fake_client: AsyncMock) -> None:
    fake_client.list_providers.return_value = [{"ProvNum": 1}]
    result = json.loads(await server.list_providers(limit=1, offset=5))
    assert result == {
        "count": 1,
        "results": [{"ProvNum": 1}],
        "offset": 5,
        "limit": 1,
        "scanned_count": 1,
        "next_offset": 6,
        "has_more": None,
    }


async def test_empty_filtered_page_keeps_source_cursor(fake_client: AsyncMock) -> None:
    class FilteredPage(list[Any]):
        scanned_count = 10
        next_offset = 30
        has_more = None

    fake_client.find_recalls.return_value = FilteredPage()
    result = json.loads(await server.find_recalls(patnum=1, limit=10, offset=20))
    assert result["count"] == 0
    assert result["scanned_count"] == 10
    assert result["next_offset"] == 30
    assert result["has_more"] is None


async def test_health_check_is_minimal_and_limited(fake_client: AsyncMock) -> None:
    fake_client.list_providers.return_value = [{"FName": "PRIVATE_PROVIDER"}]
    result = json.loads(await server.health_check())
    assert result == {"status": "ok", "read_only": True, "verified_endpoint": "providers"}
    fake_client.list_providers.assert_awaited_once_with(limit=1, offset=0)


def test_error_formatter_does_not_echo_metadata() -> None:
    for exc in [ValueError("PRIVATE"), OpenDentalAPIError("PRIVATE", request_id="PRIVATE")]:
        assert "PRIVATE" not in server._format_error(exc)


async def test_wire_errors_are_marked_and_redacted(fake_client: AsyncMock) -> None:
    handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]
    fake_client.get_patient.side_effect = OpenDentalAPIError("PRIVATE_PATIENT")
    for arguments in ({"patnum": 1}, {"patnum": "PRIVATE_PATIENT"}):
        result = await handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(name="get_patient", arguments=arguments)
            )
        )
        assert isinstance(result.root, types.CallToolResult)
        assert result.root.isError is True
        assert "PRIVATE_PATIENT" not in result.model_dump_json()


async def test_unknown_tool_wire_error_does_not_echo_name(fake_client: AsyncMock) -> None:
    handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]
    result = await handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(name="PRIVATE_PATIENT", arguments={})
        )
    )
    assert isinstance(result.root, types.CallToolResult)
    assert result.root.isError is True
    assert "PRIVATE_PATIENT" not in result.model_dump_json()
    fake_client.__aenter__.assert_not_awaited()


def test_cli_loads_only_explicit_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = Mock()
    runner = Mock()
    monkeypatch.setattr(server, "load_project_env", loader)
    monkeypatch.setattr(server.mcp, "run", runner)
    monkeypatch.setattr("sys.argv", ["open_dental_mcp"])
    server.main()
    loader.assert_not_called()
    runner.assert_called_once_with(transport="stdio")

    monkeypatch.setattr("sys.argv", ["open_dental_mcp", "--env-file", "office.env"])
    server.main()
    loader.assert_called_once_with(Path("office.env"))


def test_cli_errors_never_echo_values(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr("sys.argv", ["open_dental_mcp", "--PRIVATE", "SECRET"])
    with pytest.raises(SystemExit) as err:
        server.main()
    assert err.value.code == 1
    captured = capsys.readouterr()
    assert "SECRET" not in captured.err
    assert "PRIVATE" not in captured.err
    assert captured.out == ""


async def test_nonfinite_result_is_an_error(fake_client: AsyncMock) -> None:
    fake_client.get_patient.return_value = {"PatNum": 1, "Balance": float("nan")}
    with pytest.raises(ToolError):
        await server.mcp.call_tool("get_patient", {"patnum": 1})
    fake_client.__aexit__.assert_awaited_once()


async def test_unknown_filter_is_not_silently_ignored(fake_client: AsyncMock) -> None:
    with pytest.raises(ToolError) as err:
        await server.mcp.call_tool("find_appointments", {"patnum": 1, "prov_num": 2})
    assert "invalid arguments" in str(err.value)
    fake_client.__aenter__.assert_not_awaited()


@pytest.mark.parametrize(
    "name",
    [
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
        "mcp.server.lowlevel.server",
    ],
)
def test_library_log_filter_survives_level_override(name: str) -> None:
    logger = logging.getLogger(name)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.error("PRIVATE_PATIENT FAKE_KEY")
        assert stream.getvalue() == ""
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
