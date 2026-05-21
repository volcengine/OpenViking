# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite schema for product usage/audit projections."""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_token_daily (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    token_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        account_id, user_id, agent_id, date, source, token_type, provider, model_name
    )
);
CREATE INDEX IF NOT EXISTS idx_usage_token_account_date
    ON usage_token_daily(account_id, date);

CREATE TABLE IF NOT EXISTS usage_retrieval_daily (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    date TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, user_id, agent_id, date, operation, status)
);
CREATE INDEX IF NOT EXISTS idx_usage_retrieval_account_date
    ON usage_retrieval_daily(account_id, date);

CREATE TABLE IF NOT EXISTS usage_context_write_bucket (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    date TEXT NOT NULL,
    hour_bucket INTEGER NOT NULL,
    operation TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, user_id, agent_id, date, hour_bucket, operation)
);
CREATE INDEX IF NOT EXISTS idx_usage_context_write_account_date
    ON usage_context_write_bucket(account_id, date, hour_bucket);

CREATE TABLE IF NOT EXISTS usage_agent_activity_daily (
    account_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    date TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, agent_id, date)
);
CREATE INDEX IF NOT EXISTS idx_usage_agent_activity_account_date
    ON usage_agent_activity_daily(account_id, date, last_seen_at);

CREATE TABLE IF NOT EXISTS request_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    account_id TEXT NOT NULL,
    user_id TEXT,
    agent_id TEXT,
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
