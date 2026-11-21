"""Tests for sanitized config defaults, IP CIDR resolution, and SQL script execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.config import Lab18Settings
from src.snowflake_semantic import (
    _resolve_allowed_ip_cidr,
    connect_snowflake_admin,
    execute_sql_script,
)


def test_settings_generic_defaults_and_dynamic_mcp_endpoint() -> None:
    """Uninitialized settings contain zero real tenant PII and derive MCP endpoint dynamically."""
    settings = Lab18Settings(_env_file=None)
    assert settings.dataverse_url == "https://your-org.crm.dynamics.com"
    assert settings.snowflake_account == "<YOUR_ORG>-<YOUR_ACCOUNT>"
    assert settings.snowflake_user == "<YOUR_SNOWFLAKE_USER>"
    assert settings.snowflake_private_key_file == "~/.ssh/snowflake_rsa_key.p8"

    custom = Lab18Settings(
        _env_file=None,
        snowflake_account="ACMEORG-DEMO01",
        snowflake_database="OSSIE_DB",
        snowflake_schema="ANALYTICS",
    )
    assert custom.snowflake_mcp_endpoint == (
        "https://acmeorg-demo01.snowflakecomputing.com"
        "/api/v2/databases/OSSIE_DB/schemas/ANALYTICS/mcp-servers/CRM_OSSIE_MCP_SERVER"
    )


def test_resolve_allowed_ip_cidr_appends_32_and_validates_explicit_values() -> None:
    """Explicit IP auto-appends /32 while explicit CIDR ranges are preserved."""
    ip_only = Lab18Settings(_env_file=None, snowflake_allowed_ip="203.0.113.10")
    assert _resolve_allowed_ip_cidr(ip_only) == "203.0.113.10/32"

    cidr = Lab18Settings(_env_file=None, snowflake_allowed_ip="203.0.113.10/32")
    assert _resolve_allowed_ip_cidr(cidr) == "203.0.113.10/32"

    invalid = Lab18Settings(_env_file=None, snowflake_allowed_ip="<YOUR_PUBLIC_IP>/32")
    with pytest.raises(RuntimeError, match="Invalid SNOWFLAKE_ALLOWED_IP"):
        _resolve_allowed_ip_cidr(invalid)


def test_execute_sql_script_substitutes_allowed_ip_cidr_via_ipify_fallback(tmp_path: Path) -> None:
    """Unset SNOWFLAKE_ALLOWED_IP resolves public IP via api.ipify.org in execute_sql_script."""
    sql_file = tmp_path / "mcp_setup.sql"
    sql_file.write_text(
        "CREATE NETWORK POLICY IF NOT EXISTS P ALLOWED_IP_LIST = ('__ALLOWED_IP_CIDR__');",
        encoding="utf-8",
    )

    ip_resp = MagicMock(text="198.51.100.42\n")
    ip_resp.raise_for_status = MagicMock()

    mock_conn = MagicMock()
    mock_conn.execute_string.return_value = [MagicMock()]

    settings = Lab18Settings(_env_file=None, snowflake_allowed_ip="")
    with (
        patch("src.snowflake_semantic.httpx.get", return_value=ip_resp) as mock_get,
        patch("src.snowflake_semantic.connect_snowflake_admin", return_value=mock_conn),
    ):
        stmt_count = execute_sql_script(settings, sql_file)

    mock_get.assert_called_once_with("https://api.ipify.org", timeout=10.0)
    executed_sql = mock_conn.execute_string.call_args.args[0]
    assert "'198.51.100.42/32'" in executed_sql
    assert "__ALLOWED_IP_CIDR__" not in executed_sql
    assert stmt_count == 1
    mock_conn.close.assert_called_once()


def test_resolve_allowed_ip_cidr_raises_explicit_error_when_offline() -> None:
    """Unset SNOWFLAKE_ALLOWED_IP raises RuntimeError if api.ipify.org is unreachable."""
    settings = Lab18Settings(_env_file=None, snowflake_allowed_ip="")
    with patch("src.snowflake_semantic.httpx.get", side_effect=httpx.ConnectError("offline")):
        with pytest.raises(RuntimeError, match="SNOWFLAKE_ALLOWED_IP is unset"):
            _resolve_allowed_ip_cidr(settings)


def test_connect_snowflake_admin_expands_tilde_key_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connect_snowflake_admin resolves ~/... key paths cleanly before connecting."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    key_file = ssh_dir / "test_key.p8"
    key_file.write_text("dummy", encoding="utf-8")

    settings = Lab18Settings(
        _env_file=None,
        snowflake_account="acct-01",
        snowflake_user="ADMIN_USER",
        snowflake_private_key_file="~/.ssh/test_key.p8",
    )
    with patch("src.snowflake_semantic.snowflake.connector.connect") as mock_connect:
        connect_snowflake_admin(settings)

    assert mock_connect.call_args.kwargs["private_key_file"] == str(key_file)
