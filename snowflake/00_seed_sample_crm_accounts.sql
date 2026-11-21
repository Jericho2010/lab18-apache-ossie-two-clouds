-- =============================================================================
-- Lab 18 (Standalone Trial Setup): Idempotent Sample CRM Accounts Seed
-- Note: For users without an upstream CRM_LAB16 Bronze sync pipeline.
--       Seeds 5 synthetic sample accounts matching:
--         - Total revenue           : $6,390,000.00 (5 accounts)
--         - Preferred + Active total: $4,990,000.00 (3 accounts)
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE DATABASE IF NOT EXISTS CRM_LAB16;
CREATE SCHEMA IF NOT EXISTS CRM_LAB16.CRM_BRONZE;

CREATE TABLE IF NOT EXISTS CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS (
    ACCOUNTID            VARCHAR(36)    NOT NULL,
    NAME                 VARCHAR(160)   NOT NULL,
    ACCOUNTNUMBER        VARCHAR(20),
    ACCOUNTCATEGORYCODE  NUMBER(38,0),  -- 1 = Preferred Customer, 2 = Standard
    REVENUE              NUMBER(18,2),
    STATECODE            NUMBER(38,0)   NOT NULL, -- 0 = Active, 1 = Inactive
    STATUSCODE           NUMBER(38,0),
    MODIFIEDON           TIMESTAMP_TZ,
    CONSTRAINT PK_RAW_ACCOUNTS PRIMARY KEY (ACCOUNTID)
);

MERGE INTO CRM_LAB16.CRM_BRONZE.RAW_ACCOUNTS AS target
USING (
    SELECT * FROM VALUES
        (
            '11111111-1111-4111-8111-111111111001',
            'Northwind Precision Engineering',
            'ACC-1001',
            1,
            2400000.00,
            0,
            1,
            '2026-11-15T08:00:00Z'::TIMESTAMP_TZ
        ),
        (
            '11111111-1111-4111-8111-111111111002',
            'Contoso Cloud Systems Ltd',
            'ACC-1002',
            1,
            1590000.00,
            0,
            1,
            '2026-11-16T09:30:00Z'::TIMESTAMP_TZ
        ),
        (
            '11111111-1111-4111-8111-111111111003',
            'Alpine Robotics Group',
            'ACC-1003',
            1,
            1000000.00,
            0,
            1,
            '2026-11-17T11:15:00Z'::TIMESTAMP_TZ
        ),
        (
            '11111111-1111-4111-8111-111111111004',
            'Harbour Freight Sample Logistics',
            'ACC-1004',
            2,
            850000.00,
            0,
            1,
            '2026-11-18T14:45:00Z'::TIMESTAMP_TZ
        ),
        (
            '11111111-1111-4111-8111-111111111005',
            'Beacon Marine Components',
            'ACC-1005',
            1,
            550000.00,
            1,
            2,
            '2026-11-19T16:20:00Z'::TIMESTAMP_TZ
        )
) AS src (ACCOUNTID, NAME, ACCOUNTNUMBER, ACCOUNTCATEGORYCODE, REVENUE, STATECODE, STATUSCODE, MODIFIEDON)
ON target.ACCOUNTID = src.ACCOUNTID
WHEN MATCHED THEN UPDATE SET
    NAME = src.NAME,
    ACCOUNTNUMBER = src.ACCOUNTNUMBER,
    ACCOUNTCATEGORYCODE = src.ACCOUNTCATEGORYCODE,
    REVENUE = src.REVENUE,
    STATECODE = src.STATECODE,
    STATUSCODE = src.STATUSCODE,
    MODIFIEDON = src.MODIFIEDON
WHEN NOT MATCHED THEN INSERT (
    ACCOUNTID, NAME, ACCOUNTNUMBER, ACCOUNTCATEGORYCODE, REVENUE, STATECODE, STATUSCODE, MODIFIEDON
) VALUES (
    src.ACCOUNTID, src.NAME, src.ACCOUNTNUMBER, src.ACCOUNTCATEGORYCODE, src.REVENUE, src.STATECODE, src.STATUSCODE, src.MODIFIEDON
);

-- =============================================================================
-- OPTIONAL (No-Dataverse Snowflake-Only Trial):
-- If you do not have a Microsoft Dataverse tenant for `./run.sh --sync-picklists`,
-- run `snowflake/01_setup_ossie_database.sql` and uncomment the MERGE statements below
-- before running `./run.sh --compile --export-pbit --provision-mcp --query-mcp`:
--
-- MERGE INTO CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_CATEGORY AS t
-- USING (SELECT * FROM VALUES (1, 'Preferred Customer'), (2, 'Standard')) AS s(CATEGORY_CODE, CATEGORY_LABEL)
-- ON t.CATEGORY_CODE = s.CATEGORY_CODE
-- WHEN MATCHED THEN UPDATE SET CATEGORY_LABEL = s.CATEGORY_LABEL
-- WHEN NOT MATCHED THEN INSERT (CATEGORY_CODE, CATEGORY_LABEL) VALUES (s.CATEGORY_CODE, s.CATEGORY_LABEL);
--
-- MERGE INTO CRM_OSSIE_LAB18.SEMANTIC.DIM_ACCOUNT_STATE AS t
-- USING (SELECT * FROM VALUES (0, 'Active'), (1, 'Inactive')) AS s(STATE_CODE, STATE_LABEL)
-- ON t.STATE_CODE = s.STATE_CODE
-- WHEN MATCHED THEN UPDATE SET STATE_LABEL = s.STATE_LABEL
-- WHEN NOT MATCHED THEN INSERT (STATE_CODE, STATE_LABEL) VALUES (s.STATE_CODE, s.STATE_LABEL);
-- =============================================================================
