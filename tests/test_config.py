import os
from pathlib import Path

import pytest

from open_dental_mcp.client import BASE_URL_ENV, DEFAULT_BASE_URL
from open_dental_mcp.config import load_project_env


@pytest.mark.parametrize("prefix", ["", "ODFHIR "])
def test_office_combined_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    target = tmp_path / ".env"
    target.write_text(f'OPEN_DENTAL_API_KEY="{prefix}developer/customer"\nIGNORED=secret\n')
    monkeypatch.delenv("IGNORED", raising=False)
    monkeypatch.delenv("OPENDENTAL_DEVELOPER_KEY", raising=False)
    monkeypatch.delenv("OPENDENTAL_CUSTOMER_KEY", raising=False)
    monkeypatch.setenv(BASE_URL_ENV, "http://127.0.0.1:30223/api/v1")
    load_project_env(target)

    assert os.environ["OPENDENTAL_DEVELOPER_KEY"] == "developer"
    assert os.environ["OPENDENTAL_CUSTOMER_KEY"] == "customer"
    assert "IGNORED" not in os.environ
    assert os.environ[BASE_URL_ENV] == DEFAULT_BASE_URL


@pytest.mark.parametrize(
    "contents",
    [
        "OPEN_DENTAL_API_KEY=onekey",
        "OPEN_DENTAL_API_KEY=dev/cust\nBASE_URL=https://evil.invalid/api/v1",
        "OPEN_DENTAL_API_KEY=dev/cust\nOPEN_DENTAL_API_KEY=other/key",
        "OPEN_DENTAL_API_KEY=dev/cust\nOPENDENTAL_CUSTOMER_KEY=other",
        "OPENDENTAL_DEVELOPER_KEY=dev",
        'OPEN_DENTAL_API_KEY="unterminated',
        "OPEN_DENTAL_API_KEY=dev/cust\nBASE_URL=https://api.opendental.com/api/v1\n"
        "OPENDENTAL_BASE_URL=https://evil.invalid",
        "OPEN_DENTAL_API_KEY=dev/cust\nBASE_URL=https://api.opendental.com/api/v1\n"
        "OPEN_DENTAL_BASE_URL=http://127.0.0.1:30223/api/v1/",
    ],
)
def test_bad_config_rejected(tmp_path: Path, contents: str) -> None:
    target = tmp_path / ".env"
    target.write_text(contents)
    with pytest.raises(ValueError, match=r".") as error:
        load_project_env(target)
    assert contents not in str(error.value)


def test_no_interpolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / ".env"
    target.write_text("OPENDENTAL_DEVELOPER_KEY=${DO_NOT_EXPAND}\nOPENDENTAL_CUSTOMER_KEY=cust")
    monkeypatch.setenv("DO_NOT_EXPAND", "private")
    monkeypatch.delenv("OPENDENTAL_DEVELOPER_KEY", raising=False)
    monkeypatch.delenv("OPENDENTAL_CUSTOMER_KEY", raising=False)
    with pytest.raises(ValueError, match="valid format"):
        load_project_env(target)
    assert "OPENDENTAL_DEVELOPER_KEY" not in os.environ
