-- Add indexes for account and user management list pagination.
--
-- These indexes support:
-- - list_accounts(): service-scoped ordering by created_at/account_id
-- - list_users_page(): active-user pagination by account and created_at/user_pk
-- - list_users_page(role_filter=...): active-user pagination by account, role,
--   and created_at/user_pk

ALTER TABLE `ov_accounts`
    ADD INDEX ix_ov_accounts_account_created
        (service_name, created_at, account_id);

ALTER TABLE `ov_accounts_users`
    ADD INDEX ix_ov_accounts_user_active_created
        (service_name, account_pk, deletion_task_id, created_at, user_pk),
    ADD INDEX ix_ov_accounts_user_role_active_created
        (service_name, account_pk, `role`, deletion_task_id, created_at, user_pk);
