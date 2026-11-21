"""Live Dataverse Picklist & State OptionSet sync into Snowflake lookup dimensions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.config import Lab18Settings

MERGE_CATEGORY_SQL = """
MERGE INTO CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_CATEGORY AS tgt
USING (SELECT %s AS CATEGORY_CODE, %s AS CATEGORY_LABEL) AS src
ON tgt.CATEGORY_CODE = src.CATEGORY_CODE
WHEN MATCHED THEN UPDATE SET
    tgt.CATEGORY_LABEL = src.CATEGORY_LABEL,
    tgt.SYNCED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (CATEGORY_CODE, CATEGORY_LABEL)
    VALUES (src.CATEGORY_CODE, src.CATEGORY_LABEL);
"""

MERGE_STATE_SQL = """
MERGE INTO CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_STATE AS tgt
USING (SELECT %s AS STATE_CODE, %s AS STATE_LABEL) AS src
ON tgt.STATE_CODE = src.STATE_CODE
WHEN MATCHED THEN UPDATE SET
    tgt.STATE_LABEL = src.STATE_LABEL,
    tgt.SYNCED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (STATE_CODE, STATE_LABEL)
    VALUES (src.STATE_CODE, src.STATE_LABEL);
"""


class PicklistOptionEntry(BaseModel):
    """Single Dataverse OptionSet value/label entry."""

    value: int
    label: str


class PicklistSyncResult(BaseModel):
    """Summary of live Dataverse Picklist/State OptionSets synced to Snowflake."""

    categories: list[PicklistOptionEntry] = Field(default_factory=list)
    states: list[PicklistOptionEntry] = Field(default_factory=list)


def get_az_access_token(resource_url: str) -> str:
    """Acquire an OAuth token for Dataverse via Azure CLI without logging secrets."""
    normalized = resource_url.rstrip("/")
    proc = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            normalized,
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    token = proc.stdout.strip()
    if proc.returncode != 0 or not token:
        raise RuntimeError(
            "Unable to acquire Azure CLI token for Dataverse. Run `az login` first."
        )
    return token


def parse_optionset_payload(payload: dict[str, Any]) -> list[PicklistOptionEntry]:
    """Extract sorted PicklistOptionEntry items from Dataverse OptionSet JSON."""
    raw_options = payload.get("OptionSet", {}).get("Options", [])
    entries: list[PicklistOptionEntry] = []
    for item in raw_options:
        val = item.get("Value")
        if val is None:
            continue
        label = (
            item.get("Label", {})
            .get("UserLocalizedLabel", {})
            .get("Label")
        )
        entries.append(PicklistOptionEntry(value=int(val), label=str(label or val)))
    return sorted(entries, key=lambda entry: entry.value)


def fetch_dataverse_optionset(
    base_api: str,
    token: str,
    entity_name: str,
    attribute_name: str,
    metadata_subtype: str,
) -> list[PicklistOptionEntry]:
    """Query Dataverse Web API v9.2 for a single Picklist/State OptionSet."""
    url = (
        f"{base_api}/EntityDefinitions(LogicalName='{entity_name}')/"
        f"Attributes(LogicalName='{attribute_name}')/"
        f"Microsoft.Dynamics.CRM.{metadata_subtype}"
        "?$select=LogicalName&$expand=OptionSet($select=Options)"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return parse_optionset_payload(resp.json())


def sync_dataverse_picklists_to_snowflake(
    settings: Lab18Settings,
    token: str,
    setup_sql_path: Path,
) -> PicklistSyncResult:
    """Provision Lab 18 lookup tables and MERGE live Dataverse OptionSets into Snowflake."""
    from src.snowflake_semantic import connect_snowflake_admin, execute_sql_script

    execute_sql_script(settings, setup_sql_path)

    categories = fetch_dataverse_optionset(
        base_api=settings.dataverse_base_api,
        token=token,
        entity_name="account",
        attribute_name="accountcategorycode",
        metadata_subtype="PicklistAttributeMetadata",
    )
    states = fetch_dataverse_optionset(
        base_api=settings.dataverse_base_api,
        token=token,
        entity_name="account",
        attribute_name="statecode",
        metadata_subtype="StateAttributeMetadata",
    )

    conn = connect_snowflake_admin(settings)
    try:
        cur = conn.cursor()
        try:
            for entry in categories:
                cur.execute(MERGE_CATEGORY_SQL, (entry.value, entry.label))
            for entry in states:
                cur.execute(MERGE_STATE_SQL, (entry.value, entry.label))
        finally:
            cur.close()
    finally:
        conn.close()

    return PicklistSyncResult(categories=categories, states=states)
