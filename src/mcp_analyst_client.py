"""HTTPS JSON-RPC client for Snowflake-Managed MCP + REST /api/v2/statements executor."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.config import Lab18Settings

FORBIDDEN_SQL_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "CALL",
    "COPY",
    "MERGE",
)


class McpToolSummary(BaseModel):
    """Metadata for a single tool exposed by Snowflake-Managed MCP."""

    name: str
    description: str = ""
    argument_keys: list[str] = Field(default_factory=list)


class McpVerificationResult(BaseModel):
    """End-to-end HTTP JSON-RPC result from CRM_OSSIE_MCP_SERVER."""

    endpoint: str
    initialize_status: int
    tools_list_status: int
    tools_call_status: int
    tools: list[McpToolSummary] = Field(default_factory=list)
    question: str
    raw_answer_text: str
    generated_sql: str = ""


class RestSqlExecutionResult(BaseModel):
    """Result of executing Cortex Analyst's validated SQL via /api/v2/statements."""

    http_status: int
    statement_handle: str = ""
    metric_value: Decimal


def _parse_mcp_http_response(resp: httpx.Response) -> dict[str, Any]:
    """Parse JSON or SSE (`data: {...}`) MCP HTTP response payload."""
    content_type = resp.headers.get("content-type", "").lower()
    text = resp.text.strip()
    if "text/event-stream" in content_type or text.startswith("event:") or text.startswith("data:"):
        for line in reversed(text.splitlines()):
            if line.startswith("data:"):
                candidate = line[len("data:") :].strip()
                if candidate and candidate != "[DONE]":
                    return dict(json.loads(candidate))
    return dict(resp.json())


def call_mcp_jsonrpc(
    client: httpx.Client,
    endpoint: str,
    pat: str,
    method: str,
    params: dict[str, Any],
    request_id: int,
) -> tuple[int, dict[str, Any]]:
    """Send a single JSON-RPC 2.0 request to Snowflake-Managed MCP over HTTPS."""
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    resp = client.post(endpoint, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.status_code, _parse_mcp_http_response(resp)


def _extract_sql_from_parsed(parsed: Any) -> str:
    """Recursively inspect parsed Cortex Analyst JSON dicts/lists for SQL `statement`."""
    if isinstance(parsed, dict):
        if "statement" in parsed and isinstance(parsed["statement"], str):
            return parsed["statement"].strip()
        if parsed.get("type") == "sql" and isinstance(parsed.get("statement"), str):
            return str(parsed["statement"]).strip()
        for val in parsed.values():
            found = _extract_sql_from_parsed(val)
            if found:
                return found
    elif isinstance(parsed, list):
        for item in parsed:
            found = _extract_sql_from_parsed(item)
            if found:
                return found
    return ""


def _extract_analyst_text(rpc_result: dict[str, Any]) -> tuple[str, str]:
    """Extract readable answer text and SQL from MCP tools/call result (list or dict JSON)."""
    result_obj = rpc_result.get("result", rpc_result)
    content_items = result_obj.get("content", [])
    chunks: list[str] = []
    sql_found = ""
    for item in content_items:
        if isinstance(item, dict):
            text_val = str(item.get("text") or item.get("json") or item)
            chunks.append(text_val)
            if not sql_found:
                try:
                    parsed = json.loads(text_val)
                    sql_found = _extract_sql_from_parsed(parsed)
                except json.JSONDecodeError:
                    pass
    combined = "\n".join(chunks) if chunks else json.dumps(result_obj)
    if not sql_found:
        sql_match = re.search(
            r"(SELECT\b[\s\S]+?\bFROM\s+SEMANTIC_VIEW\s*\([\s\S]+?\))",
            combined,
            re.IGNORECASE,
        )
        if sql_match:
            sql_found = sql_match.group(1).strip()
    return combined, sql_found


def validate_semantic_sql(sql_text: str, expected_view_fqn: str) -> str:
    """Validate Cortex Analyst SQL is strictly a single SELECT over the allowed Semantic View."""
    cleaned = re.sub(r"--[^\n]*", "", sql_text).strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("Cortex Analyst did not return an executable SQL statement.")
    if ";" in cleaned:
        raise ValueError("Multi-statement SQL rejected by guardrail.")
    if not re.match(r"^\s*SELECT\b", cleaned, re.IGNORECASE):
        raise ValueError("Only SELECT statements are permitted by guardrail.")

    upper = cleaned.upper()
    for kw in FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise ValueError(f"Forbidden SQL keyword '{kw}' rejected by guardrail.")

    view_pattern = rf"\bSEMANTIC_VIEW\s*\(\s*{re.escape(expected_view_fqn.upper())}\b"
    if not re.search(view_pattern, upper):
        raise ValueError(
            f"SQL must query SEMANTIC_VIEW({expected_view_fqn} ...); rejected by guardrail."
        )
    return cleaned


def execute_semantic_sql_via_rest(
    settings: Lab18Settings,
    generated_sql: str,
) -> RestSqlExecutionResult:
    """Execute validated SEMANTIC_VIEW SQL via Snowflake /api/v2/statements using SVC PAT."""
    pat = settings.snowflake_mcp_pat.strip()
    if not pat:
        raise ValueError("SNOWFLAKE_MCP_PAT is empty. Run `./run.sh --provision-mcp` first.")

    safe_sql = validate_semantic_sql(generated_sql, settings.semantic_view_fqn)
    headers = {
        "Authorization": f"Bearer {pat}",
        "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "statement": safe_sql,
        "timeout": 60,
        "database": settings.snowflake_database,
        "schema": settings.snowflake_schema,
        "warehouse": settings.snowflake_warehouse,
        "role": "ROLE_LAB18_MCP",
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(settings.snowflake_statements_endpoint, headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()

    rows = payload.get("data", [])
    if not rows or not rows[0]:
        raise RuntimeError(f"Snowflake /api/v2/statements returned empty data: {payload}")
    metric_value = Decimal(str(rows[0][0]))
    return RestSqlExecutionResult(
        http_status=resp.status_code,
        statement_handle=str(payload.get("statementHandle", "")),
        metric_value=metric_value,
    )


def run_mcp_analyst_verification(
    settings: Lab18Settings,
    question: str = "What is our total Preferred Customer revenue across active accounts?",
) -> McpVerificationResult:
    """Run live initialize, tools/list, and tools/call against CRM_OSSIE_MCP_SERVER."""
    pat = settings.snowflake_mcp_pat.strip()
    if not pat:
        raise ValueError(
            "SNOWFLAKE_MCP_PAT is empty. Run `./run.sh --provision-mcp` first."
        )

    with httpx.Client(timeout=60.0) as client:
        init_status, _ = call_mcp_jsonrpc(
            client=client,
            endpoint=settings.snowflake_mcp_endpoint,
            pat=pat,
            method="initialize",
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "lab18-ossie-verifier", "version": "0.1.0"},
            },
            request_id=1,
        )

        list_status, list_body = call_mcp_jsonrpc(
            client=client,
            endpoint=settings.snowflake_mcp_endpoint,
            pat=pat,
            method="tools/list",
            params={},
            request_id=2,
        )

        raw_tools = list_body.get("result", {}).get("tools", [])
        tools: list[McpToolSummary] = []
        for t in raw_tools:
            props = t.get("inputSchema", {}).get("properties", {})
            tools.append(
                McpToolSummary(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    argument_keys=list(props.keys()),
                )
            )
        if not tools:
            raise RuntimeError("No tools returned by CRM_OSSIE_MCP_SERVER tools/list.")

        target_tool = tools[0]
        arg_key = (
            "message"
            if "message" in target_tool.argument_keys
            else (target_tool.argument_keys[0] if target_tool.argument_keys else "message")
        )

        call_status, call_body = call_mcp_jsonrpc(
            client=client,
            endpoint=settings.snowflake_mcp_endpoint,
            pat=pat,
            method="tools/call",
            params={
                "name": target_tool.name,
                "arguments": {arg_key: question},
            },
            request_id=3,
        )

    answer_text, sql_text = _extract_analyst_text(call_body)
    return McpVerificationResult(
        endpoint=settings.snowflake_mcp_endpoint,
        initialize_status=init_status,
        tools_list_status=list_status,
        tools_call_status=call_status,
        tools=tools,
        question=question,
        raw_answer_text=answer_text,
        generated_sql=sql_text,
    )
