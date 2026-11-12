# MCP client setup

Configure a local STDIO connection using the command and arguments from the
[README](../README.md). For a client with a JSON config, copy the
[example](../examples/mcp-client.json) and replace both installation paths.
Keep API credentials in the local credential file. Provide its path, not the
key values, in client settings.

The Windows launcher starts the same server with the project's `.env`:
`powershell -File .\start-readonly.ps1`. It waits for MCP protocol messages,
so a terminal that appears idle after startup is expected.

## Verify the connection

1. Start or restart your MCP client and confirm it lists the 12 read tools.
2. Call `health_check`; a successful result reports `read_only: true`.
3. Request a schedule date and follow `next_offset` to finish pagination.
   A connection error or incomplete page sequence must not be treated as zero
   appointments.

On Windows, the optional live check prints metadata, not records. Supply a
date appropriate for your installation:

```powershell
.\.venv\Scripts\python.exe scripts/live_readonly_check.py --env-file .env --date 2026-01-05 --metadata-only
```

Removing `--metadata-only` adds a bounded appointment/patient sample on the
selected date. Use that mode only when authorized to read those records.

| Symptom | Check |
| --- | --- |
| Loopback connection fails | Port 30222: Open Dental is running and signed in. Port 30223: API Service or compatible Cloud Client is running. The MCP must run on that same host. |
| Remote connection fails | Network access, API enablement, and the office eConnector |
| Authentication or permission error | Credential pair, API enablement, and the resource's read permissions |
| Invalid base URL | Use one of the three exact addresses in the README, with an optional trailing slash |
| No tools in a cloud agent | Use an authorized local executor that can launch the STDIO process. This project does not provide a hosted MCP server. |
