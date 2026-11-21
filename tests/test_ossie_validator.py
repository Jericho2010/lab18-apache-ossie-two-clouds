"""Tests for Apache Ossie YAML validation and Snowflake round-trip diffing."""

from pathlib import Path

from src.config import OSSIE_SCHEMA_PATH, OSSIE_YAML_PATH
from src.ossie_validator import (
    compare_roundtrip_yaml,
    load_ossie_yaml,
    validate_ossie_yaml,
)


def test_canonical_ossie_yaml_passes_both_snowflake_and_core_schema() -> None:
    """semantic/crm_account_model.ossie.yaml validates cleanly with delta notes."""
    res = validate_ossie_yaml(OSSIE_YAML_PATH, OSSIE_SCHEMA_PATH)
    assert res.valid, res.errors
    assert res.snowflake_version == "0.1.1"
    assert res.core_schema_version == "0.3.0.dev0"
    assert res.datasets_count == 3
    assert res.relationships_count == 2
    assert res.metrics_count == 3
    assert len(res.dialect_delta_notes) >= 2


def test_validator_rejects_object_ai_context_for_snowflake_011(tmp_path: Path) -> None:
    """Object ai_context is flagged because Snowflake 0.1.1 only accepts string ai_context."""
    bad_yaml = tmp_path / "bad.ossie.yaml"
    bad_yaml.write_text(
        """version: "0.1.1"
semantic_model:
  - name: BAD_MODEL
    ai_context:
      instructions: "Do not use object in 0.1.1"
    datasets:
      - name: ds1
        source: DB.SCH.TBL
        fields:
          - name: id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: ID
""",
        encoding="utf-8",
    )
    res = validate_ossie_yaml(bad_yaml, OSSIE_SCHEMA_PATH)
    assert not res.valid
    assert any("ai_context to be a string" in err for err in res.errors)


def test_roundtrip_diff_detects_missing_metrics() -> None:
    """compare_roundtrip_yaml catches dropped metrics from Snowflake READ_OSSIE_YAML."""
    src_doc = load_ossie_yaml(OSSIE_YAML_PATH)
    incomplete_rt = """version: 0.1.1
semantic_model:
  - name: CRM_ACCOUNT_SEMANTIC_VIEW
    ai_context: "other"
    datasets:
      - name: RAW_ACCOUNTS
      - name: DIM_ACCOUNT_CATEGORY
      - name: DIM_ACCOUNT_STATE
    relationships:
      - name: ACCOUNT_TO_CATEGORY
      - name: ACCOUNT_TO_STATE
"""
    diff = compare_roundtrip_yaml(src_doc, incomplete_rt)
    assert not diff.matched
    assert "metric:TOTAL_REVENUE" in diff.missing_items
    assert "ai_context" in diff.missing_items
