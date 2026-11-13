-- Add an optional usage description per account user.
--
-- The description records why a key exists and which consumer holds it
-- (human operator, backup cronjob, uptime probe). It is non-credential
-- metadata: existing rows keep NULL and behave exactly as before.

ALTER TABLE `ov_accounts_users`
    ADD COLUMN `description` VARCHAR(512) COLLATE utf8mb4_bin NULL AFTER `role`;
