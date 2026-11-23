-- Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
-- SPDX-License-Identifier: AGPL-3.0
-- Administrator-run additive migration for preview PostgreSQL schema v1.
-- Replace all openviking_usage_audit identifiers for a custom schema.
-- Stop all writers before migration; restart all instances with the new code.
BEGIN;
DO $$
BEGIN
    IF (SELECT value FROM openviking_usage_audit._schema_meta WHERE key = 'version')
       IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'Usage/audit migration requires schema version 1';
    END IF;
END $$;
CREATE TABLE openviking_usage_audit.usage_deletion_fence (
    account_id text COLLATE "C" NOT NULL,
    scope text COLLATE "C" NOT NULL CHECK (scope IN ('account', 'user')),
    user_id text COLLATE "C" NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    PRIMARY KEY (account_id, scope, user_id)
);
INSERT INTO openviking_usage_audit._schema_meta (key, value) VALUES ('deletion_generation', '0');
UPDATE openviking_usage_audit._schema_meta SET value = '2' WHERE key = 'version';
COMMIT;
