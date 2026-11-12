"""Audit records must contain metadata only, even on failure."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any

import pytest

from open_dental_mcp.audit import audit_tool_call


def test_audit_omits_all_argument_keys_and_values_and_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args: dict[str, Any] = {
        "patient-secret-key": "Patient Example",
        "patnum": 987654321,
        "birthdate": "2008-09-10",
        "phone": "555-123-0987",
        "email": "private@example.invalid",
        "nested": [{"Authorization": "ODFHIR secret/customer"}],
        "unexpected": object(),
    }
    with audit_tool_call("find_patients", args) as audit:
        audit.set_result("Clinical result: café")
        assert not hasattr(audit, "args")
    captured = capsys.readouterr()
    assert captured.out == ""
    record = json.loads(captured.err)
    assert set(record) == {
        "ts",
        "tool",
        "request_id",
        "argument_count",
        "result_size",
        "is_error",
        "error_type",
        "duration_ms",
    }
    assert record["argument_count"] == len(args)
    assert record["result_size"] == len("Clinical result: café".encode())
    assert record["is_error"] is False
    assert record["error_type"] is None
    assert record["duration_ms"] >= 0
    assert record["ts"].endswith("Z")
    for secret in (*args.keys(), "Patient Example", "987654321", "ODFHIR", "Clinical result"):
        assert secret not in captured.err


@pytest.mark.parametrize("error", [RuntimeError("private clinical text"), asyncio.CancelledError()])
def test_audit_errors_preserve_original_exception_and_emit_once(error: BaseException) -> None:
    buf = io.StringIO()
    with (
        redirect_stderr(buf),
        pytest.raises(type(error)) as caught,
        audit_tool_call("failing_tool", {}),
    ):
        raise error
    assert caught.value is error
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["is_error"] is True
    assert record["error_type"] == type(error).__name__
    assert record["result_size"] is None
    assert "private clinical text" not in buf.getvalue()


def test_audit_ignores_legacy_file_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "audit.jsonl"
    monkeypatch.setenv("OPEN_DENTAL_MCP_AUDIT_LOG", str(log_file))
    buf = io.StringIO()
    with redirect_stderr(buf), audit_tool_call("test_tool", {}) as audit:
        audit.set_result("ok")
    assert not log_file.exists()
    assert json.loads(buf.getvalue())["tool"] == "test_tool"


class _BrokenSink(io.StringIO):
    def write(self, text: str) -> int:
        raise OSError("sink unavailable")


def test_audit_sink_failure_preserves_result() -> None:
    with redirect_stderr(_BrokenSink()), audit_tool_call("test_tool", {}) as audit:
        audit.set_result("ok")


def test_audit_sink_failure_preserves_original_error() -> None:
    original = ValueError("patient detail")
    with (
        redirect_stderr(_BrokenSink()),
        pytest.raises(ValueError, match="patient detail") as caught,
        audit_tool_call("test_tool", {}),
    ):
        raise original
    assert caught.value is original
