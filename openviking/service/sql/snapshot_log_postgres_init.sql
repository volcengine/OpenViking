-- Provision once with a deployment/migration role before starting OV.
-- Runtime needs SELECT and INSERT on snapshot_log and USAGE on its sequence.
-- No extensions are required. service_name isolates deployments sharing a database/schema.
CREATE TABLE IF NOT EXISTS snapshot_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service_name TEXT COLLATE "C" NOT NULL CHECK (octet_length(service_name) BETWEEN 1 AND 64),
    account TEXT COLLATE "C" NOT NULL CHECK (octet_length(account) <= 64),
    branch TEXT COLLATE "C" NOT NULL CHECK (octet_length(branch) <= 128),
    path TEXT COLLATE "C" NOT NULL CHECK (octet_length(path) BETWEEN 1 AND 2048),
    commit_oid TEXT COLLATE "C" NOT NULL CHECK (commit_oid ~ '^[0-9a-fA-F]{40}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Bounded service/account/branch lengths keep full-path keys within the B-tree limit.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_snapshot_log_path_commit
    ON snapshot_log (service_name, account, branch, path, commit_oid);
CREATE INDEX IF NOT EXISTS idx_snapshot_log_lookup
    ON snapshot_log (service_name, account, branch, path, id DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_log_latest
    ON snapshot_log (service_name, account, branch, id DESC);
