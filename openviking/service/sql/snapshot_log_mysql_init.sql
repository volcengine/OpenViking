-- Provision once with a deployment/migration role before starting OV.
-- Runtime needs only SELECT and INSERT on snapshot_log; it never runs DDL.
-- service_name isolates OV deployments that share a database.
CREATE TABLE IF NOT EXISTS snapshot_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  service_name VARBINARY(64) NOT NULL,
  account VARBINARY(64) NOT NULL,
  branch VARBINARY(512) NOT NULL,
  path VARBINARY(2048) NOT NULL,
  commit_oid CHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_snapshot_log_path_commit (service_name, account, branch, path, commit_oid),
  KEY idx_snapshot_log_latest (service_name, account, branch, id DESC),
  KEY idx_snapshot_log_lookup (service_name, account, branch, path, id DESC)
);
