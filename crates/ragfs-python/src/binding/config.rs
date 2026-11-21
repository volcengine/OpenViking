use std::fmt;
use std::fs;
use std::future::Future;
use std::time::Duration;

/// Validate and convert a wait timeout from Python.
///
/// Must be finite and non-negative. Returns `Duration::from_secs_f64(v)`.
/// NaN, negative, or infinite → `ValueError`.
fn validate_timeout_secs(v: f64) -> PyResult<Duration> {
    if v.is_finite() && v >= 0.0 {
        Ok(Duration::from_secs_f64(v))
    } else {
        Err(PyValueError::new_err(
            "timeout_secs must be a finite non-negative number",
        ))
    }
}

/// Validate a positive leader lease TTL from Python.
fn validate_leader_ttl_secs(v: f64) -> PyResult<Duration> {
    let max_ttl_secs = (i64::MAX as f64) / 1000.0;
    if v.is_finite() && (0.001..=max_ttl_secs).contains(&v) {
        Ok(Duration::from_secs_f64(v))
    } else {
        Err(PyValueError::new_err(
            "ttl_secs must be a finite number between 0.001 and i64::MAX milliseconds",
        ))
    }
}

use ragfs::cache_runtime::{CacheRuntime, DynamicProviderConfig, RedisProviderConfig};
use ragfs::coordination::{CoordinationConfig, CoordinationRuntime};
use ragfs::core::builder::{
    CacheFsConfig, CacheRuntimeProviderConfig, CacheStackConfig, EncryptionConfig,
};
use ragfs::core::{
    build_configured_stack, ConfigValue, FileInfo, FileSystem, FilesystemStats, FsContext,
    FsContextInner, FsOperation, GlobPage, GrepOptions, GrepResult, ListSortBy, MountableFS,
    OperationStats, PathLockContext, PluginConfig, RagfsConfig, SortOrder, TreeEntry, WriteFlag,
    FS_CTX,
};
use ragfs::lock::types::PathLockError;
use ragfs::lock::{
    BorrowedPathLockLease, OwnedPathLockLease, PathLockConfig, PathLockHandleMode,
    PathLockHandoffRef, PathLockKind, PathLockManager, PathLockRequest,
};
use ragfs::metrics::{RagfsMetric, RagfsMetricValue};
use ragfs::write_hook::WriteHookRegistry;

/// Parse an optional listing sort field and return the matching RagFS value.
fn parse_list_sort_by(value: Option<&str>) -> PyResult<Option<ListSortBy>> {
    match value {
        None => Ok(None),
        Some("name") => Ok(Some(ListSortBy::Name)),
        Some("mtime") => Ok(Some(ListSortBy::Mtime)),
        Some(value) => Err(PyValueError::new_err(format!(
            "sort_by must be 'name' or 'mtime', got {value:?}"
        ))),
    }
}

/// Parse an optional listing sort direction and return the matching RagFS value.
fn parse_sort_order(value: Option<&str>) -> PyResult<Option<SortOrder>> {
    match value {
        None | Some("asc") => Ok(Some(SortOrder::Asc)),
        Some("desc") => Ok(Some(SortOrder::Desc)),
        Some(value) => Err(PyValueError::new_err(format!(
            "sort_order must be 'asc' or 'desc', got {value:?}"
        ))),
    }
}

/// Validate `offset` and return it as an unsigned RagFS offset.
fn parse_listing_offset(offset: i64) -> PyResult<usize> {
    usize::try_from(offset).map_err(|_| PyValueError::new_err("offset must be non-negative"))
}

/// Validate `limit` and return it as an optional unsigned RagFS limit.
fn parse_listing_limit(limit: Option<i64>) -> PyResult<Option<usize>> {
    match limit {
        None => Ok(None),
        Some(value) if value > 0 => Ok(Some(value as usize)),
        Some(_) => Err(PyValueError::new_err("limit must be positive")),
    }
}

/// Parse one Python pathlock request without silently defaulting invalid fields.
fn parse_pathlock_request(raw: &HashMap<String, String>) -> PyResult<PathLockRequest> {
    let path = raw
        .get("path")
        .filter(|path| !path.is_empty() && path.starts_with('/'))
        .ok_or_else(|| PyValueError::new_err("pathlock request.path must be an absolute path"))?;
    let kind = match raw.get("kind").map(String::as_str) {
        Some("exact") => PathLockKind::Exact,
        Some("tree") => PathLockKind::Tree,
        _ => {
            return Err(PyValueError::new_err(
                "pathlock request.kind must be 'exact' or 'tree'",
            ));
        }
    };
    Ok(PathLockRequest {
        path: path.clone(),
        kind,
    })
}

/// Parse a non-empty Python pathlock request batch.
fn parse_pathlock_request_batch(
    requests: &[HashMap<String, String>],
) -> PyResult<Vec<PathLockRequest>> {
    if requests.is_empty() {
        return Err(PyValueError::new_err(
            "pathlock request batch must not be empty",
        ));
    }
    requests.iter().map(parse_pathlock_request).collect()
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum CacheProviderKind {
    Redis,
    Dynamic,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RagfsCacheConfig {
    enabled: bool,
    runtime_enabled: bool,
    service_name: String,
    instance: String,
    shared_redis_ttl_seconds: u64,
    instances: HashMap<String, RagfsCacheInstanceConfig>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RagfsCacheInstanceConfig {
    provider: CacheProviderKind,
    redis: RedisCacheConfig,
    dynamic: DynamicCacheConfig,
}

#[derive(Clone, PartialEq, Eq)]
struct DynamicCacheConfig {
    library: String,
    params: serde_json::Value,
}

impl fmt::Debug for DynamicCacheConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DynamicCacheConfig")
            .field("library", &self.library)
            .field("params", &"<redacted>")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RedisCacheConfig {
    mode: String,
    endpoints: Vec<String>,
    master_name: Option<String>,
    username: String,
    password_env: String,
    password: String,
    sentinel_username: String,
    sentinel_password_env: String,
    sentinel_password: String,
    db: i64,
    pool_size: usize,
    connect_timeout_ms: u64,
    command_timeout_ms: u64,
    default_ttl_seconds: u64,
    tls_insecure_skip_verify: bool,
}

impl Default for RagfsCacheConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            runtime_enabled: false,
            service_name: String::new(),
            instance: "default".to_string(),
            shared_redis_ttl_seconds: 0,
            instances: HashMap::new(),
        }
    }
}

impl Default for DynamicCacheConfig {
    fn default() -> Self {
        Self {
            library: String::new(),
            params: serde_json::Value::Object(serde_json::Map::new()),
        }
    }
}

impl Default for RedisCacheConfig {
    fn default() -> Self {
        Self {
            mode: "standalone".to_string(),
            endpoints: vec!["redis://127.0.0.1:6379".to_string()],
            master_name: None,
            username: String::new(),
            password_env: String::new(),
            password: String::new(),
            sentinel_username: String::new(),
            sentinel_password_env: String::new(),
            sentinel_password: String::new(),
            db: 0,
            pool_size: 32,
            connect_timeout_ms: 10_000,
            command_timeout_ms: 0,
            default_ttl_seconds: 300,
            tls_insecure_skip_verify: false,
        }
    }
}

impl RagfsCacheConfig {
    fn stack_config(&self) -> Option<CacheStackConfig> {
        if !self.enabled && !self.runtime_enabled {
            return None;
        }
        let instances = self
            .instances
            .iter()
            .map(|(name, instance)| {
                let provider = match &instance.provider {
                    CacheProviderKind::Redis => {
                        CacheRuntimeProviderConfig::Redis(RedisProviderConfig {
                            mode: instance.redis.mode.clone(),
                            endpoints: instance.redis.endpoints.clone(),
                            master_name: instance.redis.master_name.clone(),
                            username: instance.redis.username.clone(),
                            password_env: instance.redis.password_env.clone(),
                            password: instance.redis.password.clone(),
                            sentinel_username: instance.redis.sentinel_username.clone(),
                            sentinel_password_env: instance.redis.sentinel_password_env.clone(),
                            sentinel_password: instance.redis.sentinel_password.clone(),
                            db: instance.redis.db,
                            pool_size: instance.redis.pool_size,
                            connect_timeout_ms: instance.redis.connect_timeout_ms,
                            command_timeout_ms: instance.redis.command_timeout_ms,
                            default_ttl_seconds: instance.redis.default_ttl_seconds,
                            tls_insecure_skip_verify: instance.redis.tls_insecure_skip_verify,
                        })
                    }
                    CacheProviderKind::Dynamic => {
                        CacheRuntimeProviderConfig::Dynamic(DynamicProviderConfig {
                            library_path: PathBuf::from(&instance.dynamic.library),
                            params_json: serde_json::to_string(&instance.dynamic.params)
                                .expect("dynamic provider params are valid JSON"),
                        })
                    }
                };
                (name.clone(), provider)
            })
            .collect();
        Some(CacheStackConfig {
            instances,
            cachefs: CacheFsConfig {
                enabled: self.enabled,
                instance: self.instance.clone(),
                service_name: self.service_name.clone(),
                shared_redis_ttl: (self.shared_redis_ttl_seconds > 0)
                    .then(|| Duration::from_secs(self.shared_redis_ttl_seconds)),
            },
        })
    }
}

/// Load PathLock configuration from one canonical OpenViking config file.
fn pathlock_config_from_ov_conf(path: &str, service_name: &str) -> Result<PathLockConfig, String> {
    let raw = fs::read_to_string(path)
        .map_err(|error| format!("failed to read OpenViking config {path}: {error}"))?;
    let json: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("failed to parse OpenViking config {path}: {error}"))?;
    pathlock_config_from_canonical_ov_conf(&json, service_name)
}

/// Parse PathLock configuration from the canonical `storage.agfs.pathlock` section.
fn pathlock_config_from_canonical_ov_conf(
    json: &serde_json::Value,
    service_name: &str,
) -> Result<PathLockConfig, String> {
    let pathlock = json
        .get("storage")
        .and_then(|storage| storage.get("agfs"))
        .and_then(|agfs| agfs.get("pathlock"));
    match pathlock {
        Some(pathlock) => {
            let mut pathlock = pathlock.clone();
            if pathlock.get("provider").and_then(serde_json::Value::as_str) == Some("sqlite")
                && pathlock
                    .get("sqlite_path")
                    .is_none_or(serde_json::Value::is_null)
            {
                let workspace = json
                    .get("storage")
                    .and_then(|storage| storage.get("workspace"))
                    .and_then(serde_json::Value::as_str)
                    .filter(|workspace| !workspace.is_empty())
                    .ok_or_else(|| {
                        "storage.workspace is required for the default SQLite lock database"
                            .to_string()
                    })?;
                // The workspace may not exist yet. Resolve relative paths against
                // the process working directory without requiring existing parents.
                let database = std::path::absolute(workspace)
                    .map_err(|error| format!("failed to resolve storage.workspace: {error}"))?
                    .join("_system/pathlock/pathlock.db");
                pathlock["sqlite_path"] = serde_json::json!(database);
            }
            pathlock_config_from_value(&pathlock, service_name)
        }
        None => Ok(PathLockConfig::default()),
    }
}

/// Parse one sectioned PathLock configuration object.
fn pathlock_config_from_value(
    value: &serde_json::Value,
    service_name: &str,
) -> Result<PathLockConfig, String> {
    let pathlock = value
        .as_object()
        .ok_or_else(|| "pathlock config must be an object".to_string())?;
    const FIELDS: &[&str] = &[
        "provider",
        "instance",
        "sqlite_path",
        "postgres_dsn_env",
        "lock_timeout_secs",
        "lock_expire_secs",
    ];
    if let Some(field) = pathlock
        .keys()
        .find(|field| !FIELDS.contains(&field.as_str()))
    {
        return Err(format!("unsupported pathlock field: {field}"));
    }

    let provider = string_field(pathlock, "provider", "filesystem")?;
    if !matches!(
        provider.as_str(),
        "filesystem" | "memory" | "sqlite" | "cache" | "postgres"
    ) {
        return Err(format!(
            "unsupported pathlock.provider: {provider}; expected filesystem, memory, sqlite, postgres, or cache"
        ));
    }
    let pathlock_service_name = matches!(provider.as_str(), "cache" | "sqlite" | "postgres")
        .then(|| service_name.to_string());
    if provider != "cache" && pathlock.contains_key("instance") {
        return Err("pathlock.instance requires provider='cache'".to_string());
    }
    let postgres_dsn_env = match pathlock.get("postgres_dsn_env") {
        None | Some(serde_json::Value::Null) => None,
        Some(serde_json::Value::String(name)) if !name.trim().is_empty() => Some(name.clone()),
        _ => return Err("pathlock.postgres_dsn_env must be a nonempty string".into()),
    };
    if provider == "postgres" && postgres_dsn_env.is_none() {
        return Err("pathlock.postgres_dsn_env is required for PostgreSQL PathLock".into());
    }
    let sqlite_path = match pathlock.get("sqlite_path") {
        None | Some(serde_json::Value::Null) => None,
        Some(serde_json::Value::String(path)) => Some(path.clone()),
        _ => return Err("pathlock.sqlite_path must be a string".to_string()),
    };
    if provider == "sqlite"
        && !sqlite_path
            .as_deref()
            .is_some_and(|path| std::path::Path::new(path).is_absolute())
    {
        return Err("pathlock.sqlite_path must be an absolute local file path".to_string());
    }

    let lock_timeout_secs = f64_field(pathlock, "lock_timeout_secs", 0.0)?;
    if !lock_timeout_secs.is_finite() || lock_timeout_secs < 0.0 {
        return Err("pathlock.lock_timeout_secs must be a finite non-negative number".to_string());
    }
    let lock_expire_secs = f64_field(pathlock, "lock_expire_secs", 30.0)?;
    if !lock_expire_secs.is_finite() || lock_expire_secs < 1.0 {
        return Err("pathlock.lock_expire_secs must be finite and >= 1.0".to_string());
    }

    Ok(PathLockConfig {
        provider,
        instance: string_field(pathlock, "instance", "default")?,
        service_name: pathlock_service_name,
        sqlite_path,
        postgres_dsn_env,
        lock_timeout_secs,
        lock_expire_secs,
    })
}

fn cache_config_from_ov_conf(path: &str, service_name: &str) -> Result<RagfsCacheConfig, String> {
    let raw = fs::read_to_string(path)
        .map_err(|error| format!("failed to read OpenViking config {path}: {error}"))?;
    let json: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("failed to parse OpenViking config {path}: {error}"))?;
    cache_config_from_canonical_ov_conf(&json, service_name)
}

/// Load canonical cache configuration while forcing a shared Runtime consumer.
fn cache_config_from_ov_conf_with_runtime(
    path: &str,
    force_runtime: bool,
    service_name: &str,
) -> Result<RagfsCacheConfig, String> {
    let raw = fs::read_to_string(path)
        .map_err(|error| format!("failed to read OpenViking config {path}: {error}"))?;
    let json: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("failed to parse OpenViking config {path}: {error}"))?;
    cache_config_from_canonical_ov_conf_with_runtime(&json, force_runtime, service_name)
}

fn cache_config_from_canonical_ov_conf(
    json: &serde_json::Value,
    service_name: &str,
) -> Result<RagfsCacheConfig, String> {
    let pathlock_uses_cache =
        pathlock_config_from_canonical_ov_conf(json, service_name)?.provider == "cache";
    let backups_use_cache = json
        .get("storage")
        .and_then(|storage| storage.get("agfs"))
        .and_then(|agfs| agfs.get("backups"))
        .and_then(|backups| backups.get("provider"))
        .and_then(serde_json::Value::as_str)
        == Some("cache");
    cache_config_from_canonical_ov_conf_with_runtime(
        json,
        pathlock_uses_cache
            || backups_use_cache
            || json
                .pointer("/storage/agfs/coordination/provider")
                .and_then(|v| v.as_str())
                == Some("cache"),
        service_name,
    )
}

/// Parse canonical cache configuration and optionally require its shared Runtime.
fn cache_config_from_canonical_ov_conf_with_runtime(
    json: &serde_json::Value,
    force_runtime: bool,
    service_name: &str,
) -> Result<RagfsCacheConfig, String> {
    let agfs = json.get("storage").and_then(|storage| storage.get("agfs"));
    if agfs.and_then(|agfs| agfs.get("cache")).is_some() {
        return Err(
            "storage.agfs.cache has been removed; use cache.instances and ".to_string()
                + "storage.agfs.cachefs.backend=cache",
        );
    }

    let queuefs = agfs.and_then(|agfs| agfs.get("queuefs"));
    if queuefs.and_then(|queuefs| queuefs.get("redis")).is_some() {
        return Err(
            "storage.agfs.queuefs.redis has been removed; use queuefs backend=cache with "
                .to_string()
                + "cache.instances",
        );
    }
    let queuefs_backend = optional_backend(queuefs, "storage.agfs.queuefs", "sqlite")?;
    if queuefs_backend == "redis" {
        return Err(
            "queuefs backend=redis has been removed; use backend=cache with ".to_string()
                + "cache.instances",
        );
    }

    let cachefs = agfs.and_then(|agfs| agfs.get("cachefs"));
    let cachefs_backend = optional_backend(cachefs, "storage.agfs.cachefs", "local")?;
    if !matches!(cachefs_backend.as_str(), "local" | "cache") {
        return Err(format!(
            "unsupported storage.agfs.cachefs.backend: {cachefs_backend}; expected local or cache"
        ));
    }

    let mut config = RagfsCacheConfig::default();
    config.service_name = service_name.to_string();
    config.enabled = cachefs_backend == "cache";
    config.runtime_enabled = config.enabled || queuefs_backend == "cache" || force_runtime;
    if let Some(cachefs) = cachefs {
        let cachefs = cachefs
            .as_object()
            .ok_or_else(|| "storage.agfs.cachefs must be an object".to_string())?;
        config.shared_redis_ttl_seconds = u64_field_allow_zero(
            cachefs,
            "shared_redis_ttl_seconds",
            config.shared_redis_ttl_seconds,
        )?;
        config.instance = string_field(cachefs, "instance", "default")?;
        if cachefs_backend != "cache" && cachefs.contains_key("instance") {
            return Err("storage.agfs.cachefs.instance requires backend='cache'".to_string());
        }
    }

    let Some(cache) = json.get("cache") else {
        if config.runtime_enabled {
            return Err(
                "top-level cache.instances is required when AGFS uses cache-backed modules"
                    .to_string(),
            );
        }
        return Ok(config);
    };
    let cache = cache
        .as_object()
        .ok_or_else(|| "top-level cache must be an object".to_string())?;
    if let Some(field) = cache.keys().find(|field| field.as_str() != "instances") {
        return Err(format!(
            "unsupported top-level cache field: {field}; use cache.instances"
        ));
    }
    config.instances = parse_canonical_cache_instances(cache)?;
    if !config.instances.contains_key("default") {
        return Err("cache.instances must contain a non-empty default instance".to_string());
    }
    validate_cache_runtime_config(&config)?;
    Ok(config)
}

fn optional_backend(
    section: Option<&serde_json::Value>,
    name: &str,
    default: &str,
) -> Result<String, String> {
    let Some(section) = section else {
        return Ok(default.to_string());
    };
    let section = section
        .as_object()
        .ok_or_else(|| format!("{name} must be an object"))?;
    string_field(section, "backend", default)
}

fn cache_config_from_value(
    cache: &serde_json::Value,
    service_name: &str,
) -> Result<RagfsCacheConfig, String> {
    if cache.is_null() {
        let mut config = RagfsCacheConfig::default();
        config.service_name = service_name.to_string();
        return Ok(config);
    }
    let cache = cache
        .as_object()
        .ok_or_else(|| "binding cache config must be an object".to_string())?;
    const FIELDS: &[&str] = &["enabled", "runtime_enabled", "instances", "cachefs"];
    if let Some(field) = cache.keys().find(|field| !FIELDS.contains(&field.as_str())) {
        return Err(format!("unsupported binding cache field: {field}"));
    }

    let mut config = RagfsCacheConfig::default();
    config.service_name = service_name.to_string();
    config.enabled = bool_field(cache, "enabled", config.enabled)?;
    config.runtime_enabled = bool_field(cache, "runtime_enabled", config.enabled)?;
    let cachefs = cache
        .get("cachefs")
        .ok_or_else(|| "binding cache.cachefs must be an object".to_string())?
        .as_object()
        .ok_or_else(|| "binding cache.cachefs must be an object".to_string())?;
    const CACHEFS_FIELDS: &[&str] = &[
        "enabled",
        "instance",
        "service_name",
        "shared_redis_ttl_seconds",
    ];
    if let Some(field) = cachefs
        .keys()
        .find(|field| !CACHEFS_FIELDS.contains(&field.as_str()))
    {
        return Err(format!("unsupported binding cache.cachefs field: {field}"));
    }
    let cachefs_enabled = bool_field(cachefs, "enabled", config.enabled)?;
    if cachefs_enabled != config.enabled {
        return Err("binding cache.enabled must match cache.cachefs.enabled".to_string());
    }
    let cachefs_service_name = string_field(cachefs, "service_name", service_name)?;
    if cachefs_service_name != service_name {
        return Err("binding cache.cachefs.service_name must match service_name".to_string());
    }
    config.instance = string_field(cachefs, "instance", "default")?;
    config.shared_redis_ttl_seconds = u64_field_allow_zero(
        cachefs,
        "shared_redis_ttl_seconds",
        config.shared_redis_ttl_seconds,
    )?;
    config.instances = match cache.get("instances") {
        Some(instances) => parse_binding_cache_instances(instances)?,
        None if config.runtime_enabled => {
            return Err("binding cache.instances is required when runtime_enabled=true".to_string())
        }
        None => HashMap::new(),
    };
    validate_cache_runtime_config(&config)?;
    Ok(config)
}

/// Parse provider instances from canonical ov.conf configuration.
fn parse_canonical_cache_instances(
    cache: &serde_json::Map<String, serde_json::Value>,
) -> Result<HashMap<String, RagfsCacheInstanceConfig>, String> {
    let instances = cache
        .get("instances")
        .ok_or_else(|| "top-level cache.instances is required".to_string())?
        .as_object()
        .ok_or_else(|| "top-level cache.instances must be an object".to_string())?;
    instances
        .iter()
        .map(|(name, value)| {
            validate_cache_instance_name(name)?;
            let value = value
                .as_object()
                .ok_or_else(|| format!("cache.instances.{name} must be an object"))?;
            if let Some(field) = value
                .keys()
                .find(|field| !matches!(field.as_str(), "provider" | "params"))
            {
                return Err(format!(
                    "unsupported cache.instances.{name} field: {field}"
                ));
            }
            let provider = provider_kind(string_field(value, "provider", "")?)?;
            let empty_params = serde_json::Map::new();
            let params = match value.get("params") {
                Some(params) => params.as_object().ok_or_else(|| {
                    format!("cache.instances.{name}.params must be an object")
                })?,
                None => &empty_params,
            };
            parse_cache_instance(provider, params, &format!("cache.instances.{name}.params"))
                .map(|instance| (name.clone(), instance))
        })
        .collect()
}

/// Parse provider instances from the JSON passed to the PyO3 binding.
fn parse_binding_cache_instances(
    value: &serde_json::Value,
) -> Result<HashMap<String, RagfsCacheInstanceConfig>, String> {
    let instances = value
        .as_object()
        .ok_or_else(|| "binding cache.instances must be an object".to_string())?;
    instances
        .iter()
        .map(|(name, value)| {
            validate_cache_instance_name(name)?;
            let value = value
                .as_object()
                .ok_or_else(|| format!("binding cache.instances.{name} must be an object"))?;
            let provider = provider_kind(string_field(value, "provider", "")?)?;
            let config_key = match &provider {
                CacheProviderKind::Redis => "redis",
                CacheProviderKind::Dynamic => "dynamic",
            };
            if let Some(field) = value
                .keys()
                .find(|field| field.as_str() != "provider" && field.as_str() != config_key)
            {
                return Err(format!(
                    "unsupported binding cache.instances.{name} field: {field}"
                ));
            }
            let params = value
                .get(config_key)
                .ok_or_else(|| format!("binding cache.instances.{name}.{config_key} is required"))?
                .as_object()
                .ok_or_else(|| {
                    format!("binding cache.instances.{name}.{config_key} must be an object")
                })?;
            parse_cache_instance(
                provider,
                params,
                &format!("binding cache.instances.{name}.{config_key}"),
            )
            .map(|instance| (name.clone(), instance))
        })
        .collect()
}

/// Validate one public cache instance identifier.
fn validate_cache_instance_name(name: &str) -> Result<(), String> {
    if !name.is_empty()
        && name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        Ok(())
    } else {
        Err(format!(
            "cache instance name '{name}' must match [A-Za-z0-9_-]+"
        ))
    }
}

/// Build one provider configuration from its normalized parameter object.
fn parse_cache_instance(
    provider: CacheProviderKind,
    params: &serde_json::Map<String, serde_json::Value>,
    name: &str,
) -> Result<RagfsCacheInstanceConfig, String> {
    let mut instance = RagfsCacheInstanceConfig {
        provider,
        redis: RedisCacheConfig::default(),
        dynamic: DynamicCacheConfig::default(),
    };
    match &instance.provider {
        CacheProviderKind::Redis => parse_redis_config(&mut instance.redis, params, name)?,
        CacheProviderKind::Dynamic => parse_dynamic_config(&mut instance.dynamic, params)?,
    }
    Ok(instance)
}

fn parse_dynamic_config(
    config: &mut DynamicCacheConfig,
    params: &serde_json::Map<String, serde_json::Value>,
) -> Result<(), String> {
    config.library = string_field(params, "library", &config.library)?;
    let mut provider_params = params.clone();
    provider_params.remove("library");
    config.params = serde_json::Value::Object(provider_params);
    Ok(())
}

fn parse_redis_config(
    config: &mut RedisCacheConfig,
    redis: &serde_json::Map<String, serde_json::Value>,
    name: &str,
) -> Result<(), String> {
    const FIELDS: &[&str] = &[
        "mode",
        "endpoints",
        "master_name",
        "username",
        "password_env",
        "password",
        "sentinel_username",
        "sentinel_password_env",
        "sentinel_password",
        "db",
        "pool_size",
        "connect_timeout_ms",
        "command_timeout_ms",
        "default_ttl_seconds",
        "tls_insecure_skip_verify",
    ];
    if let Some(field) = redis.keys().find(|field| !FIELDS.contains(&field.as_str())) {
        return Err(format!("unsupported {name} field: {field}"));
    }
    config.mode = string_field(redis, "mode", &config.mode)?;
    config.endpoints = string_array_field_or_default(redis, "endpoints", &config.endpoints)?;
    config.master_name = optional_string_field(redis, "master_name")?;
    config.username = string_field(redis, "username", &config.username)?;
    config.password_env = string_field(redis, "password_env", &config.password_env)?;
    config.password = string_field(redis, "password", &config.password)?;
    config.sentinel_username = string_field(redis, "sentinel_username", &config.sentinel_username)?;
    config.sentinel_password_env = string_field(
        redis,
        "sentinel_password_env",
        &config.sentinel_password_env,
    )?;
    config.sentinel_password = string_field(redis, "sentinel_password", &config.sentinel_password)?;
    config.db = i64_field(redis, "db", config.db)?;
    config.pool_size = usize_field(redis, "pool_size", config.pool_size)?;
    config.connect_timeout_ms = u64_field(redis, "connect_timeout_ms", config.connect_timeout_ms)?;
    config.command_timeout_ms =
        u64_field_allow_zero(redis, "command_timeout_ms", config.command_timeout_ms)?;
    config.default_ttl_seconds =
        u64_field_allow_zero(redis, "default_ttl_seconds", config.default_ttl_seconds)?;
    config.tls_insecure_skip_verify = bool_field(
        redis,
        "tls_insecure_skip_verify",
        config.tls_insecure_skip_verify,
    )?;
    Ok(())
}

fn validate_cache_runtime_config(config: &RagfsCacheConfig) -> Result<(), String> {
    if config.runtime_enabled && !config.instances.contains_key("default") {
        return Err("cache.instances must contain a non-empty default instance".to_string());
    }
    for (name, instance) in &config.instances {
        if matches!(&instance.provider, CacheProviderKind::Dynamic)
            && instance.dynamic.library.trim().is_empty()
        {
            return Err(format!(
                "cache instance '{name}' dynamic provider library must not be empty"
            ));
        }
    }
    Ok(())
}

fn provider_kind(value: String) -> Result<CacheProviderKind, String> {
    match value.trim() {
        "redis" => Ok(CacheProviderKind::Redis),
        "dynamic" => Ok(CacheProviderKind::Dynamic),
        other => Err(format!(
            "unsupported cache.provider: {other}; expected redis or dynamic"
        )),
    }
}

fn bool_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: bool,
) -> Result<bool, String> {
    match object.get(key) {
        Some(value) => value
            .as_bool()
            .ok_or_else(|| format!("{key} must be a boolean")),
        None => Ok(default),
    }
}

fn string_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: &str,
) -> Result<String, String> {
    match object.get(key) {
        Some(value) => value
            .as_str()
            .map(ToOwned::to_owned)
            .ok_or_else(|| format!("{key} must be a string")),
        None => Ok(default.to_string()),
    }
}

/// Read a floating-point field or return its default.
fn f64_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: f64,
) -> Result<f64, String> {
    match object.get(key) {
        Some(value) => value
            .as_f64()
            .ok_or_else(|| format!("{key} must be a number")),
        None => Ok(default),
    }
}

fn optional_string_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<Option<String>, String> {
    match object.get(key) {
        Some(serde_json::Value::Null) | None => Ok(None),
        Some(value) => value
            .as_str()
            .map(|value| Some(value.to_string()))
            .ok_or_else(|| format!("{key} must be a string or null")),
    }
}

fn i64_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: i64,
) -> Result<i64, String> {
    match object.get(key) {
        Some(value) => value
            .as_i64()
            .ok_or_else(|| format!("{key} must be an integer")),
        None => Ok(default),
    }
}

fn u64_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: u64,
) -> Result<u64, String> {
    match object.get(key) {
        Some(value) => value
            .as_u64()
            .filter(|value| *value > 0)
            .ok_or_else(|| format!("{key} must be a positive integer")),
        None => Ok(default),
    }
}

fn u64_field_allow_zero(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: u64,
) -> Result<u64, String> {
    match object.get(key) {
        Some(value) => value
            .as_u64()
            .ok_or_else(|| format!("{key} must be a non-negative integer")),
        None => Ok(default),
    }
}

fn usize_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: usize,
) -> Result<usize, String> {
    let value = u64_field(object, key, default as u64)?;
    usize::try_from(value).map_err(|_| format!("{key} must fit in usize"))
}

fn string_array_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<Vec<String>, String> {
    match object.get(key) {
        Some(value) => value
            .as_array()
            .ok_or_else(|| format!("{key} must be an array of strings"))?
            .iter()
            .map(|item| {
                item.as_str()
                    .map(ToOwned::to_owned)
                    .ok_or_else(|| format!("{key} must be an array of strings"))
            })
            .collect(),
        None => Ok(Vec::new()),
    }
}

fn string_array_field_or_default(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: &[String],
) -> Result<Vec<String>, String> {
    match object.get(key) {
        Some(_) => string_array_field(object, key),
        None => Ok(default.to_vec()),
    }
}
