-- Keep a group unavailable while its existing ACL grants are being cleaned.
-- Task IDs, ownership and cleanup parameters belong to the task store.
ALTER TABLE `ov_accounts_groups`
    ADD COLUMN deleting BOOLEAN NOT NULL DEFAULT 0,
    ADD INDEX ix_ov_accounts_group_deleting (service_name, deleting);
