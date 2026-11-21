"""CLI entrypoint for Lab 18: One Semantic YAML, Multiple Cloud Platforms (Apache Ossie)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer

from src.config import (
    DATA_DIR,
    ENV_FILE_PATH,
    OSSIE_SCHEMA_PATH,
    OSSIE_YAML_PATH,
    PBIT_OUTPUT_PATH,
    ROUNDTRIP_YAML_PATH,
    SNOWFLAKE_SQL_DIR,
    Lab18Settings,
)
from src.dataverse_picklist_sync import (
    PicklistSyncResult,
    get_az_access_token,
    sync_dataverse_picklists_to_snowflake,
)
from src.mcp_analyst_client import (
    McpVerificationResult,
    RestSqlExecutionResult,
    execute_semantic_sql_via_rest,
    run_mcp_analyst_verification,
)
from src.ossie_validator import (
    OssieValidationResult,
    RoundTripDiffResult,
    compare_roundtrip_yaml,
    load_ossie_yaml,
    validate_ossie_yaml,
)
from src.pbit_exporter import PbitInspectionResult, export_pbit_from_snowflake
from src.snowflake_semantic import (
    GroundTruthSummary,
    compile_ossie_semantic_view,
    execute_sql_script,
    issue_scoped_mcp_pat,
    query_direct_sql_ground_truth,
    read_ossie_roundtrip_yaml,
)

app = typer.Typer(
    name="ossie-lab18",
    help="Apache Ossie -> Snowflake Semantic View / HTTP MCP + Power BI .pbit",
    add_completion=False,
)

VERIFICATION_REPORT_PATH = DATA_DIR / "verification_report.json"

EMPIRICAL_ROUGH_EDGES = [
    (
        "Upstream apache/ossie core-spec (0.3.0.dev0) vs Snowflake 10.42.103 OSI preview (0.1.1): "
        "Snowflake requires version '0.1.1', semantic_model[0] wrapper, plain-string ai_context "
        "(rejects object ai_context), and expression.dialects (ANSI_SQL)."
    ),
    (
        "Conditional SQL CASE WHEN metrics export to preview .pbit as SUM(SWITCH(TRUE(), ...)) "
        "rather than SUMX(...), so star-schema base aggregates (TOTAL_REVENUE, AVERAGE_REVENUE, "
        "ACCOUNT_COUNT) sliced across Dataverse lookup dimensions stay clean across both platforms."
    ),
]


def _step_validate_ossie() -> OssieValidationResult:
    """Validate Ossie YAML against Snowflake 0.1.1 and official ossie-schema.json."""
    res = validate_ossie_yaml(OSSIE_YAML_PATH, OSSIE_SCHEMA_PATH)
    typer.echo(
        f"[ossie] Valid={res.valid} | SnowflakeVer={res.snowflake_version} | "
        f"CoreSchema={res.core_schema_version} | Datasets={res.datasets_count} | "
        f"Rels={res.relationships_count} | Metrics={res.metrics_count}"
    )
    for note in res.dialect_delta_notes:
        typer.echo(f"  [delta] {note}")
    for err in res.errors:
        typer.echo(f"  [error] {err}", err=True)
    return res


def _step_sync_picklists(settings: Lab18Settings) -> PicklistSyncResult:
    """Sync live Dataverse OptionSet labels into Snowflake lookup tables."""
    token = get_az_access_token(settings.dataverse_url)
    res = sync_dataverse_picklists_to_snowflake(
        settings=settings,
        token=token,
        setup_sql_path=SNOWFLAKE_SQL_DIR / "01_setup_ossie_database.sql",
    )
    typer.echo(
        f"[dataverse] Synced {len(res.categories)} category labels "
        f"({[(c.value, c.label) for c in res.categories]}) and "
        f"{len(res.states)} state labels ({[(s.value, s.label) for s in res.states]})"
    )
    return res


def _step_compile_and_roundtrip(settings: Lab18Settings) -> RoundTripDiffResult:
    """Compile Ossie YAML to Snowflake Semantic View and verify round-trip YAML."""
    msg = compile_ossie_semantic_view(settings, OSSIE_YAML_PATH)
    typer.echo(f"[snowflake] {msg}")
    rt_yaml = read_ossie_roundtrip_yaml(settings, ROUNDTRIP_YAML_PATH)
    diff = compare_roundtrip_yaml(load_ossie_yaml(OSSIE_YAML_PATH), rt_yaml)
    typer.echo(
        f"[roundtrip] Matched={diff.matched} | Model={diff.model_name} | "
        f"Datasets={diff.roundtrip_datasets} | Rels={diff.roundtrip_relationships} | "
        f"Metrics={diff.roundtrip_metrics} | AIContext={diff.ai_context_preserved}"
    )
    if diff.missing_items:
        typer.echo(f"  [missing] {diff.missing_items}", err=True)
    return diff


def _step_export_pbit(settings: Lab18Settings) -> PbitInspectionResult:
    """Export Power BI .pbit from Snowflake Semantic View and inspect DataModelSchema."""
    res = export_pbit_from_snowflake(settings, PBIT_OUTPUT_PATH)
    typer.echo(
        f"[pbit] Saved {res.pbit_path} ({res.byte_size} bytes, fmt={res.payload_format})"
    )
    typer.echo(f"  [pbit] ZIP entries: {res.zip_entries}")
    typer.echo(f"  [pbit] Tables     : { {k: len(v) for k, v in res.tables.items()} }")
    typer.echo(
        f"  [pbit] Measures   : {[(m.name, m.expression) for m in res.measures]}"
    )
    typer.echo(
        f"  [pbit] Rels       : "
        f"{[(r.from_table, r.from_column, r.to_table, r.to_column) for r in res.relationships]}"
    )
    return res


def _step_provision_mcp(settings: Lab18Settings) -> Lab18Settings:
    """Provision Snowflake MCP server, ROLE_LAB18_MCP, SVC_LAB18_MCP, IP policy, and PAT."""
    stmt_count = execute_sql_script(
        settings, SNOWFLAKE_SQL_DIR / "03_mcp_server_and_service_user.sql"
    )
    typer.echo(f"[mcp-setup] Executed 03_mcp_server_and_service_user.sql ({stmt_count} statements)")
    issue_scoped_mcp_pat(settings, ENV_FILE_PATH)
    typer.echo(f"[mcp-setup] Issued scoped PAT for SVC_LAB18_MCP -> stored in {ENV_FILE_PATH.name}")
    return Lab18Settings()


def _step_query_mcp(
    settings: Lab18Settings,
) -> tuple[GroundTruthSummary, McpVerificationResult, RestSqlExecutionResult, bool]:
    """Query CRM_OSSIE_MCP_SERVER, execute generated SQL via REST, and verify parity."""
    truth = query_direct_sql_ground_truth(settings)
    typer.echo(
        f"[sql-truth] Total={truth.total_accounts} accts (${truth.total_revenue:,.2f}) | "
        f"Preferred+Active={truth.preferred_active_accounts} accts "
        f"(${truth.preferred_active_revenue:,.2f})"
    )

    mcp_res = run_mcp_analyst_verification(settings)
    typer.echo(
        f"[mcp-http] initialize={mcp_res.initialize_status} | "
        f"tools/list={mcp_res.tools_list_status} | "
        f"tools/call={mcp_res.tools_call_status}"
    )
    typer.echo(f"[mcp-http] Tools: {[t.name for t in mcp_res.tools]}")
    typer.echo(f"[mcp-http] Generated SQL:\n{mcp_res.generated_sql}")

    rest_res = execute_semantic_sql_via_rest(settings, mcp_res.generated_sql)
    parity_ok = rest_res.metric_value == truth.preferred_active_revenue
    typer.echo(
        f"[rest-sql] /api/v2/statements HTTP={rest_res.http_status} | "
        f"Role=ROLE_LAB18_MCP | Result=${rest_res.metric_value:,.2f}"
    )
    typer.echo(
        f"[parity] Ground-truth ${truth.preferred_active_revenue:,.2f} == "
        f"Executed ${rest_res.metric_value:,.2f}: {parity_ok}"
    )
    return truth, mcp_res, rest_res, parity_ok


def build_verification_report(
    settings: Lab18Settings,
    ossie_res: OssieValidationResult,
    picklist_res: PicklistSyncResult,
    roundtrip_res: RoundTripDiffResult,
    pbit_res: PbitInspectionResult,
    truth: GroundTruthSummary,
    mcp_res: McpVerificationResult,
    rest_res: RestSqlExecutionResult,
    parity_ok: bool,
) -> dict[str, Any]:
    """Assemble the complete sanitized two-platform verification ledger (zero secrets)."""
    return {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "ossie_validation": {
            "snowflake_version": ossie_res.snowflake_version,
            "core_schema_version": ossie_res.core_schema_version,
            "dialect_delta_notes": ossie_res.dialect_delta_notes,
            "datasets_count": ossie_res.datasets_count,
            "relationships_count": ossie_res.relationships_count,
            "metrics_count": ossie_res.metrics_count,
        },
        "dataverse_picklists": {
            "categories": [c.model_dump() for c in picklist_res.categories],
            "states": [s.model_dump() for s in picklist_res.states],
        },
        "roundtrip_fidelity": {
            "matched": roundtrip_res.matched,
            "model_name": roundtrip_res.model_name,
            "datasets": roundtrip_res.roundtrip_datasets,
            "relationships": roundtrip_res.roundtrip_relationships,
            "metrics": roundtrip_res.roundtrip_metrics,
            "ai_context_preserved": roundtrip_res.ai_context_preserved,
        },
        "snowflake_mcp": {
            "semantic_view": settings.semantic_view_fqn,
            "endpoint": settings.snowflake_mcp_endpoint,
            "tools": [t.name for t in mcp_res.tools],
            "mcp_http_statuses": {
                "initialize": mcp_res.initialize_status,
                "tools_list": mcp_res.tools_list_status,
                "tools_call": mcp_res.tools_call_status,
            },
            "statements_api_status": rest_res.http_status,
            "statements_handle": rest_res.statement_handle,
            "question": mcp_res.question,
            "generated_sql": mcp_res.generated_sql,
            "ground_truth_total_accounts": truth.total_accounts,
            "ground_truth_total_revenue": str(truth.total_revenue),
            "ground_truth_preferred_active_accounts": truth.preferred_active_accounts,
            "ground_truth_preferred_active_revenue": str(truth.preferred_active_revenue),
            "executed_metric_value": str(rest_res.metric_value),
            "parity_matched": parity_ok,
        },
        "powerbi_pbit": {
            "pbit_path": pbit_res.pbit_path,
            "byte_size": pbit_res.byte_size,
            "payload_format": pbit_res.payload_format,
            "zip_entries": pbit_res.zip_entries,
            "tables": pbit_res.tables,
            "measures": [m.model_dump() for m in pbit_res.measures],
            "relationships": [r.model_dump() for r in pbit_res.relationships],
        },
        "empirical_rough_edges": list(EMPIRICAL_ROUGH_EDGES),
    }


def format_two_cloud_summary(report: dict[str, Any]) -> str:
    """Format a 5-second side-by-side two-platform proof summary from the verification ledger."""
    ossie = report["ossie_validation"]
    dv = report["dataverse_picklists"]
    rt = report["roundtrip_fidelity"]
    sf = report["snowflake_mcp"]
    pbi = report["powerbi_pbit"]
    edges = report["empirical_rough_edges"]

    exec_val = Decimal(str(sf["executed_metric_value"]))
    total_val = Decimal(str(sf["ground_truth_total_revenue"]))
    statuses = sf["mcp_http_statuses"]
    measure_names = [m["name"] for m in pbi["measures"]]
    rel_labels = [
        f"{r['from_table']}.{r['from_column']}->{r['to_table']}.{r['to_column']}"
        for r in pbi["relationships"]
    ]

    lines = [
        "",
        "=============== LAB 18: ONE SEMANTIC YAML, TWO PLATFORMS ===============",
        (
            f"Source Spec : semantic/crm_account_model.ossie.yaml "
            f"(OSI {ossie['snowflake_version']} -> core {ossie['core_schema_version']})"
        ),
        (
            f"Model Scope : {ossie['datasets_count']} datasets | "
            f"{ossie['relationships_count']} relationships | {ossie['metrics_count']} metrics"
        ),
        (
            f"Dataverse   : {len(dv['categories'])} category labels + "
            f"{len(dv['states'])} state labels synced live"
        ),
        "------------------------------------------------------------------------",
        "PLATFORM 1 | Snowflake Semantic View + Managed HTTP MCP",
        f"  View      : {sf['semantic_view']} (round-trip matched={rt['matched']})",
        (
            f"  MCP HTTP  : init={statuses['initialize']} list={statuses['tools_list']} "
            f"call={statuses['tools_call']} | /api/v2/statements={sf['statements_api_status']}"
        ),
        (
            f"  Result    : ${exec_val:,.2f} Preferred+Active "
            f"({sf['ground_truth_preferred_active_accounts']} accts) | "
            f"Total=${total_val:,.2f} ({sf['ground_truth_total_accounts']} accts) | "
            f"Parity={sf['parity_matched']}"
        ),
        "------------------------------------------------------------------------",
        "PLATFORM 2 | Power BI .pbit Archive Export",
        (
            f"  Artifact  : {Path(pbi['pbit_path']).name} "
            f"({pbi['byte_size']} bytes, {pbi['payload_format']})"
        ),
        (
            f"  Model     : {len(pbi['tables'])} DataModelSchema tables | "
            f"{len(pbi['measures'])} DAX measures {measure_names}"
        ),
        f"  Joins     : {len(pbi['relationships'])} relationships {rel_labels}",
        "------------------------------------------------------------------------",
        "EMPIRICAL ROUGH EDGES OBSERVED LIVE:",
    ]
    for idx, edge in enumerate(edges, start=1):
        lines.append(f"  {idx}. {edge}")
    lines.append("========================================================================")
    return "\n".join(lines)


def write_verification_report(
    report: dict[str, Any], out_path: Path | None = None
) -> None:
    """Persist sanitized verification ledger (zero secrets) to disk."""
    target = out_path or VERIFICATION_REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"[report] Wrote sanitized verification report -> {target}")


def _run_summary(report_path: Path | None = None) -> int:
    """Load data/verification_report.json and display the two-platform summary."""
    target = report_path or VERIFICATION_REPORT_PATH
    if not target.exists():
        typer.echo(
            f"[error] Verification report not found at {target}. "
            "Run `./run.sh --verify` first.",
            err=True,
        )
        return 1
    report = json.loads(target.read_text(encoding="utf-8"))
    if "ossie_validation" not in report:
        typer.echo(
            f"[error] {target} uses an outdated schema. Run `./run.sh --verify` to refresh it.",
            err=True,
        )
        return 1
    typer.echo(format_two_cloud_summary(report))
    return 0


@app.callback(invoke_without_command=True)
def main(
    validate_ossie: bool = typer.Option(False, "--validate-ossie"),
    sync_picklists: bool = typer.Option(False, "--sync-picklists"),
    compile_view: bool = typer.Option(False, "--compile"),
    export_pbit: bool = typer.Option(False, "--export-pbit"),
    provision_mcp: bool = typer.Option(False, "--provision-mcp"),
    query_mcp: bool = typer.Option(False, "--query-mcp"),
    verify: bool = typer.Option(False, "--verify"),
    summary: bool = typer.Option(
        False, "--summary", help="Print two-platform summary from data/verification_report.json."
    ),
) -> None:
    """Lab 18 CLI orchestrator."""
    settings = Lab18Settings()

    if summary:
        raise typer.Exit(code=_run_summary())
    if validate_ossie:
        raise typer.Exit(code=0 if _step_validate_ossie().valid else 1)
    if sync_picklists:
        _step_sync_picklists(settings)
        raise typer.Exit(code=0)
    if compile_view:
        raise typer.Exit(code=0 if _step_compile_and_roundtrip(settings).matched else 1)
    if export_pbit:
        pbit_res = _step_export_pbit(settings)
        ok = len(pbit_res.measures) >= 3 and len(pbit_res.relationships) >= 2
        raise typer.Exit(code=0 if ok else 1)
    if provision_mcp:
        _step_provision_mcp(settings)
        raise typer.Exit(code=0)
    if query_mcp:
        _, mcp_res, rest_res, parity_ok = _step_query_mcp(settings)
        ok = (
            mcp_res.initialize_status == 200
            and mcp_res.tools_list_status == 200
            and mcp_res.tools_call_status == 200
            and rest_res.http_status == 200
            and parity_ok
        )
        raise typer.Exit(code=0 if ok else 1)
    if verify:
        typer.echo("=== Lab 18 Full End-to-End Verification ===")
        ossie_res = _step_validate_ossie()
        if not ossie_res.valid:
            raise typer.Exit(code=1)
        picklist_res = _step_sync_picklists(settings)
        roundtrip_res = _step_compile_and_roundtrip(settings)
        if not roundtrip_res.matched:
            raise typer.Exit(code=1)
        pbit_res = _step_export_pbit(settings)
        if not (len(pbit_res.measures) >= 3 and len(pbit_res.relationships) >= 2):
            raise typer.Exit(code=1)
        if not settings.snowflake_mcp_pat:
            settings = _step_provision_mcp(settings)
        truth, mcp_res, rest_res, parity_ok = _step_query_mcp(settings)
        if not (
            mcp_res.initialize_status == 200
            and mcp_res.tools_list_status == 200
            and mcp_res.tools_call_status == 200
            and rest_res.http_status == 200
            and parity_ok
        ):
            raise typer.Exit(code=1)

        report = build_verification_report(
            settings=settings,
            ossie_res=ossie_res,
            picklist_res=picklist_res,
            roundtrip_res=roundtrip_res,
            pbit_res=pbit_res,
            truth=truth,
            mcp_res=mcp_res,
            rest_res=rest_res,
            parity_ok=parity_ok,
        )
        write_verification_report(report)
        typer.echo(format_two_cloud_summary(report))
        typer.echo("\n[verify] ALL LAB 18 GATES PASSED.")
        raise typer.Exit(code=0)

    typer.echo(
        "Specify one of: --validate-ossie, --sync-picklists, --compile, "
        "--export-pbit, --provision-mcp, --query-mcp, --verify, --summary"
    )
    raise typer.Exit(code=2)


if __name__ == "__main__":
    sys.exit(app())
