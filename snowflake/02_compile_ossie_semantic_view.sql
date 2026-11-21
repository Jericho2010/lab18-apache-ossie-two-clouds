-- =============================================================================
-- Lab 18: Compile Apache Ossie YAML -> Snowflake Semantic View & Power BI .pbit
-- Note: src/snowflake_semantic.py reads semantic/crm_account_model.ossie.yaml
--       directly and passes its YAML text to SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML.
-- =============================================================================

USE DATABASE CRM_OSSIE_LAB18;
USE SCHEMA SEMANTIC;

-- 1. Compile Apache Ossie YAML into a native Snowflake Semantic View
-- CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML('CRM_OSSIE_LAB18.SEMANTIC', $$<ossie_yaml>$$);

-- 2. Verify compiled Semantic View objects
SHOW SEMANTIC VIEWS IN SCHEMA CRM_OSSIE_LAB18.SEMANTIC;

-- 3. Round-trip verification (Snowflake -> Apache Ossie YAML)
SELECT SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW('CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW');

-- 4. Power BI .pbit export (returns base64-encoded .pbit ZIP archive)
SELECT SYSTEM$EXPORT_PBIT_FROM_SEMANTIC_VIEW('CRM_OSSIE_LAB18.SEMANTIC.CRM_ACCOUNT_SEMANTIC_VIEW');
