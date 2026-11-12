"""Metadata-only JSONL auditing to stderr, keeping stdio protocol output clean.

Never retain or log argument names/values, result contents, or exception messages.
Patient identifiers, search terms, and clinical notes must not enter diagnostic
logs. The former file-sink environment setting is intentionally unsupported.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


class _AuditRecord:
    """Call metadata without references to the supplied arguments or results."""

    __slots__ = (
        "_error_type",
        "_is_error",
        "_result_size",
        "_start",
        "argument_count",
        "request_id",
        "tool",
    )

    def __init__(self, tool: str, args: dict[str, Any]) -> None:
        self.tool = tool
        self.argument_count = len(args)
        self.request_id = str(uuid.uuid4())
        self._start = time.monotonic()
        self._result_size: int | None = None
        self._is_error = False
        self._error_type: str | None = None

    def set_result(self, result: str) -> None:
        self._result_size = len(result.encode("utf-8"))

    def set_error(self, exc: BaseException) -> None:
        self._is_error = True
        self._error_type = type(exc).__name__

    def to_jsonl(self) -> str:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        record = {
            "ts": timestamp.replace("+00:00", "Z"),
            "tool": self.tool,
            "request_id": self.request_id,
            "argument_count": self.argument_count,
            "result_size": self._result_size,
            "is_error": self._is_error,
            "error_type": self._error_type,
            "duration_ms": int((time.monotonic() - self._start) * 1000),
        }
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def audit_tool_call(tool: str, args: dict[str, Any]) -> Iterator[_AuditRecord]:
    """Emit one metadata-only record without affecting the tool's outcome."""
    record = _AuditRecord(tool, args)
    # Do not keep a reference to sensitive inputs across the tool's execution.
    del args
    try:
        yield record
    except BaseException as exc:
        record.set_error(exc)
        raise
    finally:
        with contextlib.suppress(Exception):
            print(record.to_jsonl(), file=sys.stderr, flush=True)  # noqa: T201


__all__ = ["audit_tool_call"]
