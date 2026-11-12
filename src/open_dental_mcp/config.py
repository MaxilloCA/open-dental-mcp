"""Load credentials only from a deliberately selected file, without interpolation."""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

from dotenv.parser import parse_stream

from .client import BASE_URL_ENV, DEFAULT_BASE_URL, validate_base_url


def load_project_env(path: str | Path) -> None:
    """Accept upstream's two keys or the office's combined developer/customer key."""
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            text = stream.read(16_385)
    except (OSError, UnicodeError):
        raise ValueError("Cannot read the selected credential file.") from None
    if len(text) > 16_384:
        raise ValueError("Credential file exceeds the size limit.")
    values: dict[str, str] = {}
    for binding in parse_stream(io.StringIO(text)):
        if binding.error or (binding.key is not None and binding.value is None):
            raise ValueError("Invalid credential file syntax.")
        if binding.key is not None and binding.value is not None:
            if binding.key in values:
                raise ValueError("Duplicate credential file key.")
            values[binding.key] = binding.value
    endpoints = {
        validate_base_url(values[name])
        for name in (BASE_URL_ENV, "OPENDENTAL_BASE_URL", "BASE_URL")
        if name in values
    }
    if len(endpoints) > 1:
        raise ValueError("Conflicting API base URLs.")
    base_url = next(iter(endpoints), DEFAULT_BASE_URL)
    names = ("OPENDENTAL_DEVELOPER_KEY", "OPENDENTAL_CUSTOMER_KEY")
    if "OPEN_DENTAL_API_KEY" in values:
        if any(name in values for name in names):
            raise ValueError("Use combined credentials or separate keys, not both.")
        combined = values["OPEN_DENTAL_API_KEY"].removeprefix("ODFHIR ")
        if combined.count("/") != 1:
            raise ValueError("Combined API credential must contain developer/customer keys.")
        developer, customer = combined.split("/")
    else:
        developer, customer = (values.get(name, "") for name in names)
    if any(not re.fullmatch(r"[A-Za-z0-9._~+\-=]{3,512}", key) for key in (developer, customer)):
        raise ValueError("Both API keys must have a valid format.")
    # Apply the validated configuration together; ignore unrelated dotenv settings.
    os.environ[names[0]] = developer
    os.environ[names[1]] = customer
    os.environ[BASE_URL_ENV] = base_url
