# Security

Report suspected vulnerabilities privately through this repository's
**Security → Report a vulnerability** flow. Include the affected version,
expected behavior, and a reproduction using synthetic records. Leave out
credentials and patient information.

This project limits Open Dental requests to GET, approved resource paths, and
three exact base URLs. HTTP is permitted only on IPv4 loopback. Keep the
credential file private and restrict the underlying key permissions. A read-only
server still returns potentially sensitive information to its MCP client.
