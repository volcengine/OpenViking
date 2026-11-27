-- Provision this schema before selecting storage.compile_store.provider=postgres.
-- For a custom table prefix, replace ov_compile in the tables and index names.
-- Quote table names if the prefix contains uppercase letters.
CREATE TABLE ov_compile_configs (
    config_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    account_id VARCHAR(255) COLLATE "C" NOT NULL CHECK (account_id ~ '^[A-Za-z0-9_.@-]+$'),
    owner_user_id VARCHAR(255) COLLATE "C" NOT NULL CHECK (owner_user_id ~ '^[A-Za-z0-9_.@-]+$'),
    compile_name VARCHAR(64) COLLATE "C" NOT NULL,
    updated_by VARCHAR(255) COLLATE "C" NOT NULL,
    source_uris JSONB NOT NULL,
    target_uri TEXT NOT NULL,
    skill_uri TEXT NOT NULL,
    instruction TEXT NULL,
    args JSONB NOT NULL,
    auto_update BOOLEAN NOT NULL,
    schedule_frequency VARCHAR(6) COLLATE "C" NULL,
    schedule_hour SMALLINT NULL,
    schedule_minute SMALLINT NULL,
    schedule_weekday SMALLINT NULL,
    task_first_create_time DOUBLE PRECISION NOT NULL,
    last_run_time DOUBLE PRECISION NULL,
    last_triggered_slot VARCHAR(40) COLLATE "C" NULL,
    next_triggered_slot VARCHAR(40) COLLATE "C" NULL,
    progress JSONB NOT NULL,
    execution_context JSONB NOT NULL,
    CONSTRAINT uq_ov_compile_configs_name
        UNIQUE (service_name, account_id, compile_name, owner_user_id)
);

CREATE TABLE ov_compile_runs (
    config_id BIGINT NOT NULL,
    task_id VARCHAR(68) COLLATE "C" NOT NULL,
    actor_user_id VARCHAR(255) COLLATE "C" NOT NULL CHECK (actor_user_id ~ '^[A-Za-z0-9_.@-]+$'),
    status VARCHAR(16) COLLATE "C" NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION NULL,
    finished_at DOUBLE PRECISION NULL,
    "trigger" VARCHAR(16) COLLATE "C" NOT NULL,
    mode VARCHAR(16) COLLATE "C" NULL,
    base_snapshot VARCHAR(255) COLLATE "C" NULL,
    start_snapshot VARCHAR(255) COLLATE "C" NULL,
    finish_snapshot VARCHAR(255) COLLATE "C" NULL,
    skill_snapshot_id VARCHAR(255) COLLATE "C" NULL,
    params JSONB NOT NULL,
    result JSONB NULL,
    error TEXT NULL,
    PRIMARY KEY (config_id, task_id)
);
CREATE INDEX ix_ov_compile_runs_history
    ON ov_compile_runs (config_id, created_at, task_id);
CREATE INDEX ix_ov_compile_runs_expiry
    ON ov_compile_runs (finished_at, config_id, task_id);
