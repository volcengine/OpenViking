-- Account authorization metadata: at most ten canonical user file URIs as JSON.
-- Apply before starting an upgraded MySQL or PostgreSQL account-store runtime.
ALTER TABLE ov_accounts ADD COLUMN frozen_files TEXT;
