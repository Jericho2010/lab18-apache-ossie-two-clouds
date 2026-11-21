"""Apache Ossie YAML schema validator and Snowflake round-trip diff engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from pydantic import BaseModel, Field


class OssieValidationResult(BaseModel):
    """Outcome of validating Ossie YAML against Snowflake OSI 0.1.1 and core 0.3.0.dev0."""

    valid: bool
    snowflake_version: str
    core_schema_version: str
    dialect_delta_notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    datasets_count: int = 0
    relationships_count: int = 0
    metrics_count: int = 0


class RoundTripDiffResult(BaseModel):
    """Outcome of comparing source Ossie YAML against Snowflake READ_OSSIE_YAML output."""

    matched: bool
    model_name: str
    expected_datasets: list[str]
    roundtrip_datasets: list[str]
    expected_relationships: list[str]
    roundtrip_relationships: list[str]
    expected_metrics: list[str]
    roundtrip_metrics: list[str]
    ai_context_preserved: bool
    missing_items: list[str] = Field(default_factory=list)


def load_ossie_yaml(yaml_path: Path) -> dict[str, Any]:
    """Load an Apache Ossie YAML document from disk."""
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected YAML mapping at root of {yaml_path}")
    return parsed


def extract_model_block(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the semantic model mapping from either 0.1.1 wrapper or 0.3.0 flat shape."""
    if "semantic_model" in doc:
        models = doc["semantic_model"]
        if not isinstance(models, list) or not models:
            raise ValueError("semantic_model must be a non-empty list")
        return dict(models[0])
    return dict(doc)


def transpile_snowflake_011_to_core_030(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize Snowflake OSI 0.1.1 YAML to core Apache Ossie 0.3.0.dev0 JSON Schema shape."""
    notes: list[str] = []
    src_version = str(doc.get("version", ""))
    model = extract_model_block(doc)

    if src_version != "0.3.0.dev0":
        notes.append(
            f"version='{src_version}' normalized to '0.3.0.dev0' "
            "(Snowflake 10.42.103 OSI parser strictly requires version '0.1.1')"
        )
    if "semantic_model" in doc:
        notes.append(
            "Unwrapped 'semantic_model[0]' wrapper used by Snowflake OSI 0.1.1 "
            "to top-level SemanticModel keys in ossie-schema.json 0.3.0.dev0"
        )
    if isinstance(model.get("ai_context"), str):
        notes.append(
            "ai_context is a plain string (Snowflake OSI 0.1.1 rejects object ai_context; "
            "string is valid under ossie-schema.json oneOf[string, object])"
        )

    normalized: dict[str, Any] = {
        "version": "0.3.0.dev0",
        "name": model.get("name", ""),
        "datasets": _normalize_datasets(model.get("datasets", []), notes),
    }
    for opt_key in ("description", "ai_context", "relationships", "metrics"):
        if opt_key in model:
            normalized[opt_key] = model[opt_key]
    return normalized, notes


def _normalize_datasets(
    datasets: list[dict[str, Any]],
    notes: list[str],
) -> list[dict[str, Any]]:
    """Map Snowflake custom_extensions -> nonportable_extensions for 0.3.0.dev0 validation."""
    out: list[dict[str, Any]] = []
    for ds in datasets:
        ds_copy = dict(ds)
        if "custom_extensions" in ds_copy:
            ds_copy["nonportable_extensions"] = ds_copy.pop("custom_extensions")
            notes.append("Mapped custom_extensions -> nonportable_extensions")
        fields_out: list[dict[str, Any]] = []
        for fld in ds_copy.get("fields", []):
            fld_copy = dict(fld)
            if "custom_extensions" in fld_copy:
                fld_copy["nonportable_extensions"] = fld_copy.pop("custom_extensions")
            fields_out.append(fld_copy)
        if fields_out:
            ds_copy["fields"] = fields_out
        out.append(ds_copy)
    return out


def validate_ossie_yaml(
    yaml_path: Path,
    schema_path: Path,
) -> OssieValidationResult:
    """Validate Ossie YAML against Snowflake 0.1.1 rules and official ossie-schema.json."""
    doc = load_ossie_yaml(yaml_path)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    sf_version = str(doc.get("version", ""))
    if sf_version != "0.1.1":
        errors.append(
            f"Snowflake 10.42.103 requires version='0.1.1', found '{sf_version}'."
        )

    model = extract_model_block(doc)
    if "ai_context" in model and not isinstance(model["ai_context"], str):
        errors.append("Snowflake OSI 0.1.1 requires ai_context to be a string.")

    core_doc, delta_notes = transpile_snowflake_011_to_core_030(doc)
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(core_doc), key=lambda e: list(e.path)):
        loc = ".".join(str(p) for p in err.path) or "<root>"
        errors.append(f"ossie-schema.json [{loc}]: {err.message}")

    return OssieValidationResult(
        valid=(len(errors) == 0),
        snowflake_version=sf_version,
        core_schema_version=str(schema.get("properties", {}).get("version", {}).get("const", "")),
        dialect_delta_notes=delta_notes,
        errors=errors,
        datasets_count=len(model.get("datasets", [])),
        relationships_count=len(model.get("relationships", [])),
        metrics_count=len(model.get("metrics", [])),
    )


def compare_roundtrip_yaml(
    source_doc: dict[str, Any],
    roundtrip_yaml_text: str,
) -> RoundTripDiffResult:
    """Compare source Ossie YAML against Snowflake SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW."""
    src_model = extract_model_block(source_doc)
    rt_doc = yaml.safe_load(roundtrip_yaml_text)
    if not isinstance(rt_doc, dict):
        raise ValueError("Round-trip YAML returned by Snowflake is not a valid mapping")
    rt_model = extract_model_block(rt_doc)

    expected_datasets = sorted(str(d["name"]).upper() for d in src_model.get("datasets", []))
    rt_datasets = sorted(str(d["name"]).upper() for d in rt_model.get("datasets", []))

    expected_rels = sorted(str(r["name"]).upper() for r in src_model.get("relationships", []))
    rt_rels = sorted(str(r["name"]).upper() for r in rt_model.get("relationships", []))

    expected_metrics = sorted(str(m["name"]).upper() for m in src_model.get("metrics", []))
    rt_metrics = sorted(str(m["name"]).upper() for m in rt_model.get("metrics", []))

    src_ai = str(src_model.get("ai_context", "")).strip()
    rt_ai = str(rt_model.get("ai_context", "")).strip()
    ai_ok = bool(src_ai) and (src_ai == rt_ai)

    missing: list[str] = []
    for ds in expected_datasets:
        if ds not in rt_datasets:
            missing.append(f"dataset:{ds}")
    for rel in expected_rels:
        if rel not in rt_rels:
            missing.append(f"relationship:{rel}")
    for met in expected_metrics:
        if met not in rt_metrics:
            missing.append(f"metric:{met}")
    if not ai_ok:
        missing.append("ai_context")

    return RoundTripDiffResult(
        matched=(len(missing) == 0),
        model_name=str(rt_model.get("name", "")),
        expected_datasets=expected_datasets,
        roundtrip_datasets=rt_datasets,
        expected_relationships=expected_rels,
        roundtrip_relationships=rt_rels,
        expected_metrics=expected_metrics,
        roundtrip_metrics=rt_metrics,
        ai_context_preserved=ai_ok,
        missing_items=missing,
    )
