-- Add service_name isolation to an existing OpenViking MySQL Task/Work store.
--
-- This script migrates tables created from openviking/service/sql/task_store_init.sql
-- before service_name was introduced. If the existing rows belong to a deployment
-- whose configured service_name is not 'openviking-server', replace that literal
-- consistently before running this script. For a custom table prefix, replace
-- ov_tasks consistently in all table and index names.

ALTER TABLE `ov_tasks_works`
    DROP FOREIGN KEY fk_ov_tasks_works_task;

ALTER TABLE `ov_tasks_execution_events`
    DROP FOREIGN KEY fk_ov_tasks_execution_events_task;

ALTER TABLE `ov_tasks`
    ADD COLUMN service_name VARCHAR(255) COLLATE utf8mb4_bin
        NOT NULL DEFAULT 'openviking-server' FIRST,
    ADD COLUMN resource_id_hash VARCHAR(64) COLLATE ascii_bin NULL AFTER resource_id,
    ADD COLUMN active_business_key_hex VARCHAR(64) COLLATE ascii_bin NULL
        AFTER active_business_key;

UPDATE `ov_tasks`
SET
    resource_id_hash = CASE
        WHEN resource_id IS NULL THEN NULL
        ELSE SHA2(resource_id, 256)
    END,
    active_business_key_hex = CASE
        WHEN active_business_key IS NULL THEN NULL
        ELSE LOWER(HEX(active_business_key))
    END;

ALTER TABLE `ov_tasks_works`
    ADD COLUMN service_name VARCHAR(255) COLLATE utf8mb4_bin
        NOT NULL DEFAULT 'openviking-server' FIRST;

ALTER TABLE `ov_tasks_execution_events`
    ADD COLUMN service_name VARCHAR(255) COLLATE utf8mb4_bin
        NOT NULL DEFAULT 'openviking-server' FIRST;

ALTER TABLE `ov_tasks`
    DROP INDEX uq_ov_tasks_active_business_key,
    DROP COLUMN active_business_key,
    CHANGE COLUMN active_business_key_hex active_business_key
        VARCHAR(64) COLLATE ascii_bin NULL,
    DROP INDEX ix_ov_tasks_owner_created,
    DROP INDEX ix_ov_tasks_account_created,
    DROP INDEX ix_ov_tasks_created,
    DROP INDEX ix_ov_tasks_owner_status,
    DROP INDEX ix_ov_tasks_owner_internal_created,
    DROP INDEX ix_ov_tasks_expiry,
    DROP PRIMARY KEY,
    ADD PRIMARY KEY (service_name, task_id),
    ADD UNIQUE KEY uq_ov_tasks_active_business_key
        (service_name, active_business_key),
    ADD KEY ix_ov_tasks_owner_created
        (service_name, account_id, user_id, created_at),
    ADD KEY ix_ov_tasks_account_created
        (service_name, account_id, created_at, task_id),
    ADD KEY ix_ov_tasks_created
        (service_name, created_at, task_id),
    ADD KEY ix_ov_tasks_owner
        (service_name, account_id, user_id),
    ADD KEY ix_ov_tasks_owner_internal_created
        (service_name, account_id, user_id, is_internal, created_at),
    ADD KEY ix_ov_tasks_acct_type_created
        (service_name, account_id, task_type, created_at),
    ADD KEY ix_ov_tasks_acct_status_created
        (service_name, account_id, status, created_at),
    ADD KEY ix_ov_tasks_acct_resource_created
        (service_name, account_id, resource_id_hash, created_at),
    ADD KEY ix_ov_tasks_acct_internal_created
        (service_name, account_id, is_internal, created_at),
    ADD KEY ix_ov_tasks_expiry
        (service_name, status, updated_at, task_id);

ALTER TABLE `ov_tasks_works`
    DROP INDEX ix_ov_tasks_works_state,
    DROP INDEX ix_ov_tasks_works_queue,
    DROP INDEX ix_ov_tasks_works_owner,
    DROP PRIMARY KEY,
    ADD PRIMARY KEY (service_name, task_id, work_id),
    ADD KEY ix_ov_tasks_works_state
        (service_name, task_id, state),
    ADD KEY ix_ov_tasks_works_queue
        (service_name, task_id, queue_name),
    ADD KEY ix_ov_tasks_works_owner
        (service_name, account_id, user_id);

ALTER TABLE `ov_tasks_execution_events`
    DROP PRIMARY KEY,
    ADD PRIMARY KEY (service_name, task_id, seq);
