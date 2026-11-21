# Security Policy & Architecture Controls

## Reporting a Security Issue

If you discover a security concern or potential credential exposure in this repository, please report it privately via a GitHub Security Advisory rather than opening a public issue.

## Security Design in Lab 18

Lab 18 separates administrative DDL provisioning from runtime MCP / BI query execution using defense-in-depth controls:

1. **Key-Pair Auth for Admin Provisioning (`ACCOUNTADMIN` / Setup Only)**
   - DDL compilation (`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML`), `.pbit` export, and MCP provisioning authenticate with an RSA key pair whose private key (`.p8` / `.pem`) is kept outside the repository and ignored by `.gitignore`.
   - Runtime configuration lives in a `chmod 600` local `.env` file (gitignored); `.env.example` contains placeholders only.

2. **Constrained Snowflake-Managed MCP Surface (`CORTEX_ANALYST_MESSAGE` Only)**
   - [`snowflake/03_mcp_server_and_service_user.sql`](snowflake/03_mcp_server_and_service_user.sql) intentionally exposes **only** `CORTEX_ANALYST_MESSAGE` on `CRM_OSSIE_MCP_SERVER`.
   - Arbitrary SQL execution tools such as `SYSTEM_EXECUTE_SQL` are deliberately omitted from the MCP specification.

3. **Dedicated Least-Privilege Service Identity + Scoped `/32` IP Policy**
   - MCP HTTP JSON-RPC calls and Snowflake SQL REST API queries run under dedicated service user `SVC_LAB18_MCP` (`TYPE = SERVICE`) and read-only role `ROLE_LAB18_MCP`.
   - `LAB18_WORKSTATION_IP_POLICY` restricts `SVC_LAB18_MCP` exclusively to the workstation's `/32` IPv4 CIDR resolved dynamically from `SNOWFLAKE_ALLOWED_IP` or `https://api.ipify.org` during provisioning — never applied account-wide or to human administrator logins.
   - `issue_scoped_mcp_pat()` rotates a 30-day PAT bound with `ROLE_RESTRICTION = 'ROLE_LAB18_MCP'` directly into local `.env` without printing secrets to stdout/logs.

4. **Application-Layer SQL Guardrail (`validate_semantic_sql`)**
   - `src/mcp_analyst_client.py` enforces that generated SQL executed against `/api/v2/statements` is strictly a single `SELECT` querying `CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW`, rejecting multi-statement payloads and non-view/DML statements before execution.

5. **Sanitized Verification Outputs**
   - `data/verification_report.json` strips all tokens, private key paths, and passwords, and `data/*.json`, `data/*.pbit`, `data/*.yaml`, `.env`, and key files are excluded from Git tracking.
