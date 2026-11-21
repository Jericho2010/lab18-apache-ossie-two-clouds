"""Snowflake Semantic View compiler, MCP provisioning, PAT rotation, and ground-truth SQL."""

from __future__ import annotations

import ipaddress
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import snowflake.connector
from pydantic import BaseModel

from src.config import Lab18Settings


class GroundTruthSummary(BaseModel):
    """Direct SQL totals from CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS + Lab 18 Dataverse dims."""

    total_accounts: int
    total_revenue: Decimal
    preferred_active_accounts: int
    preferred_active_revenue: Decimal


def _resolve_allowed_ip_cidr(settings: Lab18Settings) -> str:
    """Resolve an IP/CIDR allowlist entry for LAB18_WORKSTATION_IP_POLICY."""
    raw = settings.snowflake_allowed_ip.strip()
    if raw:
        try:
            if "/" in raw:
                return str(ipaddress.ip_network(raw, strict=False))
            ip_addr = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid SNOWFLAKE_ALLOWED_IP '{raw}'. Provide a valid IPv4/IPv6 "
                "address or CIDR (for example 203.0.113.10/32), or leave blank for auto-detection."
            ) from exc
        suffix = "/32" if ip_addr.version == 4 else "/128"
        return f"{ip_addr}{suffix}"

    try:
        resp = httpx.get("https://api.ipify.org", timeout=10.0)
        resp.raise_for_status()
        ip_addr = ipaddress.ip_address(resp.text.strip())
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(
            "SNOWFLAKE_ALLOWED_IP is unset and live public IP lookup failed "
            "(https://api.ipify.org). Set SNOWFLAKE_ALLOWED_IP=<YOUR_PUBLIC_IP>/32 in .env."
        ) from exc
    suffix = "/32" if ip_addr.version == 4 else "/128"
    return f"{ip_addr}{suffix}"


def connect_snowflake_admin(settings: Lab18Settings) -> snowflake.connector.SnowflakeConnection:
    """Open an authenticated Snowflake connection via RSA key-pair."""
    key_path = Path(settings.snowflake_private_key_file).expanduser()
    if not key_path.exists():
        raise FileNotFoundError(f"Snowflake RSA private key not found: {key_path}")
    return snowflake.connector.connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        private_key_file=str(key_path),
        role=settings.snowflake_role,
        warehouse=settings.snowflake_warehouse,
        database=settings.snowflake_database,
        schema=settings.snowflake_schema,
        enable_server_session_keep_alive_auto_detection=False,
    )


def execute_sql_script(settings: Lab18Settings, sql_path: Path) -> int:
    """Execute a multi-statement SQL file on Snowflake and return statement count."""
    sql_text = sql_path.read_text(encoding="utf-8")
    if "__ALLOWED_IP_CIDR__" in sql_text:
        sql_text = sql_text.replace("__ALLOWED_IP_CIDR__", _resolve_allowed_ip_cidr(settings))
    conn = connect_snowflake_admin(settings)
    try:
        results = conn.execute_string(sql_text)
        return len(results)
    finally:
        conn.close()


def compile_ossie_semantic_view(settings: Lab18Settings, yaml_path: Path) -> str:
    """Compile an Apache Ossie YAML file into a native Snowflake Semantic View."""
    yaml_text = yaml_path.read_text(encoding="utf-8")
    target_schema = f"{settings.snowflake_database}.{settings.snowflake_schema}"
    conn = connect_snowflake_admin(settings)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML(%s, %s)",
                (target_schema, yaml_text),
            )
            row = cur.fetchone()
            return str(row[0]) if row else "Compiled"
        finally:
            cur.close()
    finally:
        conn.close()


def read_ossie_roundtrip_yaml(settings: Lab18Settings, output_path: Path | None = None) -> str:
    """Read back compiled Ossie YAML via SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW."""
    conn = connect_snowflake_admin(settings)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW(%s)",
                (settings.semantic_view_fqn,),
            )
            row = cur.fetchone()
            yaml_text = str(row[0]) if row else ""
        finally:
            cur.close()
    finally:
        conn.close()

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_text, encoding="utf-8")
    return yaml_text


def update_env_pat(env_path: Path, pat_secret: str) -> None:
    """Write or replace SNOWFLAKE_MCP_PAT inside .env with mode 0600 without printing it."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("SNOWFLAKE_MCP_PAT="):
            updated.append(f"SNOWFLAKE_MCP_PAT={pat_secret}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"SNOWFLAKE_MCP_PAT={pat_secret}")
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def issue_scoped_mcp_pat(
    settings: Lab18Settings,
    env_path: Path,
    token_name: str = "LAB18_MCP_PAT",
) -> None:
    """Issue a scoped PAT for SVC_LAB18_MCP (ROLE_LAB18_MCP) and store it only in .env."""
    conn = connect_snowflake_admin(settings)
    try:
        cur = conn.cursor()
        try:
            try:
                cur.execute(
                    f"ALTER USER SVC_LAB18_MCP REMOVE PROGRAMMATIC ACCESS TOKEN {token_name}"
                )
            except snowflake.connector.errors.ProgrammingError:
                pass
            cur.execute(
                f"ALTER USER SVC_LAB18_MCP ADD PROGRAMMATIC ACCESS TOKEN {token_name} "
                "ROLE_RESTRICTION = 'ROLE_LAB18_MCP' DAYS_TO_EXPIRY = 30"
            )
            row = cur.fetchone()
            cols = [desc[0].lower() for desc in (cur.description or [])]
            if not row:
                raise RuntimeError("Snowflake did not return a PAT secret row.")
            secret_idx = cols.index("token_secret") if "token_secret" in cols else 1
            pat_secret = str(row[secret_idx]).strip()
            if not pat_secret:
                raise RuntimeError("Empty PAT secret returned by Snowflake.")
            update_env_pat(env_path, pat_secret)
        finally:
            cur.close()
    finally:
        conn.close()


def query_direct_sql_ground_truth(settings: Lab18Settings) -> GroundTruthSummary:
    """Query live Dataverse-synced accounts + lookup dims for numeric parity verification."""
    sql = """
    SELECT
        COUNT(a.ACCOUNTID) AS TOTAL_ACCOUNTS,
        COALESCE(SUM(a.REVENUE), 0) AS TOTAL_REVENUE,
        COUNT_IF(c.CATEGORY_LABEL = 'Preferred Customer' AND s.STATE_LABEL = 'Active')
            AS PREFERRED_ACTIVE_ACCOUNTS,
        COALESCE(SUM(
            CASE WHEN c.CATEGORY_LABEL = 'Preferred Customer' AND s.STATE_LABEL = 'Active'
                 THEN a.REVENUE ELSE 0 END
        ), 0) AS PREFERRED_ACTIVE_REVENUE
    FROM CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS a
    LEFT JOIN CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_CATEGORY c
        ON a.ACCOUNTCATEGORYCODE = c.CATEGORY_CODE
    LEFT JOIN CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_STATE s
        ON a.STATECODE = s.STATE_CODE
    """
    conn = connect_snowflake_admin(settings)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            row: tuple[Any, ...] | None = cur.fetchone()
            if not row:
                raise RuntimeError("No rows returned from ground truth SQL query.")
            return GroundTruthSummary(
                total_accounts=int(row[0]),
                total_revenue=Decimal(str(row[1])),
                preferred_active_accounts=int(row[2]),
                preferred_active_revenue=Decimal(str(row[3])),
            )
        finally:
            cur.close()
    finally:
        conn.close()
