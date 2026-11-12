# Contributing

Use a virtual environment and install development dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m mypy
```

The lock records the tested Windows Python 3.13 environment. On another
platform, install with `python -m pip install -e ".[dev]"` and run the same checks
using the virtual environment's Python.

Use synthetic records and mocked transport for tests. Please keep changes small
and include regression coverage for behavior changes. Preserve the GET-only
request guard, endpoint allowlist, pagination contract, and metadata-only logs.
Do not include API keys, practice configuration, or patient records in patches,
issues, examples, or test fixtures.
