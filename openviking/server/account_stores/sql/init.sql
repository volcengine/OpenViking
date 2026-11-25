-- OpenViking MySQL account store initial schema.
-- Execute manually before starting a MySQL account-store deployment.
-- Table names are fixed and service_name isolates OpenViking deployments.

CREATE TABLE `ov_accounts` (
    account_pk BIGINT NOT NULL AUTO_INCREMENT,
    service_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    account_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    deletion_task_id VARCHAR(64) NULL,
    deletion_owner_account_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    deletion_owner_user_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    PRIMARY KEY (account_pk),
    CONSTRAINT uq_ov_accounts_account_identity UNIQUE (service_name, account_id),
    CONSTRAINT uq_ov_accounts_account_service_pk UNIQUE (service_name, account_pk),
    INDEX ix_ov_accounts_account_deletion (service_name, deletion_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `ov_accounts_users` (
    user_pk BIGINT NOT NULL AUTO_INCREMENT,
    service_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    account_pk BIGINT NOT NULL,
    user_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `role` VARCHAR(16) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    deletion_task_id VARCHAR(64) NULL,
    deletion_owner_account_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    deletion_owner_user_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    PRIMARY KEY (user_pk),
    CONSTRAINT fk_ov_accounts_user_account
        FOREIGN KEY (service_name, account_pk)
        REFERENCES `ov_accounts` (service_name, account_pk)
        ON DELETE CASCADE,
    CONSTRAINT uq_ov_accounts_user_identity
        UNIQUE (service_name, account_pk, user_id),
    CONSTRAINT uq_ov_accounts_user_service_pk UNIQUE (service_name, user_pk),
    CONSTRAINT uq_ov_accounts_user_account_pk
        UNIQUE (service_name, account_pk, user_pk),
    INDEX ix_ov_accounts_user_admin (service_name, account_pk, `role`),
    INDEX ix_ov_accounts_user_deletion (service_name, deletion_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `ov_accounts_groups` (
    group_pk BIGINT NOT NULL AUTO_INCREMENT,
    service_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    account_pk BIGINT NOT NULL,
    group_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (group_pk),
    CONSTRAINT fk_ov_accounts_group_account
        FOREIGN KEY (service_name, account_pk)
        REFERENCES `ov_accounts` (service_name, account_pk)
        ON DELETE CASCADE,
    CONSTRAINT uq_ov_accounts_group_identity
        UNIQUE (service_name, account_pk, group_id),
    CONSTRAINT uq_ov_accounts_group_service_pk UNIQUE (service_name, group_pk),
    CONSTRAINT uq_ov_accounts_group_account_pk
        UNIQUE (service_name, account_pk, group_pk)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `ov_accounts_group_members` (
    service_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    account_pk BIGINT NOT NULL,
    group_pk BIGINT NOT NULL,
    user_pk BIGINT NOT NULL,
    PRIMARY KEY (service_name, account_pk, group_pk, user_pk),
    CONSTRAINT fk_ov_accounts_member_group
        FOREIGN KEY (service_name, account_pk, group_pk)
        REFERENCES `ov_accounts_groups` (service_name, account_pk, group_pk)
        ON DELETE CASCADE,
    CONSTRAINT fk_ov_accounts_member_user
        FOREIGN KEY (service_name, account_pk, user_pk)
        REFERENCES `ov_accounts_users` (service_name, account_pk, user_pk)
        ON DELETE CASCADE,
    INDEX ix_ov_accounts_member_user (service_name, account_pk, user_pk)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `ov_accounts_credentials` (
    credential_id VARCHAR(32) COLLATE ascii_bin NOT NULL,
    service_name VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    user_pk BIGINT NOT NULL,
    material TEXT COLLATE ascii_bin NOT NULL,
    lookup_digest VARCHAR(64) COLLATE ascii_bin NOT NULL,
    prefix VARCHAR(8) COLLATE ascii_bin NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 1,
    sequence BIGINT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    revoked_at DATETIME(6) NULL,
    active_user_pk BIGINT NULL,
    PRIMARY KEY (credential_id),
    CONSTRAINT fk_ov_accounts_credential_user
        FOREIGN KEY (service_name, user_pk)
        REFERENCES `ov_accounts_users` (service_name, user_pk)
        ON DELETE CASCADE,
    CONSTRAINT uq_ov_accounts_active_credential
        UNIQUE (service_name, active_user_pk),
    INDEX ix_ov_accounts_credential_lookup
        (service_name, lookup_digest, revoked, user_pk),
    INDEX ix_ov_accounts_credential_history
        (service_name, user_pk, revoked, sequence)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
