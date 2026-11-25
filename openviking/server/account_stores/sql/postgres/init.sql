-- Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
-- SPDX-License-Identifier: AGPL-3.0
-- PostgreSQL Account Store, provisioned by a migration/administrator role.
-- Run once in the database used by account_store.params.database.
-- No extension is required. Runtime startup never creates or migrates tables.
BEGIN;

CREATE TABLE ov_accounts (
    account_pk BIGSERIAL PRIMARY KEY,
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    account_id VARCHAR(255) COLLATE "C" NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
    deletion_task_id VARCHAR(64),
    deletion_owner_account_id VARCHAR(255) COLLATE "C",
    deletion_owner_user_id VARCHAR(255) COLLATE "C",
    CONSTRAINT uq_ov_accounts_account_identity UNIQUE (service_name, account_id),
    CONSTRAINT uq_ov_accounts_account_service_pk UNIQUE (service_name, account_pk)
);
CREATE INDEX ix_ov_accounts_account_created ON ov_accounts (service_name, created_at, account_id);
CREATE INDEX ix_ov_accounts_account_deletion ON ov_accounts (service_name, deletion_task_id);

CREATE TABLE ov_accounts_users (
    user_pk BIGSERIAL PRIMARY KEY,
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    account_pk BIGINT NOT NULL,
    user_id VARCHAR(255) COLLATE "C" NOT NULL,
    role VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
    deletion_task_id VARCHAR(64),
    deletion_owner_account_id VARCHAR(255) COLLATE "C",
    deletion_owner_user_id VARCHAR(255) COLLATE "C",
    CONSTRAINT fk_ov_accounts_user_account FOREIGN KEY (service_name, account_pk)
        REFERENCES ov_accounts (service_name, account_pk) ON DELETE CASCADE,
    CONSTRAINT uq_ov_accounts_user_identity UNIQUE (service_name, account_pk, user_id),
    CONSTRAINT uq_ov_accounts_user_service_pk UNIQUE (service_name, user_pk),
    CONSTRAINT uq_ov_accounts_user_account_pk UNIQUE (service_name, account_pk, user_pk)
);
CREATE INDEX ix_ov_accounts_user_admin ON ov_accounts_users (service_name, account_pk, role);
CREATE INDEX ix_ov_accounts_user_deletion ON ov_accounts_users (service_name, deletion_task_id);
CREATE INDEX ix_ov_accounts_user_active_created
    ON ov_accounts_users (service_name, account_pk, deletion_task_id, created_at, user_pk);
CREATE INDEX ix_ov_accounts_user_role_active_created
    ON ov_accounts_users (service_name, account_pk, role, deletion_task_id, created_at, user_pk);

CREATE TABLE ov_accounts_groups (
    group_pk BIGSERIAL PRIMARY KEY,
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    account_pk BIGINT NOT NULL,
    group_id VARCHAR(255) COLLATE "C" NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
    CONSTRAINT fk_ov_accounts_group_account FOREIGN KEY (service_name, account_pk)
        REFERENCES ov_accounts (service_name, account_pk) ON DELETE CASCADE,
    CONSTRAINT uq_ov_accounts_group_identity UNIQUE (service_name, account_pk, group_id),
    CONSTRAINT uq_ov_accounts_group_service_pk UNIQUE (service_name, group_pk),
    CONSTRAINT uq_ov_accounts_group_account_pk UNIQUE (service_name, account_pk, group_pk)
);

CREATE TABLE ov_accounts_group_members (
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    account_pk BIGINT NOT NULL,
    group_pk BIGINT NOT NULL,
    user_pk BIGINT NOT NULL,
    PRIMARY KEY (service_name, account_pk, group_pk, user_pk),
    CONSTRAINT fk_ov_accounts_member_group FOREIGN KEY (service_name, account_pk, group_pk)
        REFERENCES ov_accounts_groups (service_name, account_pk, group_pk) ON DELETE CASCADE,
    CONSTRAINT fk_ov_accounts_member_user FOREIGN KEY (service_name, account_pk, user_pk)
        REFERENCES ov_accounts_users (service_name, account_pk, user_pk) ON DELETE CASCADE
);
CREATE INDEX ix_ov_accounts_member_user
    ON ov_accounts_group_members (service_name, account_pk, user_pk);

CREATE TABLE ov_accounts_credentials (
    credential_id VARCHAR(32) COLLATE "C" PRIMARY KEY,
    service_name VARCHAR(255) COLLATE "C" NOT NULL,
    user_pk BIGINT NOT NULL,
    material TEXT COLLATE "C" NOT NULL,
    lookup_digest VARCHAR(64) COLLATE "C" NOT NULL,
    prefix VARCHAR(8) COLLATE "C" NOT NULL,
    revoked BOOLEAN DEFAULT false NOT NULL,
    version BIGINT DEFAULT 1 NOT NULL,
    sequence BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
    revoked_at TIMESTAMPTZ,
    active_user_pk BIGINT,
    CONSTRAINT fk_ov_accounts_credential_user FOREIGN KEY (service_name, user_pk)
        REFERENCES ov_accounts_users (service_name, user_pk) ON DELETE CASCADE,
    CONSTRAINT uq_ov_accounts_active_credential UNIQUE (service_name, active_user_pk)
);
CREATE INDEX ix_ov_accounts_credential_history
    ON ov_accounts_credentials (service_name, user_pk, revoked, sequence);
CREATE INDEX ix_ov_accounts_credential_lookup
    ON ov_accounts_credentials (service_name, lookup_digest, revoked, user_pk);

COMMIT;
