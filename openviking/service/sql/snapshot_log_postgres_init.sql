-- Run with a deployment/migration role to provision or upgrade the schema.
-- Runtime needs SELECT and INSERT on snapshot_log and USAGE on its sequence.
-- No extensions are required. Use a dedicated database/schema per OV deployment.
CREATE TABLE IF NOT EXISTS snapshot_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account TEXT COLLATE "C" NOT NULL CHECK (octet_length(account) <= 255),
    branch TEXT COLLATE "C" NOT NULL CHECK (octet_length(branch) <= 512),
    path TEXT COLLATE "C" NOT NULL CHECK (octet_length(path) BETWEEN 1 AND 2048),
    commit_oid TEXT COLLATE "C" NOT NULL CHECK (commit_oid ~ '^[0-9a-fA-F]{40}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Hash index entries stay small, and PostgreSQL rechecks equality against the
-- full key when enforcing the exclusion constraint.
DO $$
DECLARE
    log_table regclass := 'snapshot_log'::regclass;
    legacy_index regclass;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = log_table AND conname = 'excl_snapshot_log_path_commit'
    ) THEN
        ALTER TABLE snapshot_log ADD CONSTRAINT excl_snapshot_log_path_commit
            EXCLUDE USING hash ((ARRAY[account, branch, path, commit_oid]) WITH =);
    END IF;

    -- Drop only the old index belonging to this table, even with a custom search_path.
    SELECT indexrelid INTO legacy_index
    FROM pg_index JOIN pg_class ON pg_class.oid = indexrelid
    WHERE indrelid = log_table AND relname = 'uniq_snapshot_log_path_commit';
    IF legacy_index IS NOT NULL THEN
        EXECUTE format('DROP INDEX %s', legacy_index);
    END IF;
END;
$$;

-- A path digest keeps lookup keys below the B-tree tuple limit. Queries also
-- compare the original path.
CREATE INDEX IF NOT EXISTS idx_snapshot_log_lookup
    ON snapshot_log (account, branch, md5(path), id DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_log_latest
    ON snapshot_log (account, branch, id DESC);
