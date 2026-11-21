-- =============================================================================
-- Lab 18: Dedicated Apache Ossie Database & Dynamic Dataverse Lookup Tables
-- Note: Zero hardcoded lookup rows. Labels are MERGEd live from Dataverse API
--       by src/dataverse_picklist_sync.py.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS CRM_OSSIE_LAB18;
CREATE SCHEMA IF NOT EXISTS CRM_OSSIE_LAB18.SEMANTIC;

CREATE TABLE IF NOT EXISTS CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_CATEGORY (
    CATEGORY_CODE   NUMBER(38,0)   NOT NULL,
    CATEGORY_LABEL  VARCHAR(100)   NOT NULL,
    SYNCED_AT       TIMESTAMP_TZ   DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DIM_ACCOUNT_CATEGORY PRIMARY KEY (CATEGORY_CODE)
);

CREATE TABLE IF NOT EXISTS CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_STATE (
    STATE_CODE   NUMBER(38,0)   NOT NULL,
    STATE_LABEL  VARCHAR(100)   NOT NULL,
    SYNCED_AT    TIMESTAMP_TZ   DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DIM_ACCOUNT_STATE PRIMARY KEY (STATE_CODE)
);
