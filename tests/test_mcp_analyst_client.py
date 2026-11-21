"""Unit tests for Snowflake-Managed MCP HTTPS JSON-RPC client and REST SQL executor."""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.config import Lab18Settings
from src.mcp_analyst_client import (
    _extract_analyst_text,
    execute_semantic_sql_via_rest,
    run_mcp_analyst_verification,
    validate_semantic_sql,
)


def test_extract_analyst_text_parses_nested_dict_content() -> None:
    """_extract_analyst_text parses Cortex Analyst dict content blocks cleanly."""
    nested = json.dumps(
        {
            "message": {
                "content": [
                    {"type": "text", "text": "Preferred Customer revenue is $4,990,000.00."},
                    {"type": "sql", "statement": "SELECT SUM(REVENUE) FROM RAW_ACCOUNTS;"},
                ]
            }
        }
    )
    text, sql = _extract_analyst_text({"result": {"content": [{"text": nested}]}})
    assert "4,990,000.00" in text
    assert sql.startswith("SELECT SUM(REVENUE)")


def test_extract_analyst_text_parses_json_list_without_semicolon() -> None:
    """_extract_analyst_text parses JSON array responses returned by Snowflake MCP."""
    list_payload = json.dumps(
        [
            {"text": "This is our interpretation of your question."},
            {
                "statement": (
                    "SELECT *\nFROM SEMANTIC_VIEW(\n"
                    "    CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW\n"
                    "    METRICS TOTAL_REVENUE\n)"
                ),
                "confidence": {},
            },
        ]
    )
    text, sql = _extract_analyst_text({"result": {"content": [{"text": list_payload}]}})
    assert "interpretation" in text
    assert "SEMANTIC_VIEW(" in sql
    assert "CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW" in sql


def test_validate_semantic_sql_allows_semantic_view_and_blocks_unsafe_sql() -> None:
    """Guardrail allows SELECT over the target Semantic View and rejects unsafe SQL."""
    view_fqn = "CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW"
    good = (
        "SELECT * FROM SEMANTIC_VIEW("
        "CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW METRICS TOTAL_REVENUE"
        ") -- comment;"
    )
    assert validate_semantic_sql(good, view_fqn).startswith("SELECT * FROM SEMANTIC_VIEW")

    for bad in (
        "DROP TABLE CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS",
        "SELECT * FROM CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS",
        "SELECT * FROM SEMANTIC_VIEW(OTHER_DB.SCH.VIEW METRICS X)",
        "SELECT 1 FROM SEMANTIC_VIEW(CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW); SELECT 2",
    ):
        with pytest.raises(ValueError, match="guardrail"):
            validate_semantic_sql(bad, view_fqn)


def test_run_mcp_analyst_verification_requires_pat() -> None:
    """Missing SNOWFLAKE_MCP_PAT raises ValueError without leaking secrets."""
    settings = Lab18Settings(snowflake_mcp_pat="")
    with pytest.raises(ValueError, match="SNOWFLAKE_MCP_PAT is empty"):
        run_mcp_analyst_verification(settings)


def test_run_mcp_analyst_and_execute_via_rest() -> None:
    """Client runs initialize, tools/list, tools/call, and /api/v2/statements cleanly."""
    init_resp = MagicMock(status_code=200, headers={"content-type": "application/json"}, text="")
    init_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"protocolVersion": "2025-03-26"},
    }

    list_resp = MagicMock(status_code=200, headers={"content-type": "application/json"}, text="")
    list_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "crm_account_analyst",
                    "description": "Cortex Analyst",
                    "inputSchema": {"properties": {"message": {"type": "string"}}},
                }
            ]
        },
    }

    analyst_array = json.dumps(
        [
            {"text": "Preferred Customer revenue for active accounts:"},
            {
                "statement": (
                    "SELECT * FROM SEMANTIC_VIEW(\n"
                    "  CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW\n"
                    "  METRICS TOTAL_REVENUE\n)"
                )
            },
        ]
    )
    call_resp = MagicMock(status_code=200, headers={"content-type": "application/json"}, text="")
    call_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {"content": [{"type": "text", "text": analyst_array}]},
    }

    rest_resp = MagicMock(status_code=200)
    rest_resp.raise_for_status = MagicMock()
    rest_resp.json.return_value = {
        "statementHandle": "stmt-handle-123",
        "data": [["4990000.00"]],
    }

    settings = Lab18Settings(snowflake_mcp_pat="dummy-pat-for-unit-test")
    with patch("src.mcp_analyst_client.httpx.Client") as mock_client_cls:
        mock_post = mock_client_cls.return_value.__enter__.return_value.post
        mock_post.side_effect = [init_resp, list_resp, call_resp, rest_resp]

        mcp_res = run_mcp_analyst_verification(settings)
        rest_res = execute_semantic_sql_via_rest(settings, mcp_res.generated_sql)

    assert mcp_res.initialize_status == 200
    assert mcp_res.tools_list_status == 200
    assert mcp_res.tools_call_status == 200
    assert rest_res.http_status == 200
    assert rest_res.metric_value == Decimal("4990000.00")
