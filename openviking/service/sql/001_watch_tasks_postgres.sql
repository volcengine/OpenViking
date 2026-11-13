-- OpenViking PostgreSQL Watch store schema (PostgreSQL 14+).
-- Provision before selecting storage.watch_store.provider=postgres.
-- For a custom params.table, replace ov_watch_tasks in table and index names.
-- This is independent of the Task/Work, Account and vector storage schemas.

CREATE TABLE ov_watch_tasks (
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    task_id VARCHAR(255) COLLATE "C" NOT NULL,
    account_id VARCHAR(255) COLLATE "C" NOT NULL,
    user_id VARCHAR(255) COLLATE "C" NOT NULL,
    original_role VARCHAR(64) NOT NULL,
    target_uri_hash VARCHAR(64) NOT NULL,
    target_uri_key VARCHAR(768) COLLATE "C" NOT NULL,
    target_uri TEXT COLLATE "C" NULL,
    enabled BOOLEAN NOT NULL,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    watch_interval DOUBLE PRECISION NOT NULL,
    next_run_at DOUBLE PRECISION NULL,
    current_run_id VARCHAR(255) COLLATE "C" NULL,
    run_started_at DOUBLE PRECISION NULL,
    last_execution_time DOUBLE PRECISION NULL,
    last_task_id VARCHAR(255) COLLATE "C" NULL,
    last_status VARCHAR(64) NULL,
    last_error TEXT NULL,
    payload JSON NOT NULL,
    connector BOOLEAN NOT NULL DEFAULT FALSE,
    -- AES-GCM ciphertext; every node needs the same credential_encryption_key.
    auth_state TEXT NULL,
    connector_states TEXT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (service_name, task_id)
);

CREATE INDEX ix_ov_watch_tasks_due
    ON ov_watch_tasks (service_name, enabled, next_run_at);
CREATE INDEX ix_ov_watch_tasks_run
    ON ov_watch_tasks (service_name, run_started_at);
CREATE INDEX ix_ov_watch_tasks_target
    ON ov_watch_tasks (service_name, account_id, target_uri_hash);
-- Bound UTF-8 B-tree entry size; longer paths still use the full URI predicate.
CREATE INDEX ix_ov_watch_tasks_target_prefix
    ON ov_watch_tasks (service_name, account_id, deleted, substr(target_uri_key, 1, 128));
-- Account/user visibility is filtered separately; avoid an oversized UTF-8 key.
CREATE INDEX ix_ov_watch_tasks_list_all
    ON ov_watch_tasks (service_name, deleted, created_at, task_id);
