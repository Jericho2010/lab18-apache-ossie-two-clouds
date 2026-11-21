"""Power BI .pbit exporter and programmatic DataModelSchema inspector."""

from __future__ import annotations

import base64
import binascii
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.config import Lab18Settings
from src.snowflake_semantic import connect_snowflake_admin


class PbitMeasure(BaseModel):
    """DAX measure discovered in Power BI DataModelSchema."""

    table: str
    name: str
    expression: str


class PbitRelationship(BaseModel):
    """Relationship discovered in Power BI DataModelSchema."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str


class PbitInspectionResult(BaseModel):
    """Structured inspection summary of an exported Power BI .pbit archive."""

    pbit_path: str
    payload_format: str
    byte_size: int
    zip_entries: list[str] = Field(default_factory=list)
    tables: dict[str, list[str]] = Field(default_factory=dict)
    measures: list[PbitMeasure] = Field(default_factory=list)
    relationships: list[PbitRelationship] = Field(default_factory=list)


def _decode_snowflake_pbit_payload(raw_payload: str) -> tuple[bytes, str]:
    """Decode base64 ZIP (or raw JSON) from SYSTEM$EXPORT_PBIT_FROM_SEMANTIC_VIEW."""
    stripped = raw_payload.strip()
    if stripped.startswith("{"):
        return stripped.encode("utf-8"), "raw_json"
    try:
        decoded = base64.b64decode(stripped, validate=True)
        if decoded[:2] == b"PK":
            return decoded, "base64_zip"
        return decoded, "base64_binary"
    except (binascii.Error, ValueError):
        return stripped.encode("utf-8"), "raw_text"


def _parse_data_model_schema(schema_bytes: bytes) -> dict[str, Any]:
    """Decode Power BI DataModelSchema bytes (UTF-16-LE or UTF-8) into a JSON dict."""
    for encoding in ("utf-16-le", "utf-8-sig", "utf-8"):
        try:
            text = schema_bytes.decode(encoding)
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("Unable to decode DataModelSchema as UTF-16-LE or UTF-8 JSON")


def inspect_pbit_archive(
    pbit_path: Path, payload_format: str = "base64_zip"
) -> PbitInspectionResult:
    """Inspect a saved .pbit archive and extract tables, columns, measures, and relationships."""
    raw_bytes = pbit_path.read_bytes()
    tables: dict[str, list[str]] = {}
    measures: list[PbitMeasure] = []
    relationships: list[PbitRelationship] = []
    zip_entries: list[str] = []

    if raw_bytes[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
            zip_entries = zf.namelist()
            if "DataModelSchema" not in zip_entries:
                raise ValueError("Exported .pbit ZIP archive does not contain DataModelSchema")
            schema_json = _parse_data_model_schema(zf.read("DataModelSchema"))
    else:
        schema_json = _parse_data_model_schema(raw_bytes)
        payload_format = "raw_json"

    model = schema_json.get("model", schema_json)
    for tbl in model.get("tables", []):
        tname = str(tbl.get("name", ""))
        cols = [str(c.get("name", "")) for c in tbl.get("columns", [])]
        tables[tname] = cols
        for m in tbl.get("measures", []):
            expr = m.get("expression", "")
            if isinstance(expr, list):
                expr = "\n".join(str(line) for line in expr)
            measures.append(
                PbitMeasure(
                    table=tname,
                    name=str(m.get("name", "")),
                    expression=str(expr),
                )
            )

    for rel in model.get("relationships", []):
        relationships.append(
            PbitRelationship(
                from_table=str(rel.get("fromTable", "")),
                from_column=str(rel.get("fromColumn", "")),
                to_table=str(rel.get("toTable", "")),
                to_column=str(rel.get("toColumn", "")),
            )
        )

    return PbitInspectionResult(
        pbit_path=str(pbit_path),
        payload_format=payload_format,
        byte_size=len(raw_bytes),
        zip_entries=zip_entries,
        tables=tables,
        measures=measures,
        relationships=relationships,
    )


def export_pbit_from_snowflake(
    settings: Lab18Settings,
    output_path: Path,
) -> PbitInspectionResult:
    """Execute SYSTEM$EXPORT_PBIT_FROM_SEMANTIC_VIEW, save .pbit, and verify its contents."""
    conn = connect_snowflake_admin(settings)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT SYSTEM$EXPORT_PBIT_FROM_SEMANTIC_VIEW(%s)",
                (settings.semantic_view_fqn,),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                raise RuntimeError("SYSTEM$EXPORT_PBIT_FROM_SEMANTIC_VIEW returned empty payload")
            raw_payload = str(row[0])
        finally:
            cur.close()
    finally:
        conn.close()

    decoded_bytes, payload_fmt = _decode_snowflake_pbit_payload(raw_payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(decoded_bytes)
    return inspect_pbit_archive(output_path, payload_format=payload_fmt)
