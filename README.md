# open-dental-mcp

**Model Context Protocol (MCP) server for [Open Dental](https://www.opendental.com/)** — dental practice management software.

Talk to your Open Dental data from Claude, Cursor, or any MCP client. Read patients, appointments, providers, procedures, recalls. Write communications (phone calls, messages) back to patient charts.

Built against the [Open Dental REST API](https://www.opendental.com/site/apispecification.html). No existing MCP for Open Dental — this is the first.

---

## What you can do with it

```
You:   "Pull patient 4523 and show me their last 5 appointments + outstanding recalls."
Claude: *calls get_patient, then find_appointments, then find_recalls*

You:   "Find every recall due this month and draft a friendly reminder for each."
Claude: *calls find_recalls with due_date window, drafts the messages*

You:   "Just got off a call with the Smith family about rescheduling — log it."
Claude: *calls log_communication with the right comm_type + comm_mode*

You:   "What providers are working at clinic 2 tomorrow?"
Claude: *calls list_providers + find_appointments, filters*
```

## Install

```bash
pip install -e .
```

## Configure

You need two API keys from Open Dental's [Developer Portal](https://api.opendental.com/portal):

1. **Developer API Key** — applies to your integration (you register via `vendor.relations@opendental.com`)
2. **Customer API Key** — generated per-customer in the Developer Portal

```bash
export OPENDENTAL_DEVELOPER_KEY="..."
export OPENDENTAL_CUSTOMER_KEY="..."
```

### Who uses this server?

1. **Approved Open Dental API Partners** building tools for dental practices. Your developer key + customer keys work directly.
2. **Dental practices / DSOs** doing custom integration work. Email `vendor.relations@opendental.com` to get set up.

## Use with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "open_dental_mcp": {
      "command": "open_dental_mcp",
      "env": {
        "OPENDENTAL_DEVELOPER_KEY": "your-developer-key",
        "OPENDENTAL_CUSTOMER_KEY": "your-customer-key"
      }
    }
  }
}
```

## Tools

| Tool | Type | What it does |
| --- | --- | --- |
| `health_check` | Diagnostic | Verifies credentials |
| `get_patient` | Read | Fetch a patient by PatNum |
| `find_patients` | Read | Search by name / phone / email / DOB |
| `update_patient` | Write | Patch patient fields |
| `get_appointment` | Read | Fetch an appointment by AptNum |
| `find_appointments` | Read | List appointments (by date / patient / provider) |
| `create_appointment` | Write | Book a new appointment |
| `list_providers` | Read | All providers in the practice |
| `list_clinics` | Read | All clinics (offices) accessible |
| `list_operatories` | Read | Treatment rooms |
| `list_appointment_types` | Read | Cleaning / filling / exam definitions |
| `find_procedurelogs` | Read | Treatments performed |
| `log_communication` | Write | Log a phone call / email / message |
| `find_recalls` | Read | Hygiene follow-up reminders |

## Common patterns

### Pre-visit brief

```
User:  I'm seeing patient 4523 at 2pm. Brief me.
Agent: get_patient(patnum=4523)
       find_appointments(patnum=4523, limit=5)
       find_procedurelogs(patnum=4523, limit=10)
       find_recalls(patnum=4523)
       → Summary: patient info, last visits, what's been done, what's due
```

### Recall campaign

```
User:  Build this week's recall list and draft rebooking messages.
Agent: find_recalls(due_date_start="2026-06-26", due_date_end="2026-07-03")
       → for each: pull patient, draft a friendly message
```

### Post-call documentation

```
User:  Just took a call from the Smiths about rescheduling next week's cleaning.
Agent: find_patients(lname="Smith", fname="...", phone="...")
       log_communication(
         patnum=<found>,
         comm_datetime="2026-06-26T15:30:00",
         comm_type=1,           # ApptRelated
         comm_mode=2,           # Phone
         sent_or_received=1,    # Received
         note="Patient called to reschedule cleaning. Will call back with new date."
       )
```

## API coverage

| Open Dental endpoint | MCP tool |
| --- | --- |
| `GET /patients/{id}` | `get_patient` |
| `GET /patients` | `find_patients` |
| `PUT /patients/{id}` | `update_patient` |
| `GET /appointments/{id}` | `get_appointment` |
| `GET /appointments` | `find_appointments` |
| `POST /appointments` | `create_appointment` |
| `GET /providers` | `list_providers` |
| `GET /clinics` | `list_clinics` |
| `GET /operatories` | `list_operatories` |
| `GET /appointmenttypes` | `list_appointment_types` |
| `GET /procedurelogs` | `find_procedurelogs` |
| `POST /commlogs` | `log_communication` |
| `GET /recalls` | `find_recalls` |

Full Open Dental API: <https://www.opendental.com/site/apispecification.html>

## Development

```bash
pip install -e ".[dev]"
pytest
open_dental_mcp
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- Open Dental for the public REST API
- Built using [mcp-vertical-template](https://github.com/sanjibani/mcp-vertical-template)
- Inspired by [sanjibani/hawksoft-mcp](https://github.com/sanjibani/hawksoft-mcp) (insurance vertical, same pattern)

## See also

- [Open Dental API docs](https://www.opendental.com/site/apispecification.html)
- [Open Dental Developer Portal](https://api.opendental.com/portal/gwt/fhirportal.html)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [More vertical MCPs from sanjibani](https://github.com/sanjibani?q=-mcp)