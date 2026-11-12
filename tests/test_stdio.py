"""Real stdio subprocess integration with a network-free HTTP transport."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any

import pytest

CHILD_PROGRAM = r"""
import os

import httpx

_original_client = httpx.AsyncClient

def respond(request):
    assert request.method == "GET", "Write attempted"
    endpoint = os.environ["TEST_EXPECTED_BASE_URL"]
    assert str(request.url.copy_with(query=None)).startswith(endpoint + "/")
    assert request.headers["Authorization"] == "ODFHIR FAKE_DEV/FAKE_CUSTOMER"
    if request.url.path.endswith("/providers"):
        return httpx.Response(200, json=[{"ProvNum": 1, "Abbr": "TEST"}])
    return httpx.Response(404, text="PRIVATE_PATIENT FAKE_DEV FAKE_CUSTOMER")

def mock_client(*args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(respond)
    return _original_client(*args, **kwargs)

from open_dental_mcp.server import main
httpx.AsyncClient = mock_client
main()
"""


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.opendental.com/api/v1/",
        "http://127.0.0.1:30222/api/v1/",
        "http://127.0.0.1:30223/api/v1/",
    ],
)
def test_real_stdio_initialization_read_success_and_safe_errors(
    tmp_path: Path, base_url: str
) -> None:
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "readonly-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_providers",
                "arguments": {"limit": 1},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_patient",
                "arguments": {"patnum": 123456789},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "update_patient",
                "arguments": {"patnum": 1},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "get_patient",
                "arguments": {"patnum": "PRIVATE_PATIENT"},
            },
        },
    ]
    env = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")
        if key in os.environ
    }
    env["TEST_EXPECTED_BASE_URL"] = base_url.removesuffix("/")
    config_file = tmp_path / "fixture.env"
    config_file.write_text(
        f"OPEN_DENTAL_API_KEY=FAKE_DEV/FAKE_CUSTOMER\nOPEN_DENTAL_BASE_URL={base_url}\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD_PROGRAM, "--env-file", str(config_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    output: Queue[str] = Queue()
    stdout_stream = process.stdout

    def collect_stdout() -> None:
        for line in stdout_stream:
            output.put(line)

    reader = Thread(target=collect_stdout, daemon=True)
    reader.start()
    raw_responses = []
    try:
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            if "id" in request:
                raw_responses.append(output.get(timeout=10))
        process.stdin.close()
        process.wait(timeout=10)
        reader.join(timeout=5)
        stderr = process.stderr.read()
        assert process.returncode == 0
        assert not reader.is_alive()
        assert output.empty()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
    stdout = "".join(raw_responses)
    # Every stdout line must be a JSON-RPC response, with no log contamination.
    responses = [json.loads(line) for line in stdout.splitlines()]
    assert all(response["jsonrpc"] == "2.0" for response in responses)
    by_id = {response["id"]: response for response in responses}
    assert set(by_id) == {1, 2, 3, 4, 5, 6}
    assert by_id[1]["result"]["serverInfo"]["name"] == "open_dental_mcp"
    tools = by_id[2]["result"]["tools"]
    assert len(tools) == 12
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)
    assert not {"update_patient", "create_appointment", "log_communication"} & {
        tool["name"] for tool in tools
    }
    success = by_id[3]["result"]
    assert success["isError"] is False
    page = json.loads(success["content"][0]["text"])
    assert page["results"] == [{"ProvNum": 1, "Abbr": "TEST"}]
    assert page["next_offset"] == 1
    assert page["has_more"] is None
    for failed_id in (4, 5, 6):
        assert by_id[failed_id]["result"]["isError"] is True
    for private in ("PRIVATE_PATIENT", "123456789", "FAKE_DEV", "FAKE_CUSTOMER"):
        assert private not in stdout
        assert private not in stderr
    audit_lines = [json.loads(line) for line in stderr.splitlines()]
    assert len(audit_lines) == 2
    assert {record["tool"] for record in audit_lines} == {"list_providers", "get_patient"}
