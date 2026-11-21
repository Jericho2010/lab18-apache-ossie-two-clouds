"""Tests for enriched two-platform verification report schema and --summary CLI output."""

import json
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from src.config import Lab18Settings
from src.dataverse_picklist_sync import PicklistOptionEntry, PicklistSyncResult
from src.main import app, build_verification_report, format_two_cloud_summary
from src.mcp_analyst_client import (
    McpToolSummary,
    McpVerificationResult,
    RestSqlExecutionResult,
)
from src.ossie_validator import OssieValidationResult, RoundTripDiffResult
from src.pbit_exporter import PbitInspectionResult, PbitMeasure, PbitRelationship
from src.snowflake_semantic import GroundTruthSummary


def _sample_report() -> dict[str, object]:
    """Build a representative sanitized two-cloud verification ledger."""
    settings = Lab18Settings()
    ossie_res = OssieValidationResult(
        valid=True,
        snowflake_version="0.1.1",
        core_schema_version="0.3.0.dev0",
        dialect_delta_notes=["version='0.1.1' normalized to '0.3.0.dev0'"],
        datasets_count=3,
        relationships_count=2,
        metrics_count=3,
    )
    picklist_res = PicklistSyncResult(
        categories=[
            PicklistOptionEntry(value=1, label="Preferred Customer"),
            PicklistOptionEntry(value=2, label="Standard"),
        ],
        states=[
            PicklistOptionEntry(value=0, label="Active"),
            PicklistOptionEntry(value=1, label="Inactive"),
        ],
    )
    roundtrip_res = RoundTripDiffResult(
        matched=True,
        model_name="CRM_ACCOUNT_SEMANTIC_VIEW",
        expected_datasets=["DIM_ACCOUNT_CATEGORY", "DIM_ACCOUNT_STATE", "RAW_ACCOUNTS"],
        roundtrip_datasets=["DIM_ACCOUNT_CATEGORY", "DIM_ACCOUNT_STATE", "RAW_ACCOUNTS"],
        expected_relationships=["ACCOUNT_TO_CATEGORY", "ACCOUNT_TO_STATE"],
        roundtrip_relationships=["ACCOUNT_TO_CATEGORY", "ACCOUNT_TO_STATE"],
        expected_metrics=["ACCOUNT_COUNT", "AVERAGE_REVENUE", "TOTAL_REVENUE"],
        roundtrip_metrics=["ACCOUNT_COUNT", "AVERAGE_REVENUE", "TOTAL_REVENUE"],
        ai_context_preserved=True,
    )
    pbit_res = PbitInspectionResult(
        pbit_path="data/crm_account_model.pbit",
        payload_format="base64_zip",
        byte_size=5891,
        zip_entries=["DataModelSchema", "Version"],
        tables={
            "RAW_ACCOUNTS": ["ACCOUNTID", "REVENUE"],
            "DIM_ACCOUNT_CATEGORY": ["CATEGORY_CODE", "CATEGORY_LABEL"],
            "DIM_ACCOUNT_STATE": ["STATE_CODE", "STATE_LABEL"],
            "Derived Metrics": ["_placeholder"],
        },
        measures=[
            PbitMeasure(table="Derived Metrics", name="TOTAL_REVENUE", expression="SUM(...)"),
            PbitMeasure(table="Derived Metrics", name="AVERAGE_REVENUE", expression="AVERAGE(...)"),
            PbitMeasure(table="Derived Metrics", name="ACCOUNT_COUNT", expression="COUNT(...)"),
        ],
        relationships=[
            PbitRelationship(
                from_table="RAW_ACCOUNTS",
                from_column="ACCOUNTCATEGORYCODE",
                to_table="DIM_ACCOUNT_CATEGORY",
                to_column="CATEGORY_CODE",
            ),
            PbitRelationship(
                from_table="RAW_ACCOUNTS",
                from_column="STATECODE",
                to_table="DIM_ACCOUNT_STATE",
                to_column="STATE_CODE",
            ),
        ],
    )
    truth = GroundTruthSummary(
        total_accounts=5,
        total_revenue=Decimal("6390000.00"),
        preferred_active_accounts=3,
        preferred_active_revenue=Decimal("4990000.00"),
    )
    mcp_res = McpVerificationResult(
        endpoint=settings.snowflake_mcp_endpoint,
        initialize_status=200,
        tools_list_status=200,
        tools_call_status=200,
        tools=[McpToolSummary(name="crm_account_analyst")],
        question="What is our total Preferred Customer revenue across active accounts?",
        raw_answer_text="[]",
        generated_sql="SELECT * FROM SEMANTIC_VIEW(...)",
    )
    rest_res = RestSqlExecutionResult(
        http_status=200,
        statement_handle="handle-123",
        metric_value=Decimal("4990000.00"),
    )
    return build_verification_report(
        settings=settings,
        ossie_res=ossie_res,
        picklist_res=picklist_res,
        roundtrip_res=roundtrip_res,
        pbit_res=pbit_res,
        truth=truth,
        mcp_res=mcp_res,
        rest_res=rest_res,
        parity_ok=True,
    )


def test_enriched_verification_report_contains_all_two_cloud_sections() -> None:
    """Enriched ledger contains all required sections and zero sensitive keys."""
    report = _sample_report()
    expected_sections = {
        "verified_at_utc",
        "ossie_validation",
        "dataverse_picklists",
        "roundtrip_fidelity",
        "snowflake_mcp",
        "powerbi_pbit",
        "empirical_rough_edges",
    }
    assert expected_sections.issubset(report.keys())
    assert len(report["empirical_rough_edges"]) == 2
    serialized = json.dumps(report).lower()
    assert "private_key" not in serialized
    assert "snowflake_mcp_pat" not in serialized


def test_format_two_cloud_summary_renders_both_clouds_and_rough_edges() -> None:
    """Summary text shows Snowflake MCP parity, Power BI .pbit schema, and 2 rough edges."""
    rendered = format_two_cloud_summary(_sample_report())
    assert "ONE SEMANTIC YAML, TWO PLATFORMS" in rendered
    assert "PLATFORM 1 | Snowflake Semantic View + Managed HTTP MCP" in rendered
    assert "$4,990,000.00 Preferred+Active" in rendered
    assert "Parity=True" in rendered
    assert "PLATFORM 2 | Power BI .pbit Archive Export" in rendered
    assert "4 DataModelSchema tables | 3 DAX measures" in rendered
    assert "EMPIRICAL ROUGH EDGES OBSERVED LIVE:" in rendered
    assert "0.3.0.dev0" in rendered
    assert "SUM(SWITCH(TRUE(), ...))" in rendered


def test_cli_summary_reads_verification_report_file(tmp_path: Path, monkeypatch) -> None:
    """CLI --summary prints the two-platform summary when report exists and fails if absent."""
    runner = CliRunner()
    missing_path = tmp_path / "missing.json"
    monkeypatch.setattr("src.main.VERIFICATION_REPORT_PATH", missing_path)
    missing_res = runner.invoke(app, ["--summary"])
    assert missing_res.exit_code == 1

    report_path = tmp_path / "verification_report.json"
    report_path.write_text(json.dumps(_sample_report()), encoding="utf-8")
    monkeypatch.setattr("src.main.VERIFICATION_REPORT_PATH", report_path)
    ok_res = runner.invoke(app, ["--summary"])
    assert ok_res.exit_code == 0
    assert "ONE SEMANTIC YAML, TWO PLATFORMS" in ok_res.output
    assert "PLATFORM 1 | Snowflake Semantic View + Managed HTTP MCP" in ok_res.output
    assert "PLATFORM 2 | Power BI .pbit Archive Export" in ok_res.output
