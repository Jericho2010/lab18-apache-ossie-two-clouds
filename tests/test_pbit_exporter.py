"""Tests for Power BI .pbit ZIP/DataModelSchema decoding and inspection."""

import base64
import io
import json
import zipfile
from pathlib import Path

from src.pbit_exporter import _decode_snowflake_pbit_payload, inspect_pbit_archive


def _build_synthetic_pbit_zip() -> bytes:
    """Create a synthetic UTF-16-LE DataModelSchema inside a ZIP matching Snowflake export."""
    schema_obj = {
        "name": "CRM_ACCOUNT_SEMANTIC_VIEW",
        "model": {
            "tables": [
                {
                    "name": "RAW_ACCOUNTS",
                    "columns": [{"name": "ACCOUNTID"}, {"name": "REVENUE"}],
                    "measures": [],
                },
                {
                    "name": "DIM_ACCOUNT_CATEGORY",
                    "columns": [{"name": "CATEGORY_CODE"}, {"name": "CATEGORY_LABEL"}],
                    "measures": [],
                },
                {
                    "name": "Derived Metrics",
                    "columns": [{"name": "_placeholder"}],
                    "measures": [
                        {"name": "TOTAL_REVENUE", "expression": "SUM(RAW_ACCOUNTS[REVENUE])"}
                    ],
                },
            ],
            "relationships": [
                {
                    "fromTable": "RAW_ACCOUNTS",
                    "fromColumn": "ACCOUNTCATEGORYCODE",
                    "toTable": "DIM_ACCOUNT_CATEGORY",
                    "toColumn": "CATEGORY_CODE",
                }
            ],
        },
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("DataModelSchema", json.dumps(schema_obj).encode("utf-16-le"))
        zf.writestr("Version", b"1.0")
    return buf.getvalue()


def test_decode_and_inspect_utf16le_pbit_archive(tmp_path: Path) -> None:
    """inspect_pbit_archive extracts tables, DAX measures, and relationships from .pbit ZIP."""
    zip_bytes = _build_synthetic_pbit_zip()
    b64_payload = base64.b64encode(zip_bytes).decode("ascii")

    decoded, fmt = _decode_snowflake_pbit_payload(b64_payload)
    assert fmt == "base64_zip"

    pbit_path = tmp_path / "model.pbit"
    pbit_path.write_bytes(decoded)

    result = inspect_pbit_archive(pbit_path, payload_format=fmt)
    assert "DataModelSchema" in result.zip_entries
    assert set(result.tables.keys()) == {"RAW_ACCOUNTS", "DIM_ACCOUNT_CATEGORY", "Derived Metrics"}
    assert len(result.measures) == 1
    assert result.measures[0].name == "TOTAL_REVENUE"
    assert result.measures[0].expression == "SUM(RAW_ACCOUNTS[REVENUE])"
    assert len(result.relationships) == 1
    assert result.relationships[0].to_table == "DIM_ACCOUNT_CATEGORY"
