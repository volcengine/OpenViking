-- Provision this table before selecting storage.compile_store.provider=mysql.
-- MySQL 5.7.44: use InnoDB DYNAMIC and large prefixes (16 KiB pages).
-- For storage.compile_store.params.table, replace the ov_compile prefix.
-- The prefix must be an ASCII SQL identifier of at most 51 characters.
CREATE TABLE `ov_compile_configs` (
    config_id BIGINT NOT NULL AUTO_INCREMENT,
    service_name VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    account_id VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner_user_id VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    compile_name VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    updated_by VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    source_uris JSON NOT NULL,
    target_uri TEXT NOT NULL,
    skill_uri TEXT NOT NULL,
    instruction LONGTEXT NULL,
    args JSON NOT NULL,
    auto_update BOOLEAN NOT NULL,
    schedule_frequency ENUM('daily', 'weekly') NULL,
    schedule_hour TINYINT UNSIGNED NULL,
    schedule_minute TINYINT UNSIGNED NULL,
    schedule_weekday TINYINT UNSIGNED NULL,
    task_first_create_time DOUBLE NOT NULL,
    last_run_time DOUBLE NULL,
    last_triggered_slot VARCHAR(40) NULL,
    next_triggered_slot VARCHAR(40) NULL,
    progress JSON NOT NULL,
    execution_context JSON NOT NULL,
    PRIMARY KEY (config_id),
    UNIQUE KEY uq_compile_configs_name (service_name, account_id, compile_name, owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC;
