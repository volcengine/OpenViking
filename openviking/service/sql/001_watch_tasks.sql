-- OpenViking MySQL Watch store schema.
-- Provision once before selecting storage.watch_store.provider=mysql.
-- For a custom storage.watch_store.params.table, replace ov_watch_tasks
-- consistently in table and index names.
-- Compatible with MySQL 5.7.

CREATE TABLE `ov_watch_tasks` (
    service_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    task_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    account_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    user_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    original_role VARCHAR(64) COLLATE ascii_bin NOT NULL,
    target_uri_hash CHAR(64) COLLATE ascii_bin NOT NULL,
    target_uri TEXT COLLATE utf8mb4_bin NULL,
    enabled BOOLEAN NOT NULL,
    deleted BOOLEAN NOT NULL DEFAULT 0,
    watch_interval DOUBLE NOT NULL,
    next_run_at DOUBLE NULL,
    current_run_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    run_started_at DOUBLE NULL,
    run_fenced BOOLEAN NOT NULL DEFAULT 0,
    last_execution_time DOUBLE NULL,
    last_task_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    last_status VARCHAR(64) COLLATE ascii_bin NULL,
    last_error TEXT COLLATE utf8mb4_bin NULL,
    payload JSON NOT NULL,
    connector BOOLEAN NOT NULL DEFAULT 0,
    -- AES-GCM ciphertext under storage.watch_store.params.credential_encryption_key.
    auth_state MEDIUMTEXT COLLATE ascii_bin NULL,
    connector_states MEDIUMTEXT COLLATE ascii_bin NULL,
    created_at DOUBLE NOT NULL,
    updated_at DOUBLE NOT NULL,
    PRIMARY KEY (service_name, task_id),
    KEY ix_ov_watch_tasks_due (service_name, enabled, next_run_at),
    KEY ix_ov_watch_tasks_run (service_name, current_run_id),
    KEY ix_ov_watch_tasks_target (service_name, account_id, target_uri_hash),
    KEY ix_ov_watch_tasks_list
        (service_name(128), account_id(128), user_id(128), created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;
