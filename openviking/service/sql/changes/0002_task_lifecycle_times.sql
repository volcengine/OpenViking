-- MySQL: add lifecycle times to an existing OpenViking Task store.
--
-- Apply once before starting a version that uses started_at / finished_at.
-- For a fresh database, run task_store_init.sql, then applicable MySQL changes
-- in order (including 0001_task_store_service_name.sql).
-- For a custom table prefix, replace ov_tasks below.
-- Existing rows keep NULL timestamps; no historical backfill is required.
-- Skip this script when both columns already exist.

ALTER TABLE `ov_tasks`
    ADD COLUMN started_at DOUBLE NULL,
    ADD COLUMN finished_at DOUBLE NULL;
