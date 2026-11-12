"""Bounded live checks through the real MCP stdio connection.

Prints only check names/status/counts; patient records stay in process memory.
Explicit --env-file and --date are required. Never invokes a write tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED = {
    "health_check",
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
}


async def run(env_file: Path, day: str, metadata_only: bool) -> int:
    checks: list[dict[str, Any]] = []
    params = StdioServerParameters(
        command=str(Path(sys.executable)),
        args=[
            "-c",
            "from open_dental_mcp.server import main; main()",
            "--env-file",
            str(env_file.resolve()),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        if {t.name for t in tools} != EXPECTED or not all(
            t.annotations and t.annotations.readOnlyHint and not t.annotations.destructiveHint
            for t in tools
        ):
            raise RuntimeError("Unexpected tool registry.")
        checks.append({"check": "readonly_registry", "status": "pass", "count": len(tools)})

        async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
            result = await session.call_tool(name, arguments)
            if result.isError:
                # Do not echo tool errors: only internally classified safe status.
                text = " ".join(getattr(item, "text", "") for item in result.content)
                category = "api_error"
                if "credentials" in text or "permissions" in text:
                    category = "credentials_or_permissions"
                elif "Network" in text:
                    category = "network"
                checks.append({"check": name, "status": "fail", "category": category})
                return None
            payload = json.loads(next(item.text for item in result.content if item.type == "text"))
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected MCP result shape.")
            checks.append({"check": name, "status": "pass", "count": payload.get("count", 1)})
            return payload

        healthy = await call("health_check", {})
        if healthy is not None:
            for tool in (
                "list_providers",
                "list_clinics",
                "list_operatories",
                "list_appointment_types",
            ):
                await call(tool, {"limit": 2})
            if not metadata_only:
                appointments = await call("find_appointments", {"date": day, "limit": 2})
                if appointments is not None and appointments["results"]:
                    first = appointments["results"][0]
                    patnum, aptnum = first["PatNum"], first["AptNum"]
                    await call("get_appointment", {"aptnum": aptnum})
                    patient = await call("get_patient", {"patnum": patnum})
                    procedures = await call("find_procedurelogs", {"patnum": patnum, "limit": 2})
                    recalls = await call("find_recalls", {"patnum": patnum, "limit": 2})
                    for extra in (
                        {"date_start": day, "date_end": day},
                        {"date": day, "patnum": patnum},
                        {"date": day, "provnum": first["ProvNum"]},
                        {"date": day, "clinicnum": first["ClinicNum"]},
                    ):
                        await call("find_appointments", {**extra, "limit": 2})
                    await call("list_operatories", {"clinicnum": first["ClinicNum"], "limit": 2})
                    if procedures is not None and procedures["results"]:
                        proc_day = procedures["results"][0]["ProcDate"][:10]
                        await call(
                            "find_procedurelogs",
                            {
                                "patnum": patnum,
                                "limit": 2,
                                "date_start": proc_day,
                                "date_end": proc_day,
                            },
                        )
                    if recalls is not None and recalls["results"]:
                        recall = recalls["results"][0]
                        await call(
                            "find_recalls",
                            {
                                "patnum": patnum,
                                "limit": 2,
                                "due_date_start": recall["DateDue"],
                                "due_date_end": recall["DateDue"],
                                "recall_type": recall["RecallTypeNum"],
                            },
                        )
                    if patient is not None:
                        filters = {
                            "lname": patient["LName"],
                            "fname": patient["FName"],
                            "birthdate": patient["Birthdate"],
                            "limit": 25,
                        }
                        found = await call("find_patients", filters)
                        if found is not None:
                            matched = any(row.get("PatNum") == patnum for row in found["results"])
                            checks.append(
                                {
                                    "check": "patient_search_roundtrip",
                                    "status": "pass" if matched else "inconclusive",
                                }
                            )
                    if appointments["next_offset"] is not None:
                        next_page = await call(
                            "find_appointments",
                            {
                                "date": day,
                                "limit": 2,
                                "offset": appointments["next_offset"],
                            },
                        )
                        if next_page is not None:
                            overlap = {r["AptNum"] for r in appointments["results"]} & {
                                r["AptNum"] for r in next_page["results"]
                            }
                            checks.append(
                                {
                                    "check": "pagination_no_overlap",
                                    "status": "fail" if overlap else "pass",
                                }
                            )
                else:
                    checks.append({"check": "patient_linked_reads", "status": "not_exercised"})
        else:
            checks.append({"check": "remaining_live_checks", "status": "not_exercised"})
    sys.stdout.write(json.dumps({"read_only": True, "checks": checks}, indent=2) + "\n")
    return int(any(check["status"] == "fail" for check in checks))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    try:
        date.fromisoformat(args.date)
        code = asyncio.run(asyncio.wait_for(run(args.env_file, args.date, args.metadata_only), 180))
    except Exception:
        sys.stdout.write(
            '{"read_only":true,"status":"failed","reason":"check could not complete"}\n'
        )
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
