# Changelog

## 0.4.0

- Support the workstation Local API on IPv4 loopback port 30222 and API Service
  on IPv4 loopback port 30223, alongside the official Remote API.
- Add `OPEN_DENTAL_BASE_URL` configuration and reject conflicting endpoint values.
- Test endpoint selection over the STDIO protocol with mocked HTTP transport.
- Document installation, client setup, contribution, and private security reporting.

This edition carries forward the read-only fork's bounded pagination, validated
response shapes, privacy-conscious logs, and patient-scoped communication reader.
