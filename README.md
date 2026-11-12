# Open Dental MCP

A read-only Model Context Protocol (MCP) server for the Open Dental REST API.
Use it from an MCP client to look up appointments, patients, procedures, and
communication history. The server uses the documented API and exposes 12
read tools.

This is an independent MIT-licensed project based on
[sanjibani/open-dental-mcp](https://github.com/sanjibani/open-dental-mcp).
It is not affiliated with Open Dental Software.

## Quick start

Requires Python 3.10+ and your own Open Dental developer and customer API keys.
The development suite is verified on Windows with Python 3.13.

```powershell
git clone https://github.com/MaxilloCA/open-dental-mcp.git
cd open-dental-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
```

Edit the new `.env` file with your credentials and choose an endpoint below.
Use an API key with only the permissions your workflow needs. The key pair
is required in all three modes. See [Open Dental API setup](https://www.opendental.com/site/apisetup.html)
for prerequisites and permissions.

| Mode | `OPEN_DENTAL_BASE_URL` | Where the MCP runs |
| --- | --- | --- |
| Remote (default) | `https://api.opendental.com/api/v1/` | A machine that can reach the Remote API; the office needs a working eConnector |
| Workstation Local API | `http://127.0.0.1:30222/api/v1/` | The same workstation as a running, signed-in Open Dental desktop instance |
| API Service | `http://127.0.0.1:30223/api/v1/` | The same machine as the running API Service or compatible Open Dental Cloud Client |

The [official API mode guide](https://www.opendental.com/site/apilocal.html)
explains the distinction and Cloud Client support. In loopback modes, Open Dental
can still contact its servers for credential validation. A VM, container, or
remote agent has a different loopback interface from the Windows host.

For example, to use the API Service:

```dotenv
OPEN_DENTAL_BASE_URL=http://127.0.0.1:30223/api/v1/
OPENDENTAL_DEVELOPER_KEY=your-developer-key
OPENDENTAL_CUSTOMER_KEY=your-customer-key
```

Alternatively, replace both key lines with
`OPEN_DENTAL_API_KEY=developer-key/customer-key`. The legacy base URL aliases
`BASE_URL` and `OPENDENTAL_BASE_URL` are accepted in the credential file.
If aliases are supplied together, they must agree. An explicitly selected
credential file supplies the endpoint, defaulting to Remote if omitted.

## Connect your MCP client

Choose **STDIO**. Use the absolute path to the virtual environment's Python
executable as the command and pass these arguments separately:

```text
-c
from open_dental_mcp.server import main; main()
--env-file
<absolute path to your .env file>
```

See [example client configuration](examples/mcp-client.json) for clients that
use the `mcpServers` format. Replace its installation paths. On macOS/Linux,
the virtual environment executable is typically `.venv/bin/python`; the API
still needs to be reachable from that process. [Client setup](docs/CLIENT_SETUP.md)
includes a connection check and troubleshooting.

The client starts the MCP as a child process. The API address above is the
upstream Open Dental endpoint; it is not the URL of an MCP server.

## Tools

| Area | Tools |
| --- | --- |
| Connection | `health_check` |
| Patients | `get_patient`, `find_patients` |
| Appointments | `get_appointment`, `find_appointments` |
| Reference data | `list_providers`, `list_clinics`, `list_operatories`, `list_appointment_types` |
| Patient history | `find_procedurelogs`, `find_commlogs`, `find_recalls` |

Start with `health_check`, then try `find_appointments` with a date in
`YYYY-MM-DD` format. The health check tests one provider read; other tools
may require additional permissions or a compatible Open Dental version.

List calls return a page of up to 100 source rows. Follow `next_offset` with
unchanged filters until it is `null`, even if a locally filtered page is empty.
`count` is the number of matches on that page. A full page has
`has_more: null` until another request confirms whether more rows exist.

## Read-only design

- The server exposes no write tools. The HTTP client also enforces GET and
  checks the request path and parameters against an allowlist.
- The three endpoint URLs above are the only supported destinations. No LAN
  addresses, custom ports, redirects, automatic fallback, or environment proxies.
- Audit logs contain request metadata. Tool responses can contain patient
  information; use a client and workspace approved for your practice's data.
- Communication notes are records to interpret. They do not authorize actions
  or establish current illness, consent, or treatment clearance.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for test commands,
[CHANGELOG.md](CHANGELOG.md) for release changes, and
[SECURITY.md](SECURITY.md) to report a security issue. Tests use fake
credentials and mocked API responses. [MIT license](LICENSE).
