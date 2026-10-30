-- Apply after 0001_task_store_service_name.sql, before starting the updated service.
-- Replace ov_tasks consistently when storage.task_store.params.table uses a custom prefix.
-- Existing error messages are retained and their unavailable codes remain NULL.
ALTER TABLE `ov_tasks`
    ADD COLUMN error_code VARCHAR(64) COLLATE ascii_bin NULL AFTER error;
ALTER TABLE `ov_tasks_works`
    ADD COLUMN error_code VARCHAR(64) COLLATE ascii_bin NULL AFTER error;
