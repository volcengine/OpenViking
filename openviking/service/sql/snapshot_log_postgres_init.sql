-- Provision once with a deployment/migration role before starting OV.
-- Runtime needs SELECT, INSERT, UPDATE and DELETE on snapshot_log, USAGE on its
-- sequence, and SELECT, INSERT and UPDATE on snapshot_log_generation.
-- No extensions are required. Use a dedicated database/schema per OV deployment.
CREATE TABLE IF NOT EXISTS snapshot_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account TEXT COLLATE "C" NOT NULL CHECK (octet_length(account) <= 255),
    branch TEXT COLLATE "C" NOT NULL CHECK (octet_length(branch) <= 512),
    path TEXT COLLATE "C" NOT NULL CHECK (octet_length(path) BETWEEN 1 AND 2048),
    commit_oid TEXT COLLATE "C" NOT NULL CHECK (commit_oid ~ '^[0-9a-fA-F]{40}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A path digest keeps maximum-width keys below PostgreSQL's B-tree tuple limit.
-- Queries also compare the original path; digest matches alone are never trusted.
-- A digest collision for the same commit rejects the append rather than losing rows.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_snapshot_log_path_commit
    ON snapshot_log (account, branch, md5(path), commit_oid);
CREATE INDEX IF NOT EXISTS idx_snapshot_log_lookup
    ON snapshot_log (account, branch, md5(path), id DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_log_latest
    ON snapshot_log (account, branch, id DESC);

CREATE TABLE IF NOT EXISTS snapshot_log_generation (
    account TEXT COLLATE "C" NOT NULL,
    branch TEXT COLLATE "C" NOT NULL,
    root TEXT NOT NULL,
    PRIMARY KEY (account, branch)
);
