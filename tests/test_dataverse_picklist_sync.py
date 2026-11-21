"""Tests for Dataverse OptionSet parsing and Snowflake MERGE lookup SQL."""

from src.dataverse_picklist_sync import (
    MERGE_CATEGORY_SQL,
    MERGE_STATE_SQL,
    parse_optionset_payload,
)


def test_parse_optionset_payload_extracts_and_sorts_labels() -> None:
    """Dataverse Picklist OptionSet metadata extracts integer values and English labels."""
    payload = {
        "LogicalName": "accountcategorycode",
        "OptionSet": {
            "Options": [
                {"Value": 2, "Label": {"UserLocalizedLabel": {"Label": "Standard"}}},
                {"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Preferred Customer"}}},
            ]
        },
    }
    entries = parse_optionset_payload(payload)
    assert [(e.value, e.label) for e in entries] == [
        (1, "Preferred Customer"),
        (2, "Standard"),
    ]


def test_merge_lookup_sql_statements_target_lab18_schema() -> None:
    """MERGE SQL statements target CRM_OSSIE_LAB18.SEMANTIC lookup tables."""
    assert "MERGE INTO CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_CATEGORY" in MERGE_CATEGORY_SQL
    assert "MERGE INTO CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_STATE" in MERGE_STATE_SQL
