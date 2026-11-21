"""Typed Pydantic configuration and canonical paths for Lab 18."""

from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"
OSSIE_YAML_PATH = PROJECT_ROOT / "semantic" / "crm_account_model.ossie.yaml"
OSSIE_SCHEMA_PATH = PROJECT_ROOT / "semantic" / "osi-schema.json"
SNOWFLAKE_SQL_DIR = PROJECT_ROOT / "snowflake"
DATA_DIR = PROJECT_ROOT / "data"
PBIT_OUTPUT_PATH = DATA_DIR / "crm_account_model.pbit"
ROUNDTRIP_YAML_PATH = DATA_DIR / "roundtrip_crm_account_model.ossie.yaml"


class Lab18Settings(BaseSettings):
    """Runtime settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dataverse_url: str = "https://your-org.crm.dynamics.com"
    dataverse_api_version: str = "v9.2"

    snowflake_account: str = "<YOUR_ORG>-<YOUR_ACCOUNT>"
    snowflake_user: str = "<YOUR_SNOWFLAKE_USER>"
    snowflake_private_key_file: str = "~/.ssh/snowflake_rsa_key.p8"
    snowflake_role: str = "ACCOUNTADMIN"
    snowflake_warehouse: str = "SNOWFLAKE_LEARNING_WH"
    snowflake_database: str = "CRM_OSSIE_LAB18"
    snowflake_schema: str = "SEMANTIC"

    snowflake_allowed_ip: str = ""
    snowflake_mcp_endpoint: str = ""
    snowflake_mcp_pat: str = ""

    @model_validator(mode="after")
    def _populate_dynamic_mcp_endpoint(self) -> Self:
        """Build the Snowflake-Managed MCP endpoint dynamically when unset."""
        if not self.snowflake_mcp_endpoint:
            self.snowflake_mcp_endpoint = (
                f"https://{self.snowflake_account.lower()}.snowflakecomputing.com"
                f"/api/v2/databases/{self.snowflake_database}"
                f"/schemas/{self.snowflake_schema}/mcp-servers/CRM_OSSIE_MCP_SERVER"
            )
        return self

    @property
    def semantic_view_fqn(self) -> str:
        """Return fully qualified Snowflake Semantic View identifier."""
        return f"{self.snowflake_database}.{self.snowflake_schema}.CRM_ACCOUNT_SEMANTIC_VIEW"

    @property
    def dataverse_base_api(self) -> str:
        """Return normalized Dataverse Web API base URL."""
        return f"{self.dataverse_url.rstrip('/')}/api/data/{self.dataverse_api_version}"

    @property
    def snowflake_statements_endpoint(self) -> str:
        """Return Snowflake SQL REST API v2 statements endpoint URL."""
        return (
            f"https://{self.snowflake_account.lower()}.snowflakecomputing.com"
            "/api/v2/statements"
        )
