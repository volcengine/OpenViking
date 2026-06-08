# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite schema for product usage/audit projections.

All time-keyed columns (`date_utc`, `hour_utc`, `created_at`) are persisted in
UTC. The viewer's timezone is applied at read time so a single store can
serve any region. Token and retrieval rollups are hour-grained so cross-tz
"today" queries can slice at user-local day boundaries.
"""

# Bump when the table layout changes incompatibly. Stored on the `_schema_meta`
# row so `SQLiteUsageAuditStore.initialize` can reset the local SQLite store
# when an older snapshot is detected.
SCHEMA_VERSION = 4

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS _schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_token_hourly (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    date_utc TEXT NOT NULL,
    hour_utc INTEGER NOT NULL,
    source TEXT NOT NULL,
    token_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        account_id, user_id, date_utc, hour_utc,
        source, token_type, provider, model_name
    )
);
CREATE INDEX IF NOT EXISTS idx_usage_token_account_date
    ON usage_token_hourly(account_id, date_utc, hour_utc);

CREATE TABLE IF NOT EXISTS usage_retrieval_hourly (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    date_utc TEXT NOT NULL,
    hour_utc INTEGER NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        account_id, user_id, date_utc, hour_utc, operation, status
    )
);
CREATE INDEX IF NOT EXISTS idx_usage_retrieval_account_date
    ON usage_retrieval_hourly(account_id, date_utc, hour_utc);

CREATE TABLE IF NOT EXISTS usage_context_write_bucket (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    date_utc TEXT NOT NULL,
    hour_utc INTEGER NOT NULL,
    operation TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        account_id, user_id, date_utc, hour_utc, operation
    )
);
CREATE INDEX IF NOT EXISTS idx_usage_context_write_account_date
    ON usage_context_write_bucket(account_id, date_utc, hour_utc);

CREATE TABLE IF NOT EXISTS request_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    account_id TEXT NOT NULL,
    user_id TEXT,
    method TEXT NOT NULL,
    route TEXT NOT NULL,
    api_type TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_audit_account_created
    ON request_audit(account_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_request_audit_request_id
    ON request_audit(request_id);
CREATE INDEX IF NOT EXISTS idx_request_audit_account_api
    ON request_audit(account_id, api_type);
CREATE INDEX IF NOT EXISTS idx_request_audit_account_status
    ON request_audit(account_id, status_code);
"""

# The SQLite backend is a local pre-GA store with short retention. When the
# schema changes incompatibly, reset all local usage/audit tables instead of
# carrying partial migrations that can leave date/date_utc or daily/hourly
# shapes mixed in one database.
RESET_ON_SCHEMA_UPGRADE_TABLES = (
    "usage_token_daily",
    "usage_retrieval_daily",
    "usage_token_hourly",
    "usage_retrieval_hourly",
    "usage_context_write_bucket",
    "request_audit",
)
