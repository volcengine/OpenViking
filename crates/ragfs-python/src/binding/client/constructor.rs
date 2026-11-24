#[pymethods]
impl RAGFSBindingClient {
    /// Create a new RAGFS binding client.
    ///
    /// `config_path` loads the canonical ov.conf schema. `config.service_name` must be supplied
    /// by the Python runtime.
    /// `config` is an optional sectioned dict produced by the Python runtime. The `encryption`
    /// section, when present, carries `root_key` (32 bytes) + `provider_type` (int) and causes the
    /// stack to include an `EncryptionWrappedFS` layer. Runtime `cache` configuration takes
    /// precedence over `config_path`.
    #[new]
    #[pyo3(signature = (config_path=None, config=None, git_config_path=None))]
    fn new(
        py: Python<'_>,
        config_path: Option<&str>,
        config: Option<HashMap<String, Py<PyAny>>>,
        git_config_path: Option<&str>,
    ) -> PyResult<Self> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create runtime: {}", e)))?;

        // Phase A (holding GIL): parse the sectioned config into an owned RagfsConfig.
        let mut ragfs_cfg = RagfsConfig::default();
        let service_name = match config.as_ref() {
            Some(cfg) => Some(
                cfg.get("service_name")
                    .ok_or_else(|| PyValueError::new_err("service_name is required"))?
                    .extract::<String>(py)?,
            ),
            None => None,
        };
        let coordination_value = config
            .as_ref()
            .and_then(|cfg| cfg.get("coordination"))
            .map(|value| py_to_json_value(value.bind(py)))
            .transpose()?;
        let mut coordination_value = match coordination_value {
            Some(value) => value,
            None => {
                let from_file = config_path
                    .map(|path| -> PyResult<Option<serde_json::Value>> {
                        let contents = fs::read_to_string(path)
                            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
                        let value: serde_json::Value = serde_json::from_str(&contents)
                            .map_err(|e| PyValueError::new_err(e.to_string()))?;
                        let mut coordination = value
                            .pointer("/storage/agfs/coordination")
                            .cloned()
                            .unwrap_or_else(|| serde_json::json!({}));
                        if let Some(config) = coordination.as_object_mut() {
                            let legacy_provider = value
                                .get("watch_leader_provider")
                                .and_then(serde_json::Value::as_str)
                                .unwrap_or("auto");
                            let watch_store = value
                                .pointer("/storage/watch_store/provider")
                                .and_then(serde_json::Value::as_str)
                                .unwrap_or("file");
                            let provider = if legacy_provider == "cache"
                                || (legacy_provider == "auto" && watch_store != "file")
                            {
                                "cache"
                            } else {
                                "memory"
                            };
                            config
                                .entry("provider")
                                .or_insert_with(|| serde_json::json!(provider));
                        }
                        Ok(Some(coordination))
                    })
                    .transpose()?
                    .flatten();
                from_file.unwrap_or_else(|| serde_json::json!({"provider": "memory"}))
            }
        };
        if let Some(value) = coordination_value.as_object_mut() {
            value
                .entry("provider")
                .or_insert_with(|| serde_json::json!("memory"));
        }
        let coordination_config: CoordinationConfig = serde_json::from_value(coordination_value)
            .map_err(|e| PyValueError::new_err(format!("Invalid coordination config: {e}")))?;
        let coordination_uses_cache =
            matches!(coordination_config, CoordinationConfig::Cache { .. });
        let inline_pathlock_configured = config
            .as_ref()
            .is_some_and(|config| config.contains_key("pathlock"));
        if !inline_pathlock_configured {
            if let Some(path) = config_path {
                let service_name = service_name
                    .as_deref()
                    .ok_or_else(|| PyValueError::new_err("service_name is required"))?;
                ragfs_cfg.pathlock =
                    pathlock_config_from_ov_conf(path, service_name).map_err(|error| {
                        PyRuntimeError::new_err(format!("Invalid PathLock config: {error}"))
                    })?;
            }
        }
        let mut runtime_cache_config = None;
        let mut inline_git_cfg: Option<ragfs::git::GitConfig> = None;
        if let Some(cfg) = config {
            let service_name = service_name
                .as_deref()
                .ok_or_else(|| PyValueError::new_err("service_name is required"))?;
            if let Some(log_obj) = cfg.get("log") {
                let log_cfg: HashMap<String, Py<PyAny>> = log_obj.extract(py)?;
                let log_level = log_cfg
                    .get("level")
                    .map(|value| value.extract::<String>(py))
                    .transpose()?
                    .unwrap_or_else(|| "INFO".to_string());
                let log_output = log_cfg
                    .get("output")
                    .map(|value| value.extract::<String>(py))
                    .transpose()?
                    .unwrap_or_else(|| "stderr".to_string());
                if let Some(format) = log_cfg.get("format") {
                    let format: String = format.extract(py)?;
                    if format != "%(asctime)s %(levelname)s %(name)s pid=%(process)d request_id=%(request_id)s trace_id=%(trace_id)s message=%(message)s" {
                        return Err(PyValueError::new_err("Rust logging requires the common cross-runtime format"));
                    }
                }
                init_tracing(&log_level, &log_output)?;
            }
            if let Some(enc_obj) = cfg.get("encryption") {
                let enc: HashMap<String, Py<PyAny>> = enc_obj.extract(py)?;
                let rk: Vec<u8> = enc
                    .get("root_key")
                    .ok_or_else(|| PyValueError::new_err("encryption.root_key required"))?
                    .extract(py)?;
                let root_key: [u8; 32] = rk
                    .as_slice()
                    .try_into()
                    .map_err(|_| PyValueError::new_err("root_key must be 32 bytes"))?;
                let provider_type: u8 = enc
                    .get("provider_type")
                    .ok_or_else(|| PyValueError::new_err("encryption.provider_type required"))?
                    .extract(py)?;
                ragfs_cfg.encryption = Some(EncryptionConfig {
                    root_key,
                    provider_type,
                });
            }
            if let Some(cache_obj) = cfg.get("cache") {
                let cache_value = py_to_json_value(cache_obj.bind(py))?;
                runtime_cache_config = Some(
                    cache_config_from_value(&cache_value, service_name).map_err(|error| {
                        PyRuntimeError::new_err(format!("Invalid cache config: {error}"))
                    })?,
                );
            }
            if let Some(git_obj) = cfg.get("git") {
                let git_value = py_to_json_value(git_obj.bind(py))?;
                let git_cfg: ragfs::git::GitConfig = serde_json::from_value(git_value)
                    .map_err(|e| PyRuntimeError::new_err(format!("Invalid git config: {e}")))?;
                inline_git_cfg = Some(git_cfg);
            }
            if let Some(pl_obj) = cfg.get("pathlock") {
                let pl_value = py_to_json_value(pl_obj.bind(py))?;
                ragfs_cfg.pathlock = pathlock_config_from_value(&pl_value, service_name)
                    .map_err(PyValueError::new_err)?;
            }
        }

        let inline_cache_configured = runtime_cache_config.is_some();
        let pathlock_uses_cache = ragfs_cfg.pathlock.provider == "cache";
        let file_cache_config = config_path
            .map(|path| {
                let service_name = service_name
                    .as_deref()
                    .ok_or_else(|| "service_name is required".to_string())?;
                if inline_cache_configured {
                    cache_config_from_ov_conf_with_runtime(path, false, service_name)
                } else if coordination_uses_cache {
                    cache_config_from_ov_conf_with_runtime(path, true, service_name)
                } else if inline_pathlock_configured {
                    cache_config_from_ov_conf_with_runtime(path, pathlock_uses_cache, service_name)
                } else {
                    cache_config_from_ov_conf(path, service_name)
                }
            })
            .transpose()
            .map_err(|error| PyRuntimeError::new_err(format!("Invalid cache config: {error}")))?;
        let mut cache_config = runtime_cache_config
            .or(file_cache_config)
            .unwrap_or_default();
        if coordination_uses_cache && inline_cache_configured {
            cache_config.runtime_enabled = true;
        }
        if coordination_uses_cache && !cache_config.runtime_enabled {
            return Err(PyValueError::new_err(
                "top-level cache config is required for cache coordination",
            ));
        }
        if pathlock_uses_cache {
            let pathlock_instance = &ragfs_cfg.pathlock.instance;
            if !matches!(
                cache_config
                    .instances
                    .get(pathlock_instance)
                    .map(|config| &config.provider),
                Some(CacheProviderKind::Redis)
            ) {
                return Err(PyValueError::new_err(
                    format!(
                        "cache-backed PathLock requires cache instance '{pathlock_instance}' to use redis"
                    ),
                ));
            }
            if inline_cache_configured {
                cache_config.runtime_enabled = true;
            }
            if !cache_config.runtime_enabled {
                return Err(PyValueError::new_err(
                    "top-level cache config is required for cache-backed PathLock",
                ));
            }
        }

        // Phase B: RAGFS owns Runtime construction and injects the same instance
        // into CacheFS and QueueFS. The binding only translates configuration.
        let stack = rt
            .block_on(build_configured_stack(
                ragfs_cfg,
                cache_config.stack_config(),
            ))
            .map_err(|error| {
                if matches!(error, ragfs::core::Error::NotSupported(_)) {
                    to_py_err(error)
                } else {
                    PyRuntimeError::new_err(format!("Failed to build RAGFS stack: {error}"))
                }
            })?;

        // Build the git service from inline config when present; otherwise fall
        // back to loading the [git] section from a config file path.
        let (git_service, git_backend) = match inline_git_cfg {
            Some(git_cfg) => build_git_from_cfg(git_cfg, &stack.mountable, &rt)?,
            None => match git_config_path {
                Some(path) => load_git_from_config(path, &stack.mountable, &rt)?,
                None => (None, None),
            },
        };

        let mut write_hooks = WriteHookRegistry::new();
        ragfs::global_policy::register_write_hooks(&mut write_hooks);

        let coordination = Arc::new(
            CoordinationRuntime::new(
                &stack.cache_runtimes,
                coordination_config,
                service_name.unwrap_or_else(|| "ragfs".to_string()),
            )
            .map_err(|e| PyValueError::new_err(e.to_string()))?,
        );
        Ok(Self {
            mountable: stack.mountable,
            top: stack.top,
            rt,
            git_service,
            git_backend,
            pathlock_manager: stack.pathlock_manager,
            cache_runtimes: stack.cache_runtimes,
            coordination,
            write_hooks,
        })
    }

}
