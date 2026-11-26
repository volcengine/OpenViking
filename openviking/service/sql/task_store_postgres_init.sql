-- PostgreSQL Task/Work store, including service_name partitioning.
-- Run once before starting OpenViking. Replace ov_tasks consistently for a custom prefix.
BEGIN;

CREATE TABLE ov_tasks (
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    task_id VARCHAR(255) COLLATE "C" NOT NULL,
    task_type VARCHAR(255) COLLATE "C" NOT NULL,
    status VARCHAR(16) COLLATE "C" NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    resource_id TEXT COLLATE "C",
    resource_id_hash VARCHAR(64) COLLATE "C",
    account_id VARCHAR(255) COLLATE "C" NOT NULL,
    user_id VARCHAR(255) COLLATE "C" NOT NULL,
    meta JSONB,
    stage VARCHAR(255),
    result JSONB,
    error TEXT,
    auth JSONB,
    processing_seconds DOUBLE PRECISION,
    reject_if_active BOOLEAN NOT NULL DEFAULT FALSE,
    is_internal BOOLEAN NOT NULL DEFAULT FALSE,
    active_business_key VARCHAR(64) COLLATE "C",
    PRIMARY KEY (service_name, task_id),
    CONSTRAINT uq_ov_tasks_active_business_key UNIQUE (service_name, active_business_key)
);

CREATE INDEX ix_ov_tasks_account_created ON ov_tasks (service_name, account_id, created_at, task_id);
CREATE INDEX ix_ov_tasks_acct_internal_created ON ov_tasks (service_name, account_id, is_internal, created_at);
CREATE INDEX ix_ov_tasks_acct_resource_created ON ov_tasks (service_name, account_id, resource_id_hash, created_at);
CREATE INDEX ix_ov_tasks_acct_status_created ON ov_tasks (service_name, account_id, status, created_at);
CREATE INDEX ix_ov_tasks_acct_type_created ON ov_tasks (service_name, account_id, task_type, created_at);
CREATE INDEX ix_ov_tasks_created ON ov_tasks (service_name, created_at, task_id);
CREATE INDEX ix_ov_tasks_expiry ON ov_tasks (service_name, status, updated_at, task_id);
CREATE INDEX ix_ov_tasks_owner ON ov_tasks (service_name, account_id, user_id);
CREATE INDEX ix_ov_tasks_owner_created ON ov_tasks (service_name, account_id, user_id, created_at);
CREATE INDEX ix_ov_tasks_owner_internal_created ON ov_tasks (service_name, account_id, user_id, is_internal, created_at);

CREATE TABLE ov_tasks_works (
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    task_id VARCHAR(255) COLLATE "C" NOT NULL,
    work_id VARCHAR(255) COLLATE "C" NOT NULL,
    account_id VARCHAR(255) COLLATE "C" NOT NULL,
    user_id VARCHAR(255) COLLATE "C" NOT NULL,
    queue_name VARCHAR(255) COLLATE "C" NOT NULL,
    state VARCHAR(16) COLLATE "C" NOT NULL,
    requeue_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    errors JSONB,
    context_count INTEGER NOT NULL DEFAULT 0,
    enqueued_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (service_name, task_id, work_id)
);

CREATE INDEX ix_ov_tasks_works_owner ON ov_tasks_works (service_name, account_id, user_id);
CREATE INDEX ix_ov_tasks_works_queue ON ov_tasks_works (service_name, task_id, queue_name);
CREATE INDEX ix_ov_tasks_works_state ON ov_tasks_works (service_name, task_id, state);

CREATE TABLE ov_tasks_execution_events (
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    task_id VARCHAR(255) COLLATE "C" NOT NULL,
    seq INTEGER NOT NULL,
    recorded_at VARCHAR(64) COLLATE "C" NOT NULL,
    kind VARCHAR(64) COLLATE "C" NOT NULL,
    status VARCHAR(16) COLLATE "C" NOT NULL,
    stage VARCHAR(128),
    operation TEXT,
    error TEXT,
    dropped_count INTEGER NOT NULL DEFAULT 0,
    started_mid_task BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (service_name, task_id, seq)
);

COMMIT;
