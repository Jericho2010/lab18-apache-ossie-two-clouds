# Lab 18 Setup Guide — Running Apache Ossie Across Snowflake & Power BI

This guide walks through setting up Lab 18 from a clean workstation in either:
- **Mode A (Live Two-Platform Flow):** Real Microsoft Dataverse CRM OptionSets + Snowflake Semantic View / Managed HTTP MCP + Power BI `.pbit` export.
- **Mode B (Snowflake-Only Trial Mode):** Self-contained setup using the idempotent synthetic seed script [`snowflake/00_seed_sample_crm_accounts.sql`](../snowflake/00_seed_sample_crm_accounts.sql) (no Azure or Dataverse tenant required).

---

## 1. Prerequisites

| Requirement | Required / Optional | Notes |
| :--- | :--- | :--- |
| **Snowflake Account** | Required | Trial or enterprise account with Semantic Views (`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML`), Cortex Analyst, and Snowflake-Managed MCP enabled (`ACCOUNTADMIN` for initial DDL/PAT setup). |
| **Python 3.11+ & `uv`** | Required | Installed and managed strictly via [`uv`](https://docs.astral.sh/uv/). |
| **OpenSSL** | Required | Generates the RSA key pair for non-interactive Snowflake Admin DDL authentication. |
| **Azure CLI (`az`) + Dataverse Environment** | Optional | Required only for live `--sync-picklists` from Dataverse Web API v9.2 (`az login` session). |
| **Power BI Desktop (Windows)** | Optional | Needed only if you want to visually open `data/crm_account_model.pbit`. Python inspection of `DataModelSchema` runs natively on Linux/macOS/Windows without Power BI Desktop. |

---

## 2. Step-by-Step Setup

### Step 1: Initialize the Python Environment

```bash
./setup.sh
```

`setup.sh` creates `.venv` on Python 3.11 using `uv` and installs runtime + dev dependencies from [`pyproject.toml`](../pyproject.toml) (`pydantic-settings`, `snowflake-connector-python`, `httpx`, `typer`, `pyyaml`, `jsonschema`, `pytest`, `ruff`).

---

### Step 2: Configure Snowflake RSA Key-Pair Authentication

Generate an unencrypted PKCS#8 private key outside the repository (ignored by `.gitignore`):

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out ~/.ssh/snowflake_rsa_key.p8 -nocrypt
chmod 600 ~/.ssh/snowflake_rsa_key.p8
openssl rsa -in ~/.ssh/snowflake_rsa_key.p8 -pubout -outform PEM | grep -v "PUBLIC KEY" | tr -d '\n'
echo ""
```

Assign the single-line base64 public key string in Snowsight as `ACCOUNTADMIN`:

```sql
ALTER USER "<YOUR_SNOWFLAKE_USER>" SET RSA_PUBLIC_KEY='<SINGLE_LINE_PUBLIC_KEY>';
```

*(Never commit `.p8`, `.pem`, or `.key` files into Git.)*

---

### Step 3: Create `.env` from `.env.example`

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` with your Snowflake account details:

```dotenv
DATAVERSE_URL=https://your-org.crm.dynamics.com
DATAVERSE_API_VERSION=v9.2

SNOWFLAKE_ACCOUNT=<YOUR_ORG>-<YOUR_ACCOUNT>
SNOWFLAKE_USER=<YOUR_SNOWFLAKE_USER>
SNOWFLAKE_PRIVATE_KEY_FILE=~/.ssh/snowflake_rsa_key.p8
SNOWFLAKE_ROLE=ACCOUNTADMIN
SNOWFLAKE_WAREHOUSE=SNOWFLAKE_LEARNING_WH
SNOWFLAKE_DATABASE=CRM_OSSIE_LAB18
SNOWFLAKE_SCHEMA=SEMANTIC

# Option 1: Pin your workstation egress IPv4/CIDR (/32 appended automatically if omitted)
# Option 2: Leave empty to resolve your current public IP automatically via https://api.ipify.org
SNOWFLAKE_ALLOWED_IP=
# Leave empty to derive https://<account>.snowflakecomputing.com/api/v2/databases/... automatically
SNOWFLAKE_MCP_ENDPOINT=
SNOWFLAKE_MCP_PAT=
```

> **Note on `SNOWFLAKE_MCP_PAT`:** Leave `SNOWFLAKE_MCP_PAT=` blank initially. `--provision-mcp` (and `--verify`) generates a 30-day PAT bound strictly to `ROLE_LAB18_MCP` on service user `SVC_LAB18_MCP` and writes it only to `.env` with `chmod 600`.

---

### Step 4: Prepare Source Tables (Choose Mode A or Mode B)

#### Mode A — Live Dataverse + Existing Bronze Table
If you already sync Dataverse `account` rows into `CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS`:

```bash
az login
./run.sh --sync-picklists
```

`--sync-picklists` creates `CRM_OSSIE_LAB18.SEMANTIC` (`DIM_ACCOUNT_CATEGORY`, `DIM_ACCOUNT_STATE`) via [`snowflake/01_setup_ossie_database.sql`](../snowflake/01_setup_ossie_database.sql) and `MERGE`s live localized OptionSet labels from Dataverse Web API v9.2.

#### Mode B — Standalone Snowflake Trial (No Dataverse Required)
Run [`snowflake/00_seed_sample_crm_accounts.sql`](../snowflake/00_seed_sample_crm_accounts.sql) followed by [`snowflake/01_setup_ossie_database.sql`](../snowflake/01_setup_ossie_database.sql) in Snowsight (using `ACCOUNTADMIN`).

Then uncomment and run the optional `MERGE` statements at the bottom of `snowflake/00_seed_sample_crm_accounts.sql` to populate `DIM_ACCOUNT_CATEGORY` and `DIM_ACCOUNT_STATE` with matching `Preferred Customer` / `Standard` and `Active` / `Inactive` labels.

Afterward, run the remaining Snowflake pipeline steps via Python:

```bash
./run.sh --validate-ossie
./run.sh --compile
./run.sh --export-pbit
./run.sh --provision-mcp
./run.sh --query-mcp
```

---

### Step 5: Run Full End-to-End Verification (Mode A)

If using live Dataverse (`az login` active):

```bash
./run.sh --verify
./run.sh --summary
```

This single pipeline executes all stages in sequence:
1. Validates [`semantic/crm_account_model.ossie.yaml`](../semantic/crm_account_model.ossie.yaml) against Snowflake `0.1.1` rules and official `0.3.0.dev0` [`semantic/osi-schema.json`](../semantic/osi-schema.json).
2. Syncs live Dataverse `accountcategorycode` and `statecode` OptionSet labels to Snowflake lookup tables.
3. Compiles `CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW` and checks `SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW` round-trip fidelity.
4. Exports `data/crm_account_model.pbit` via `SYSTEM$EXPORT_PBIT_FROM_SEMANTIC_VIEW` and decodes its UTF-16-LE `DataModelSchema` ZIP payload.
5. Provisions `CRM_OSSIE_MCP_SERVER`, `ROLE_LAB18_MCP`, `SVC_LAB18_MCP`, `LAB18_WORKSTATION_IP_POLICY` (`'__ALLOWED_IP_CIDR__'` substituted dynamically), and stores a 30-day `ROLE_LAB18_MCP` PAT in `.env`.
6. Calls MCP `initialize` -> `tools/list` -> `tools/call` (`CORTEX_ANALYST_MESSAGE`), validates the generated SQL with `validate_semantic_sql()`, executes it via `/api/v2/statements`, asserts numeric parity against direct SQL ground truth (`$4,990,000.00`), and writes a secret-free `data/verification_report.json`.

---

## 3. Troubleshooting

| Symptom | Likely Cause | Fix |
| :--- | :--- | :--- |
| `Snowflake RSA private key not found` | Path mismatch in `.env` | Set `SNOWFLAKE_PRIVATE_KEY_FILE=~/.ssh/snowflake_rsa_key.p8` (tilde expansion is supported). |
| `HTTP 403` / network policy blocked on MCP call | Workstation public IP changed | Update `SNOWFLAKE_ALLOWED_IP` (or blank it for `api.ipify.org`), drop/alter `LAB18_WORKSTATION_IP_POLICY` in Snowflake (`ALTER NETWORK POLICY LAB18_WORKSTATION_IP_POLICY SET ALLOWED_IP_LIST = ('<NEW_IP>/32')`), and re-run `--query-mcp`. |
| `SNOWFLAKE_ALLOWED_IP is unset and live public IP lookup failed` | Offline environment or firewall blocks `api.ipify.org` | Explicitly set `SNOWFLAKE_ALLOWED_IP=<YOUR_PUBLIC_IP>/32` in `.env`. |
| `SNOWFLAKE_MCP_PAT is empty` | Provisioning hasn't run yet | Run `./run.sh --provision-mcp` (requires admin RSA key auth first). |
| `SELECT * FROM SEMANTIC_VIEW(...)` rejected on REST SQL | Warehouse role or Cortex grants missing on `ROLE_LAB18_MCP` | Re-run `./run.sh --provision-mcp` as `ACCOUNTADMIN` so `SNOWFLAKE.CORTEX_ANALYST_USER` and `SNOWFLAKE.CORTEX_USER` are granted. |
