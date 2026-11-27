-- PostgreSQL: add lifecycle times to an existing OpenViking Task store.
--
-- Apply once before starting a version that uses started_at / finished_at.
-- For a fresh database, run task_store_postgres_init.sql, then this script.
-- Do not apply MySQL change scripts to PostgreSQL.
-- For a custom table prefix, replace ov_tasks below; quote uppercase names.
-- Existing rows keep NULL timestamps; no historical backfill is required.
-- Skip this script when both columns already exist.

ALTER TABLE ov_tasks
    ADD COLUMN started_at DOUBLE PRECISION NULL,
    ADD COLUMN finished_at DOUBLE PRECISION NULL;
