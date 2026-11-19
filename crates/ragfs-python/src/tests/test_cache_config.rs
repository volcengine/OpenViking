use super::*;

#[test]
fn missing_cache_config_defaults_to_disabled_runtime_map() {
    let path = std::env::temp_dir().join(format!(
        "openviking-no-cache-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{"service_name": "test-service", "storage": {"agfs": {"backend": "local"}}}"#,
    )
    .unwrap();

    let cache_config =
        cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap();

    assert!(!cache_config.enabled);
    assert!(cache_config.instances.is_empty());
    assert_eq!(cache_config.service_name, "test-service");

    fs::remove_file(path).unwrap();
}

#[test]
fn canonical_cache_config_is_parsed_from_ov_conf() {
    let path = std::env::temp_dir().join(format!(
        "openviking-canonical-cache-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "tenant-a",
            "cache": {
                "instances": {
                    "default": {
                        "provider": "redis",
                        "params": {
                            "mode": "sentinel",
                            "endpoints": ["redis://sentinel:26379"],
                            "master_name": "mymaster",
                            "command_timeout_ms": 750
                        }
                    }
                }
            },
            "storage": {
                "agfs": {
                    "cachefs": {"backend": "cache"},
                    "queuefs": {"backend": "cache"}
                }
            }
        }"#,
    )
    .unwrap();

    let cache_config = cache_config_from_ov_conf(path.to_str().unwrap(), "tenant-a").unwrap();

    assert!(cache_config.enabled);
    assert!(cache_config.runtime_enabled);
    assert_eq!(cache_config.service_name, "tenant-a");
    assert_eq!(cache_config.instances["default"].redis.mode, "sentinel");
    assert_eq!(
        cache_config.instances["default"].redis.master_name.as_deref(),
        Some("mymaster")
    );
    assert_eq!(
        cache_config.instances["default"].redis.command_timeout_ms,
        750
    );

    fs::remove_file(path).unwrap();
}

#[test]
fn binding_cache_config_carries_the_shared_redis_ttl() {
    let config = serde_json::json!({
        "enabled": true,
        "runtime_enabled": true,
        "instances": {
            "default": {
                "provider": "redis",
                "redis": {"endpoints": ["redis://127.0.0.1:6379"]}
            }
        },
        "cachefs": {
            "enabled": true,
            "instance": "default",
            "service_name": "runtime-prod",
            "shared_redis_ttl_seconds": 600
        }
    });

    let cache_config = cache_config_from_value(&config, "runtime-prod").unwrap();

    assert_eq!(cache_config.shared_redis_ttl_seconds, 600);
    let stack_config = cache_config.stack_config().unwrap();
    assert_eq!(
        stack_config.cachefs.shared_redis_ttl,
        Some(Duration::from_secs(600))
    );
}

#[test]
fn canonical_cache_instances_accept_omitted_params() {
    let cache = serde_json::json!({
        "instances": {
            "default": {
                "provider": "redis"
            }
        }
    });

    let instances = parse_canonical_cache_instances(cache.as_object().unwrap()).unwrap();
    assert_eq!(
        instances["default"].redis.endpoints,
        RedisCacheConfig::default().endpoints
    );
}

/// Accept canonical provider values normalized by the Python config layer.
#[test]
fn canonical_cache_provider_name_is_trimmed() {
    let cache = serde_json::json!({
        "instances": {
            "default": {
                "provider": " redis "
            }
        }
    });

    let instances = parse_canonical_cache_instances(cache.as_object().unwrap()).unwrap();

    assert!(matches!(
        instances["default"].provider,
        CacheProviderKind::Redis
    ));
}

/// Avoid opening configured cache providers until a consumer selects one.
#[test]
fn canonical_unused_cache_instances_do_not_enable_runtime() {
    let config = cache_config_from_canonical_ov_conf(
        &serde_json::json!({
            "cache": {
                "instances": {
                    "default": {
                        "provider": "redis"
                    }
                }
            }
        }),
        "tenant-a",
    )
    .unwrap();

    assert!(!config.enabled);
    assert!(!config.runtime_enabled);
    assert!(config.stack_config().is_none());
}

#[test]
fn binding_cache_config_without_a_shared_redis_ttl_keeps_the_default() {
    let config = serde_json::json!({
        "enabled": true,
        "runtime_enabled": true,
        "instances": {
            "default": {
                "provider": "redis",
                "redis": {"endpoints": ["redis://127.0.0.1:6379"]}
            }
        },
        "cachefs": {
            "enabled": true,
            "instance": "default",
            "service_name": "runtime-prod",
            "shared_redis_ttl_seconds": 0
        }
    });

    let cache_config = cache_config_from_value(&config, "runtime-prod").unwrap();

    assert_eq!(cache_config.shared_redis_ttl_seconds, 0);
    let stack_config = cache_config.stack_config().unwrap();
    assert_eq!(stack_config.cachefs.shared_redis_ttl, None);
}

#[test]
fn binding_cache_config_rejects_a_negative_shared_redis_ttl() {
    let config = serde_json::json!({
        "enabled": false,
        "runtime_enabled": false,
        "instances": {},
        "cachefs": {
            "enabled": false,
            "instance": "default",
            "service_name": "runtime-prod",
            "shared_redis_ttl_seconds": -1
        }
    });

    let error = cache_config_from_value(&config, "runtime-prod").unwrap_err();

    assert!(error.contains("shared_redis_ttl_seconds"));
}

#[test]
fn config_path_rejects_removed_nested_cache_schema() {
    let path = std::env::temp_dir().join(format!(
        "openviking-removed-cache-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{"service_name":"test-service","storage":{"agfs":{"cache":{"enabled":true}}}}"#,
    )
    .unwrap();

    let error = cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap_err();

    assert!(error.contains("storage.agfs.cache has been removed"));
    assert!(error.contains("cache.instances"));
    assert!(error.contains("storage.agfs.cachefs.backend"));
    fs::remove_file(path).unwrap();
}

#[test]
fn config_path_rejects_removed_queuefs_redis_schema() {
    let path = std::env::temp_dir().join(format!(
        "openviking-removed-queue-redis-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{"storage":{"agfs":{"queuefs":{"backend":"redis"}}}}"#,
    )
    .unwrap();

    let error = cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap_err();

    assert!(error.contains("queuefs backend=redis has been removed"));
    assert!(error.contains("backend=cache"));
    assert!(error.contains("cache.instances"));
    fs::remove_file(path).unwrap();
}

#[test]
fn queuefs_cache_runtime_does_not_enable_cachefs() {
    let path = std::env::temp_dir().join(format!(
        "openviking-queue-cache-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "test-service",
            "cache": {
                "instances": {
                    "default": {"provider": "redis", "params": {}}
                }
            },
            "storage": {
                "agfs": {
                    "queuefs": {"backend": "cache"}
                }
            }
        }"#,
    )
    .unwrap();

    let cache_config =
        cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap();
    let stack_config = cache_config.stack_config().unwrap();

    assert!(cache_config.runtime_enabled);
    assert!(!stack_config.cachefs.enabled);

    fs::remove_file(path).unwrap();
}

#[test]
fn queuefs_cache_runtime_rejects_redis_provider_key_prefix() {
    let path = std::env::temp_dir().join(format!(
        "openviking-queue-cache-prefix-config-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "test-service",
            "cache": {
                "instances": {
                    "default": {
                        "provider": "redis",
                        "params": {"key_prefix": "provider-prefix"}
                    }
                }
            },
            "storage": {
                "agfs": {
                    "queuefs": {"backend": "cache"}
                }
            }
        }"#,
    )
    .unwrap();

    let error = cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap_err();

    assert!(error.contains("key_prefix"));

    fs::remove_file(path).unwrap();
}

#[test]
fn queuefs_cache_runtime_rejects_unsupported_provider_name() {
    let path = std::env::temp_dir().join(format!(
        "openviking-queue-cache-legacy-provider-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "test-service",
            "cache": {
                "instances": {
                    "default": {"provider": "mooncake", "params": {}}
                }
            },
            "storage": {
                "agfs": {
                    "queuefs": {"backend": "cache"}
                }
            }
        }"#,
    )
    .unwrap();

    let error = cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap_err();

    assert!(error.contains("mooncake"));

    fs::remove_file(path).unwrap();
}

#[test]
fn binding_cache_rejects_removed_single_provider_schema() {
    let config = serde_json::json!({"provider": "redis"});

    let error = cache_config_from_value(&config, "runtime-prod").unwrap_err();

    assert!(error.contains("provider"));
}

#[test]
fn constructor_runtime_cache_overrides_config_path() {
    Python::initialize();
    let path = std::env::temp_dir().join(format!(
        "openviking-runtime-cache-override-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{
            "service_name": "test-service",
            "cache": {
                "instances": {
                    "default": {"provider": "redis", "params": {}}
                }
            },
            "storage": {"agfs": {"cachefs": {"backend": "cache"}}}
        }"#,
    )
    .unwrap();

    Python::attach(|py| {
        let ty = py.get_type::<RAGFSBindingClient>();
        let cache = serde_json_to_py(
            py,
            &serde_json::json!({
                "enabled": false,
                "runtime_enabled": false,
                "instances": {},
                "cachefs": {
                    "enabled": false,
                    "instance": "default",
                    "service_name": "runtime-cache",
                    "shared_redis_ttl_seconds": 0
                }
            }),
        )
        .unwrap();
        let config = PyDict::new(py);
        config.set_item("service_name", "runtime-cache").unwrap();
        config.set_item("cache", cache).unwrap();
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
fn constructor_rejects_removed_config_path_schema_with_runtime_config() {
    Python::initialize();
    let path = std::env::temp_dir().join(format!(
        "openviking-removed-runtime-cache-override-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{"service_name": "test-service", "storage": {"agfs": {"cache": {"enabled": false}}}}"#,
    )
    .unwrap();

    Python::attach(|py| {
        let ty = py.get_type::<RAGFSBindingClient>();
        let cache = serde_json_to_py(
            py,
            &serde_json::json!({
                "enabled": false,
                "runtime_enabled": false,
                "instances": {},
                "cachefs": {
                    "enabled": false,
                    "instance": "default",
                    "service_name": "runtime-cache",
                    "shared_redis_ttl_seconds": 0
                }
            }),
        )
        .unwrap();
        let config = PyDict::new(py);
        config.set_item("service_name", "runtime-cache").unwrap();
        config.set_item("cache", cache).unwrap();
        let kwargs = PyDict::new(py);
        kwargs
            .set_item("config_path", path.to_str().unwrap())
            .unwrap();
        kwargs.set_item("config", config).unwrap();

        let error = ty.call((), Some(&kwargs)).unwrap_err().to_string();
        assert!(error.contains("storage.agfs.cache has been removed"));
    });

    fs::remove_file(path).unwrap();
}

#[test]
fn canonical_config_rejects_removed_redis_fields() {
    for field in ["key_prefix", "read_from_replica", "tls_enabled"] {
        let path = std::env::temp_dir().join(format!(
            "openviking-removed-redis-field-{field}-{}.json",
            std::process::id()
        ));
        fs::write(
            &path,
            format!(
                r#"{{
                    "service_name": "test-service",
                    "cache": {{
                        "instances": {{
                            "default": {{
                                "provider": "redis",
                                "params": {{"{field}": true}}
                            }}
                        }}
                    }},
                    "storage": {{"agfs": {{"cachefs": {{"backend": "cache"}}}}}}
                }}"#
            ),
        )
        .unwrap();

        let error =
            cache_config_from_ov_conf(path.to_str().unwrap(), "test-service").unwrap_err();

        assert!(error.contains(field));
        fs::remove_file(path).unwrap();
    }
}

#[test]
fn dynamic_cache_debug_output_redacts_provider_params() {
    let config = DynamicCacheConfig {
        library: "/opt/openviking/libprovider.so".to_string(),
        params: serde_json::json!({"password": "binding-secret"}),
    };

    let output = format!("{config:?}");

    assert!(output.contains("/opt/openviking/libprovider.so"));
    assert!(!output.contains("binding-secret"));
    assert!(output.contains("<redacted>"));
}
