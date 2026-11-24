-- OpenViking MySQL Task/Work store schema.
-- Provision this schema before selecting storage.task_store.provider=mysql.
-- For a custom table prefix, replace ov_tasks consistently in all tables,
-- indexes, constraints, and the foreign-key target.
-- Use at most 38 ASCII letters/digits/underscores, starting with a letter,
-- so all derived identifiers remain within MySQL's 64-character limit.

CREATE TABLE `ov_tasks` (
    task_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    task_type VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    status VARCHAR(16) COLLATE ascii_bin NOT NULL,
    created_at DOUBLE NOT NULL,
    updated_at DOUBLE NOT NULL,
    resource_id TEXT COLLATE utf8mb4_bin NULL,
    account_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    user_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    meta JSON NULL,
    stage VARCHAR(255) NULL,
    result JSON NULL,
    error TEXT NULL,
    auth JSON NULL,
    processing_seconds DOUBLE NULL,
    reject_if_active BOOLEAN NOT NULL DEFAULT 0,
    is_internal BOOLEAN NOT NULL DEFAULT 0,
    active_business_key VARBINARY(32) NULL,
    PRIMARY KEY (task_id),
    UNIQUE KEY uq_ov_tasks_active_business_key (active_business_key),
    KEY ix_ov_tasks_owner_created (account_id, user_id, created_at, task_id),
    KEY ix_ov_tasks_account_created (account_id, created_at, task_id),
    KEY ix_ov_tasks_created (created_at, task_id),
    KEY ix_ov_tasks_owner_status (account_id, user_id, status),
    KEY ix_ov_tasks_owner_internal_created
        (account_id, user_id, is_internal, created_at, task_id),
    KEY ix_ov_tasks_expiry (status, updated_at, task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;

CREATE TABLE `ov_tasks_works` (
    task_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    work_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    account_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    user_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    queue_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    state VARCHAR(16) COLLATE ascii_bin NOT NULL,
    requeue_count INT NOT NULL DEFAULT 0,
    error TEXT NULL,
    errors JSON NULL,
    context_count INT NOT NULL DEFAULT 0,
    enqueued_at DOUBLE NOT NULL,
    started_at DOUBLE NULL,
    finished_at DOUBLE NULL,
    updated_at DOUBLE NOT NULL,
    PRIMARY KEY (task_id, work_id),
    CONSTRAINT fk_ov_tasks_works_task
        FOREIGN KEY (task_id) REFERENCES `ov_tasks` (task_id)
        ON DELETE CASCADE,
    KEY ix_ov_tasks_works_state (task_id, state),
    KEY ix_ov_tasks_works_queue (task_id, queue_name, state),
    KEY ix_ov_tasks_works_owner (account_id, user_id, task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;

CREATE TABLE `ov_tasks_execution_events` (
    task_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    seq INT NOT NULL,
    recorded_at VARCHAR(64) COLLATE ascii_bin NOT NULL,
    kind VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    status VARCHAR(16) COLLATE ascii_bin NOT NULL,
    stage VARCHAR(128) NULL,
    operation TEXT NULL,
    error TEXT NULL,
    dropped_count INT NOT NULL DEFAULT 0,
    started_mid_task BOOLEAN NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, seq),
    CONSTRAINT fk_ov_tasks_execution_events_task
        FOREIGN KEY (task_id) REFERENCES `ov_tasks` (task_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;
