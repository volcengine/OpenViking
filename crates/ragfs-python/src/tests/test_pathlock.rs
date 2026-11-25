use super::*;

#[test]
fn pathlock_failures_remain_internal_through_filesystem_wrappers() {
    Python::initialize();
    Python::attach(|py| {
        let errors_mod = py.import("openviking.storage.errors").unwrap();
        let lock_error_type: Py<PyType> = errors_mod
            .getattr("LockAcquisitionError")
            .unwrap()
            .extract()
            .unwrap();
        let _ = LOCK_ACQUISITION_ERROR_TYPE.set(lock_error_type.clone_ref(py));

        let internal_error_type = get_exception(py, "AGFSInternalError").unwrap();
        for wrapped in [false, true] {
            for failure in [
                PathLockError::Io("failed to create lock dir".to_string()),
                PathLockError::InvalidToken("missing ':' in token".to_string()),
            ] {
                let error = if wrapped {
                    to_py_err(failure.into())
                } else {
                    pathlock_err_to_py(failure)
                };
                assert!(error.is_instance(py, &internal_error_type));
            }
        }
    });
}

#[test]
fn pathlock_contention_survives_filesystem_wrappers() {
    Python::initialize();
    Python::attach(|py| {
        let errors_mod = py.import("openviking.storage.errors").unwrap();
        let lock_error_type: Py<PyType> = errors_mod
            .getattr("LockAcquisitionError")
            .unwrap()
            .extract()
            .unwrap();
        let _ = LOCK_ACQUISITION_ERROR_TYPE.set(lock_error_type.clone_ref(py));

        for wrapped in [false, true] {
            for contention in [
                PathLockError::Busy {
                    lock_path: "/data/.path.ovlock".to_string(),
                    operation: "remove".to_string(),
                },
                PathLockError::Timeout {
                    elapsed_ms: 1000,
                    conflicting_owner: None,
                },
            ] {
                let error = if wrapped {
                    to_py_err(contention.into())
                } else {
                    pathlock_err_to_py(contention)
                };
                assert!(error.is_instance(py, lock_error_type.bind(py)));
            }
        }
    });
}

#[test]
fn constructor_inline_pathlock_overrides_canonical_pathlock() {
    Python::initialize();
    let path = std::env::temp_dir().join(format!(
        "openviking-inline-pathlock-override-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "test-service",
            "storage": {
                "agfs": {
                    "pathlock": {"provider": "cache"}
                }
            }
        }"#,
    )
    .unwrap();

    Python::attach(|py| {
        let ty = py.get_type::<RAGFSBindingClient>();
        let pathlock = PyDict::new(py);
        pathlock.set_item("provider", "filesystem").unwrap();
        let config = PyDict::new(py);
        config.set_item("service_name", "inline-prod").unwrap();
        config.set_item("pathlock", pathlock).unwrap();
        let kwargs = PyDict::new(py);
        kwargs
            .set_item("config_path", path.to_str().unwrap())
            .unwrap();
        kwargs.set_item("config", config).unwrap();

        let instance = ty.call((), Some(&kwargs));
        assert!(instance.is_ok());
    });

    fs::remove_file(path).unwrap();
}

#[test]
fn constructor_inline_redis_pathlock_uses_inline_cache() {
    let Ok(endpoint) = std::env::var("REDIS_URL") else {
        return;
    };
    Python::initialize();
    let path = std::env::temp_dir().join(format!(
        "openviking-inline-redis-pathlock-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{"service_name": "test-service", "storage": {"agfs": {"backend": "local"}}}"#,
    )
    .unwrap();

    Python::attach(|py| {
        let ty = py.get_type::<RAGFSBindingClient>();
        let redis = PyDict::new(py);
        redis.set_item("endpoints", vec![endpoint]).unwrap();
        redis.set_item("command_timeout_ms", 1_000).unwrap();
        let cache = PyDict::new(py);
        cache.set_item("enabled", false).unwrap();
        cache.set_item("runtime_enabled", true).unwrap();
        let instances = PyDict::new(py);
        let default = PyDict::new(py);
        default.set_item("provider", "redis").unwrap();
        default.set_item("redis", redis).unwrap();
        instances.set_item("default", default).unwrap();
        cache.set_item("instances", instances).unwrap();
        let cachefs = PyDict::new(py);
        cachefs.set_item("enabled", false).unwrap();
        cachefs.set_item("instance", "default").unwrap();
        cachefs.set_item("service_name", "inline-prod").unwrap();
        cachefs.set_item("shared_redis_ttl_seconds", 0).unwrap();
        cache.set_item("cachefs", cachefs).unwrap();
        let pathlock = PyDict::new(py);
        pathlock.set_item("provider", "cache").unwrap();
        pathlock.set_item("lock_expire_secs", 3.0).unwrap();
        let config = PyDict::new(py);
        config.set_item("service_name", "inline-prod").unwrap();
        config.set_item("cache", cache).unwrap();
        config.set_item("pathlock", pathlock).unwrap();
        let kwargs = PyDict::new(py);
        kwargs
            .set_item("config_path", path.to_str().unwrap())
            .unwrap();
        kwargs.set_item("config", config).unwrap();

        let instance = ty.call((), Some(&kwargs));
        assert!(instance.is_ok());
    });

    fs::remove_file(path).unwrap();
}

#[test]
fn canonical_redis_pathlock_config_is_parsed_and_enables_runtime() {
    let path = std::env::temp_dir().join(format!(
        "openviking-redis-pathlock-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "prod-a",
            "cache": {
                "instances": {
                    "default": {
                        "provider": "redis",
                        "params": {"endpoints": ["redis://redis:6379"]}
                    }
                }
            },
            "storage": {
                "agfs": {
                    "pathlock": {
                        "provider": "cache",
                        "lock_expire_secs": 60.0
                    }
                }
            }
        }"#,
    )
    .unwrap();

    let pathlock = pathlock_config_from_ov_conf(path.to_str().unwrap(), "prod-a").unwrap();
    let cache = cache_config_from_ov_conf(path.to_str().unwrap(), "prod-a").unwrap();

    assert_eq!(pathlock.provider, "cache");
    assert_eq!(pathlock.service_name.as_deref(), Some("prod-a"));
    assert_eq!(pathlock.lock_expire_secs, 60.0);
    assert!(cache.runtime_enabled);
    assert_eq!(
        cache.instances["default"].redis.endpoints,
        vec!["redis://redis:6379"]
    );

    fs::remove_file(path).unwrap();
}

#[test]
fn redis_pathlock_config_uses_runtime_service_name() {
    let pathlock = pathlock_config_from_canonical_ov_conf(
        &serde_json::json!({
            "service_name": "prod-a",
            "storage": {
                "agfs": {
                    "pathlock": {"provider": "cache"}
                }
            }
        }),
        "prod-a",
    )
    .unwrap();

    assert_eq!(pathlock.service_name.as_deref(), Some("prod-a"));
}

#[test]
fn constructor_sqlite_config_path_resolves_defaults_and_creates_parent() {
    let root = std::env::temp_dir().join(format!(
        "openviking-sqlite-config-path-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&root).unwrap();
    Python::initialize();
    Python::attach(|py| {
        for mode in ["omitted", "null", "explicit"] {
            let workspace = root.join(mode).join("workspace");
            let database = if mode == "explicit" {
                root.join(mode).join("custom").join("locks.db")
            } else {
                workspace.join("_system/pathlock/pathlock.db")
            };
            let mut pathlock = serde_json::json!({"provider": "sqlite"});
            if mode == "null" {
                pathlock["sqlite_path"] = serde_json::Value::Null;
            } else if mode == "explicit" {
                pathlock["sqlite_path"] = serde_json::json!(database);
            }
            let path = root.join(format!("{mode}.json"));
            fs::write(
                &path,
                serde_json::json!({
                    "storage": {
                        "workspace": workspace,
                        "agfs": {"pathlock": pathlock}
                    }
                })
                .to_string(),
            )
            .unwrap();
            assert!(!database.parent().unwrap().exists());
            let config = PyDict::new(py);
            config.set_item("service_name", "local-a").unwrap();
            let kwargs = PyDict::new(py);
            kwargs
                .set_item("config_path", path.to_str().unwrap())
                .unwrap();
            kwargs.set_item("config", config).unwrap();
            let ty = py.get_type::<RAGFSBindingClient>();
            let first = ty.call((), Some(&kwargs)).unwrap();
            let second = ty.call((), Some(&kwargs)).unwrap();
            assert!(database.is_file());
            if mode == "explicit" {
                assert!(!workspace.join("_system/pathlock/pathlock.db").exists());
            }
            let first = first.extract::<PyRef<'_, RAGFSBindingClient>>().unwrap();
            let second = second.extract::<PyRef<'_, RAGFSBindingClient>>().unwrap();
            let lease = first
                .rt
                .block_on(first.pathlock_manager.acquire_tree(
                    "/missing",
                    std::time::Duration::ZERO,
                    None,
                ))
                .unwrap();
            assert!(second
                .rt
                .block_on(second.pathlock_manager.acquire_exact(
                    "/missing/child",
                    std::time::Duration::ZERO,
                    None,
                ))
                .is_err());
            first
                .rt
                .block_on(first.pathlock_manager.release(&lease))
                .unwrap();
            let lease = second
                .rt
                .block_on(second.pathlock_manager.acquire_exact(
                    "/missing/child",
                    std::time::Duration::ZERO,
                    None,
                ))
                .unwrap();
            second
                .rt
                .block_on(second.pathlock_manager.release(&lease))
                .unwrap();
        }
    });
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn canonical_sqlite_default_requires_workspace_and_preserves_explicit_path() {
    for pathlock in [
        serde_json::json!({"provider": "sqlite"}),
        serde_json::json!({"provider": "sqlite", "sqlite_path": null}),
    ] {
        let json = serde_json::json!({
            "storage": {"workspace": "relative/workspace", "agfs": {"pathlock": pathlock}}
        });
        let config = pathlock_config_from_canonical_ov_conf(&json, "local-a").unwrap();
        let expected = std::path::absolute("relative/workspace")
            .unwrap()
            .join("_system/pathlock/pathlock.db");
        assert_eq!(config.sqlite_path.as_deref(), expected.to_str());
        assert_eq!(config.service_name.as_deref(), Some("local-a"));
    }
    for workspace in [
        serde_json::Value::Null,
        serde_json::json!(""),
        serde_json::json!(42),
    ] {
        let json = serde_json::json!({
            "storage": {"workspace": workspace, "agfs": {"pathlock": {"provider": "sqlite"}}}
        });
        let error = pathlock_config_from_canonical_ov_conf(&json, "local-a").unwrap_err();
        assert!(error.contains("storage.workspace"), "{error}");
    }
    let path = std::env::temp_dir().join("explicit-pathlock.db");
    let json = serde_json::json!({
        "storage": {"agfs": {"pathlock": {"provider": "sqlite", "sqlite_path": path}}}
    });
    assert_eq!(
        pathlock_config_from_canonical_ov_conf(&json, "local-a")
            .unwrap()
            .sqlite_path
            .as_deref(),
        path.to_str()
    );
    assert_eq!(
        pathlock_config_from_canonical_ov_conf(&serde_json::json!({}), "local-a")
            .unwrap()
            .provider,
        "filesystem"
    );
}

#[test]
fn sqlite_pathlock_config_parses_shared_path_and_namespace() {
    let path = std::env::temp_dir().join("openviking-locks.sqlite3");
    let config = pathlock_config_from_value(
        &serde_json::json!({"provider": "sqlite", "sqlite_path": path}),
        "local-a",
    )
    .unwrap();
    assert_eq!(config.sqlite_path.as_deref(), path.to_str());
    assert_eq!(config.service_name.as_deref(), Some("local-a"));
    for value in [
        serde_json::json!({"provider": "sqlite"}),
        serde_json::json!({"provider": "sqlite", "sqlite_path": "relative.db"}),
        serde_json::json!({"provider": "sqlite", "sqlite_path": 42}),
    ] {
        assert!(pathlock_config_from_value(&value, "local-a").is_err());
    }
}

#[test]
fn postgres_pathlock_config_parses_environment_and_namespace() {
    let config = pathlock_config_from_value(
        &serde_json::json!({
            "provider": "postgres", "postgres_dsn_env": "OV_POSTGRES_DSN"
        }),
        "community",
    )
    .unwrap();
    assert_eq!(config.postgres_dsn_env.as_deref(), Some("OV_POSTGRES_DSN"));
    assert_eq!(config.service_name.as_deref(), Some("community"));
    for value in [
        serde_json::json!({"provider": "postgres"}),
        serde_json::json!({"provider": "postgres", "postgres_dsn_env": ""}),
        serde_json::json!({"provider": "postgres", "postgres_dsn_env": 42}),
        serde_json::json!({"provider": "postgres", "dsn": "must-not-store-credentials"}),
    ] {
        assert!(pathlock_config_from_value(&value, "community").is_err());
    }
}
