//! Python bindings for RAGFS - Rust AGFS filesystem
//!
//! Provides `RAGFSBindingClient`, a PyO3 native class that is API-compatible
//! with the existing Go-based `AGFSBindingClient`. This embeds the ragfs
//! filesystem engine directly in the Python process (no HTTP server needed).

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyType};
use std::collections::HashMap;
use std::fs;
use std::sync::Arc;
use std::time::UNIX_EPOCH;

use ragfs::cache::{
    CacheError, CacheNamespace, CachePolicy, CacheProvider, CacheResult, MemoryCacheProvider,
};
use ragfs::core::{
    ConfigValue, FileInfo, FileSystem, FilesystemStats, FsOperation, GrepResult, MountableFS,
    OperationStats, PluginConfig, TreeEntry, WriteFlag,
};
#[cfg(feature = "s3")]
use ragfs::plugins::S3FSPlugin;
use ragfs::plugins::{
    KVFSPlugin, LocalFSPlugin, MemFSPlugin, QueueFSPlugin, SQLFSPlugin, ServerInfoFSPlugin,
};

#[derive(Debug, Clone, PartialEq, Eq)]
enum CacheProviderKind {
    Memory,
    Yuanrong,
    Mooncake,
    Redis,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RagfsCacheConfig {
    enabled: bool,
    provider: CacheProviderKind,
    namespace: String,
    max_file_size_bytes: usize,
    bypass_prefixes: Vec<String>,
    yuanrong: YuanrongCacheConfig,
    mooncake: MooncakeCacheConfig,
    redis: RedisCacheConfig,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct YuanrongCacheConfig {
    host: String,
    port: u16,
    connect_timeout_ms: u64,
    request_timeout_ms: u64,
    sdk_concurrency: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MooncakeCacheConfig {
    local_hostname: String,
    metadata_server: String,
    master_server_addr: String,
    protocol: String,
    device_name: String,
    global_segment_size: u64,
    local_buffer_size: u64,
    replica_num: usize,
    sdk_concurrency: usize,
    operation_timeout_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RedisCacheConfig {
    mode: String,
    endpoints: Vec<String>,
    username: String,
    password_env: String,
    pool_size: usize,
    connect_timeout_ms: u64,
    command_timeout_ms: u64,
    key_prefix: String,
    default_ttl_seconds: u64,
    read_from_replica: bool,
}

impl Default for RagfsCacheConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            provider: CacheProviderKind::Memory,
            namespace: "openviking".to_string(),
            max_file_size_bytes: CachePolicy::default().max_file_size(),
            bypass_prefixes: Vec::new(),
            yuanrong: YuanrongCacheConfig::default(),
            mooncake: MooncakeCacheConfig::default(),
            redis: RedisCacheConfig::default(),
        }
    }
}

impl Default for YuanrongCacheConfig {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 31501,
            connect_timeout_ms: 5_000,
            request_timeout_ms: 5_000,
            sdk_concurrency: 4,
        }
    }
}

impl Default for MooncakeCacheConfig {
    fn default() -> Self {
        Self {
            local_hostname: "127.0.0.1".to_string(),
            metadata_server: "http://127.0.0.1:8080/metadata".to_string(),
            master_server_addr: "127.0.0.1:50051".to_string(),
            protocol: "tcp".to_string(),
            device_name: String::new(),
            global_segment_size: 512 << 20,
            local_buffer_size: 128 << 20,
            replica_num: 2,
            sdk_concurrency: 4,
            operation_timeout_ms: 5_000,
        }
    }
}

impl Default for RedisCacheConfig {
    fn default() -> Self {
        Self {
            mode: "standalone".to_string(),
            endpoints: vec!["redis://127.0.0.1:6379".to_string()],
            username: String::new(),
            password_env: String::new(),
            pool_size: 32,
            connect_timeout_ms: 1_000,
            command_timeout_ms: 20,
            key_prefix: "ragfs-cache".to_string(),
            default_ttl_seconds: 3_600,
            read_from_replica: false,
        }
    }
}

struct CacheProviderFactory;

impl CacheProviderFactory {
    async fn create(config: &RagfsCacheConfig) -> CacheResult<Arc<dyn CacheProvider>> {
        match config.provider {
            CacheProviderKind::Memory => Ok(Arc::new(MemoryCacheProvider::new())),
            CacheProviderKind::Yuanrong => create_yuanrong_provider(config).await,
            CacheProviderKind::Mooncake => create_mooncake_provider(config).await,
            CacheProviderKind::Redis => create_redis_provider(config).await,
        }
    }
}

#[cfg(feature = "yuanrong-native")]
async fn create_yuanrong_provider(
    config: &RagfsCacheConfig,
) -> CacheResult<Arc<dyn CacheProvider>> {
    use ragfs_cache_yuanrong::{YuanrongConfig, YuanrongProvider};

    let provider = YuanrongProvider::connect(YuanrongConfig {
        host: config.yuanrong.host.clone(),
        port: config.yuanrong.port,
        connect_timeout_ms: config.yuanrong.connect_timeout_ms,
        request_timeout_ms: config.yuanrong.request_timeout_ms,
        sdk_concurrency: config.yuanrong.sdk_concurrency,
    })
    .await?;
    Ok(Arc::new(provider))
}

#[cfg(not(feature = "yuanrong-native"))]
async fn create_yuanrong_provider(
    _config: &RagfsCacheConfig,
) -> CacheResult<Arc<dyn CacheProvider>> {
    Err(CacheError::Unavailable(
        "Yuanrong support requires the yuanrong-native feature".to_string(),
    ))
}

#[cfg(feature = "mooncake-native")]
async fn create_mooncake_provider(
    config: &RagfsCacheConfig,
) -> CacheResult<Arc<dyn CacheProvider>> {
    use ragfs_cache_mooncake::{MooncakeConfig, MooncakeProvider};

    let provider = MooncakeProvider::connect(MooncakeConfig {
        local_hostname: config.mooncake.local_hostname.clone(),
        metadata_server: config.mooncake.metadata_server.clone(),
        master_server_addr: config.mooncake.master_server_addr.clone(),
        protocol: config.mooncake.protocol.clone(),
        device_name: config.mooncake.device_name.clone(),
        global_segment_size: config.mooncake.global_segment_size,
        local_buffer_size: config.mooncake.local_buffer_size,
        replica_num: config.mooncake.replica_num,
        sdk_concurrency: config.mooncake.sdk_concurrency,
        operation_timeout_ms: config.mooncake.operation_timeout_ms,
    })
    .await?;
    Ok(Arc::new(provider))
}

#[cfg(not(feature = "mooncake-native"))]
async fn create_mooncake_provider(
    _config: &RagfsCacheConfig,
) -> CacheResult<Arc<dyn CacheProvider>> {
    Err(CacheError::Unavailable(
        "Mooncake support requires the mooncake-native feature".to_string(),
    ))
}

#[cfg(feature = "cache-redis")]
async fn create_redis_provider(config: &RagfsCacheConfig) -> CacheResult<Arc<dyn CacheProvider>> {
    use ragfs_cache_redis::{RedisConfig, RedisProvider};

    let provider = RedisProvider::connect(RedisConfig {
        mode: config.redis.mode.clone(),
        endpoints: config.redis.endpoints.clone(),
        username: config.redis.username.clone(),
        password_env: config.redis.password_env.clone(),
        pool_size: config.redis.pool_size,
        connect_timeout_ms: config.redis.connect_timeout_ms,
        command_timeout_ms: config.redis.command_timeout_ms,
        key_prefix: config.redis.key_prefix.clone(),
        default_ttl_seconds: config.redis.default_ttl_seconds,
        read_from_replica: config.redis.read_from_replica,
    })
    .await?;
    Ok(Arc::new(provider))
}

#[cfg(not(feature = "cache-redis"))]
async fn create_redis_provider(_config: &RagfsCacheConfig) -> CacheResult<Arc<dyn CacheProvider>> {
    Err(CacheError::Unavailable(
        "Redis support requires the cache-redis feature".to_string(),
    ))
}

fn cache_config_from_ov_conf(path: &str) -> Result<RagfsCacheConfig, String> {
    let raw = fs::read_to_string(path)
        .map_err(|error| format!("failed to read OpenViking config {path}: {error}"))?;
    let json: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("failed to parse OpenViking config {path}: {error}"))?;

    let Some(cache) = json
        .get("storage")
        .and_then(|storage| storage.get("agfs"))
        .and_then(|agfs| agfs.get("cache"))
    else {
        return Ok(RagfsCacheConfig::default());
    };

    if cache.is_null() {
        return Ok(RagfsCacheConfig::default());
    }
    let cache = cache
        .as_object()
        .ok_or_else(|| "storage.agfs.cache must be an object".to_string())?;

    let mut config = RagfsCacheConfig::default();
    config.enabled = bool_field(cache, "enabled", config.enabled)?;
    config.provider = provider_kind(string_field(cache, "provider", "memory")?)?;
    config.namespace = string_field(cache, "namespace", &config.namespace)?;
    if config.namespace.trim().is_empty() {
        return Err("storage.agfs.cache.namespace must not be empty".to_string());
    }
    config.max_file_size_bytes =
        usize_field(cache, "max_file_size_bytes", config.max_file_size_bytes)?;
    config.bypass_prefixes = string_array_field(cache, "bypass_prefixes")?;

    if let Some(yuanrong) = cache.get("yuanrong") {
        let yuanrong = yuanrong
            .as_object()
            .ok_or_else(|| "storage.agfs.cache.yuanrong must be an object".to_string())?;
        config.yuanrong.host = string_field(yuanrong, "host", &config.yuanrong.host)?;
        config.yuanrong.port = u16_field(yuanrong, "port", config.yuanrong.port)?;
        config.yuanrong.connect_timeout_ms = u64_field(
            yuanrong,
            "connect_timeout_ms",
            config.yuanrong.connect_timeout_ms,
        )?;
        config.yuanrong.request_timeout_ms = u64_field(
            yuanrong,
            "request_timeout_ms",
            config.yuanrong.request_timeout_ms,
        )?;
        config.yuanrong.sdk_concurrency =
            usize_field(yuanrong, "sdk_concurrency", config.yuanrong.sdk_concurrency)?;
    }

    if let Some(mooncake) = cache.get("mooncake") {
        let mooncake = mooncake
            .as_object()
            .ok_or_else(|| "storage.agfs.cache.mooncake must be an object".to_string())?;
        config.mooncake.local_hostname =
            string_field(mooncake, "local_hostname", &config.mooncake.local_hostname)?;
        config.mooncake.metadata_server = string_field(
            mooncake,
            "metadata_server",
            &config.mooncake.metadata_server,
        )?;
        config.mooncake.master_server_addr = string_field(
            mooncake,
            "master_server_addr",
            &config.mooncake.master_server_addr,
        )?;
        config.mooncake.protocol = string_field(mooncake, "protocol", &config.mooncake.protocol)?;
        config.mooncake.device_name =
            string_field(mooncake, "device_name", &config.mooncake.device_name)?;
        config.mooncake.global_segment_size = u64_field(
            mooncake,
            "global_segment_size",
            config.mooncake.global_segment_size,
        )?;
        config.mooncake.local_buffer_size = u64_field(
            mooncake,
            "local_buffer_size",
            config.mooncake.local_buffer_size,
        )?;
        config.mooncake.replica_num =
            usize_field(mooncake, "replica_num", config.mooncake.replica_num)?;
        config.mooncake.sdk_concurrency =
            usize_field(mooncake, "sdk_concurrency", config.mooncake.sdk_concurrency)?;
        config.mooncake.operation_timeout_ms = u64_field(
            mooncake,
            "operation_timeout_ms",
            config.mooncake.operation_timeout_ms,
        )?;
    }

    if let Some(redis) = cache.get("redis") {
        let redis = redis
            .as_object()
            .ok_or_else(|| "storage.agfs.cache.redis must be an object".to_string())?;
        config.redis.mode = string_field(redis, "mode", &config.redis.mode)?;
        config.redis.endpoints =
            string_array_field_or_default(redis, "endpoints", &config.redis.endpoints)?;
        config.redis.username = string_field(redis, "username", &config.redis.username)?;
        config.redis.password_env =
            string_field(redis, "password_env", &config.redis.password_env)?;
        config.redis.pool_size = usize_field(redis, "pool_size", config.redis.pool_size)?;
        config.redis.connect_timeout_ms =
            u64_field(redis, "connect_timeout_ms", config.redis.connect_timeout_ms)?;
        config.redis.command_timeout_ms =
            u64_field(redis, "command_timeout_ms", config.redis.command_timeout_ms)?;
        config.redis.key_prefix = string_field(redis, "key_prefix", &config.redis.key_prefix)?;
        config.redis.default_ttl_seconds = u64_field_allow_zero(
            redis,
            "default_ttl_seconds",
            config.redis.default_ttl_seconds,
        )?;
        config.redis.read_from_replica =
            bool_field(redis, "read_from_replica", config.redis.read_from_replica)?;
    }

    Ok(config)
}

fn provider_kind(value: String) -> Result<CacheProviderKind, String> {
    match value.as_str() {
        "memory" => Ok(CacheProviderKind::Memory),
        "yuanrong" => Ok(CacheProviderKind::Yuanrong),
        "mooncake" => Ok(CacheProviderKind::Mooncake),
        "redis" => Ok(CacheProviderKind::Redis),
        other => Err(format!(
            "unsupported storage.agfs.cache.provider: {other}; expected memory, yuanrong, mooncake, or redis"
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

fn u16_field(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    default: u16,
) -> Result<u16, String> {
    let value = u64_field(object, key, default as u64)?;
    u16::try_from(value).map_err(|_| format!("{key} must fit in u16"))
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

fn cache_policy_from_config(config: &RagfsCacheConfig) -> CachePolicy {
    config.bypass_prefixes.iter().fold(
        CachePolicy::new(config.max_file_size_bytes),
        |policy, prefix| policy.with_bypass_prefix(prefix),
    )
}

fn py_detach_blocking<T, F>(py: Python<'_>, f: F) -> T
where
    T: Send,
    F: Send + FnOnce() -> T,
{
    py.detach(f)
}

/// Get a Python exception class from the pyagfs module
fn get_exception<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyType>> {
    let pyagfs = PyModule::import(py, "openviking.pyagfs")?;
    let exc = pyagfs.getattr(name)?;
    Ok(exc.cast_into()?)
}

/// Create a PyErr from an exception type name and message
fn new_py_err(name: &str, msg: String) -> PyErr {
    Python::attach(|py| {
        if let Ok(exc) = get_exception(py, name) {
            PyErr::from_type(exc, msg)
        } else {
            PyRuntimeError::new_err(msg)
        }
    })
}

/// Convert a ragfs error into the appropriate Python exception
fn to_py_err(e: ragfs::core::Error) -> PyErr {
    let msg = e.to_string();
    match e {
        ragfs::core::Error::NotFound(_) => new_py_err("AGFSNotFoundError", msg),
        ragfs::core::Error::AlreadyExists(_) => new_py_err("AGFSAlreadyExistsError", msg),
        ragfs::core::Error::PermissionDenied(_) => new_py_err("AGFSPermissionDeniedError", msg),
        ragfs::core::Error::InvalidPath(_) => new_py_err("AGFSInvalidPathError", msg),
        ragfs::core::Error::NotADirectory(_) => new_py_err("AGFSNotADirectoryError", msg),
        ragfs::core::Error::IsADirectory(_) => new_py_err("AGFSIsADirectoryError", msg),
        ragfs::core::Error::DirectoryNotEmpty(_) => new_py_err("AGFSDirectoryNotEmptyError", msg),
        ragfs::core::Error::InvalidOperation(_) => new_py_err("AGFSInvalidOperationError", msg),
        ragfs::core::Error::Io(_) => new_py_err("AGFSIoError", msg),
        ragfs::core::Error::Plugin(_) => {
            // Check if the plugin error message contains known patterns
            let err_msg = msg.to_lowercase();
            if err_msg.contains("directory not empty") {
                new_py_err("AGFSDirectoryNotEmptyError", msg)
            } else {
                new_py_err("AGFSPluginError", msg)
            }
        }
        ragfs::core::Error::Config(_) => new_py_err("AGFSConfigError", msg),
        ragfs::core::Error::MountPointNotFound(_) => new_py_err("AGFSMountPointNotFoundError", msg),
        ragfs::core::Error::MountPointExists(_) => new_py_err("AGFSMountPointExistsError", msg),
        ragfs::core::Error::Serialization(_) => new_py_err("AGFSSerializationError", msg),
        ragfs::core::Error::Network(_) => new_py_err("AGFSNetworkError", msg),
        ragfs::core::Error::Timeout(_) => new_py_err("AGFSTimeoutError", msg),
        ragfs::core::Error::Internal(_) => new_py_err("AGFSInternalError", msg),
    }
}

/// Convert FileInfo to a Python dict matching the Go binding JSON format:
/// {"name": str, "size": int, "mode": int, "modTime": str, "isDir": bool}
fn file_info_to_py_dict(py: Python<'_>, info: &FileInfo) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("name", &info.name)?;
    dict.set_item("size", info.size)?;
    dict.set_item("mode", info.mode)?;

    // modTime as RFC3339 string (Go binding format)
    let secs = info
        .mod_time
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let mod_time = format_rfc3339(secs);
    dict.set_item("modTime", mod_time)?;

    dict.set_item("isDir", info.is_dir)?;
    Ok(dict.into())
}

/// Convert TreeEntry to a Python dict:
/// {"path": str, "rel_path": str, "info": dict, "extra": dict}
fn tree_entry_to_py_dict(py: Python<'_>, entry: &TreeEntry) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("path", &entry.path)?;
    dict.set_item("rel_path", &entry.rel_path)?;
    dict.set_item("info", file_info_to_py_dict(py, &entry.info)?)?;

    let extra_dict = PyDict::new(py);
    for (k, v) in &entry.extra {
        let py_val: Py<PyAny> = serde_json_to_py(py, v)?;
        extra_dict.set_item(k, py_val)?;
    }
    dict.set_item("extra", extra_dict)?;

    Ok(dict.into())
}

/// Convert a serde_json::Value to a Python object.
fn serde_json_to_py(py: Python<'_>, val: &serde_json::Value) -> PyResult<Py<PyAny>> {
    match val {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => {
            let val: bool = *b;
            let bound = val
                .into_pyobject(py)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
            Ok(bound.as_any().clone().unbind())
        }
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.into_any().unbind())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(serde_json_to_py(py, item)?)?;
            }
            Ok(list.into())
        }
        serde_json::Value::Object(obj) => {
            let d = PyDict::new(py);
            for (k, v) in obj {
                d.set_item(k, serde_json_to_py(py, v)?)?;
            }
            Ok(d.into())
        }
    }
}

/// Convert GrepResult to a Python dict matching the Go binding JSON format:
/// {"matches": [{"file": str, "line": int, "content": str}, ...], "count": int}
fn grep_result_to_py_dict(py: Python<'_>, result: &GrepResult) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);

    let matches_list = PyList::empty(py);
    for m in &result.matches {
        let match_dict = PyDict::new(py);
        match_dict.set_item("file", &m.file)?;
        match_dict.set_item("line", m.line)?;
        match_dict.set_item("content", &m.content)?;
        matches_list.append(match_dict)?;
    }

    dict.set_item("matches", matches_list)?;
    dict.set_item("count", result.count)?;

    Ok(dict.into())
}

/// Convert OperationStats to a Python dict.
fn operation_stats_to_py_dict(py: Python<'_>, stats: &OperationStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("count", stats.count)?;
    dict.set_item("total_time_us", stats.total_time_us)?;
    dict.set_item("min_time_us", stats.min_time_us)?;
    dict.set_item("max_time_us", stats.max_time_us)?;
    dict.set_item("avg_time_us", stats.avg_time_us())?;
    Ok(dict.into())
}

/// Convert FilesystemStats to a Python dict.
fn filesystem_stats_to_py_dict(py: Python<'_>, stats: &FilesystemStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    let ops_dict = PyDict::new(py);

    for op in FsOperation::all() {
        let op_stats = stats.get(*op);
        let op_dict = operation_stats_to_py_dict(py, op_stats)?;
        ops_dict.set_item(op.as_str(), op_dict)?;
    }

    dict.set_item("operations", ops_dict)?;
    Ok(dict.into())
}

/// Format unix timestamp as RFC3339 string (simplified, UTC)
fn format_rfc3339(secs: u64) -> String {
    let s = secs;
    let days = s / 86400;
    let time_of_day = s % 86400;
    let h = time_of_day / 3600;
    let m = (time_of_day % 3600) / 60;
    let sec = time_of_day % 60;

    // Calculate date from days since epoch (simplified)
    let (year, month, day) = days_to_ymd(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year, month, day, h, m, sec
    )
}

/// Convert days since Unix epoch to (year, month, day)
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    // Algorithm from http://howardhinnant.github.io/date_algorithms.html
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Convert a Python dict to HashMap<String, ConfigValue>
fn py_dict_to_config(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, ConfigValue>> {
    let mut params = HashMap::new();
    for (k, v) in dict.iter() {
        let key: String = k.extract()?;
        let value = if let Ok(s) = v.extract::<String>() {
            ConfigValue::String(s)
        } else if let Ok(b) = v.extract::<bool>() {
            ConfigValue::Bool(b)
        } else if let Ok(i) = v.extract::<i64>() {
            ConfigValue::Int(i)
        } else {
            ConfigValue::String(v.str()?.to_string())
        };
        params.insert(key, value);
    }
    Ok(params)
}

/// RAGFS Python Binding Client.
///
/// Embeds the ragfs filesystem engine directly in the Python process.
/// API-compatible with the Go-based AGFSBindingClient.
#[pyclass]
struct RAGFSBindingClient {
    fs: Arc<MountableFS>,
    rt: tokio::runtime::Runtime,
}

#[pymethods]
impl RAGFSBindingClient {
    /// Create a new RAGFS binding client.
    ///
    /// Initializes the filesystem engine with all built-in plugins registered.
    #[new]
    #[pyo3(signature = (config_path=None))]
    fn new(config_path: Option<&str>) -> PyResult<Self> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create runtime: {}", e)))?;

        let fs = match config_path {
            Some(path) => {
                let cache_config = cache_config_from_ov_conf(path).map_err(|error| {
                    PyRuntimeError::new_err(format!("Invalid cache config: {error}"))
                })?;
                if cache_config.enabled {
                    let provider = rt
                        .block_on(CacheProviderFactory::create(&cache_config))
                        .map_err(|error| {
                            PyRuntimeError::new_err(format!(
                                "Failed to initialize cache provider: {error}"
                            ))
                        })?;
                    Arc::new(MountableFS::with_cache(
                        provider,
                        CacheNamespace::new(&cache_config.namespace),
                        cache_policy_from_config(&cache_config),
                    ))
                } else {
                    Arc::new(MountableFS::new())
                }
            }
            None => Arc::new(MountableFS::new()),
        };

        // Register all built-in plugins
        rt.block_on(async {
            fs.register_plugin(MemFSPlugin).await;
            fs.register_plugin(KVFSPlugin).await;
            fs.register_plugin(QueueFSPlugin::new()).await;
            fs.register_plugin(SQLFSPlugin::new()).await;
            fs.register_plugin(LocalFSPlugin::new()).await;
            fs.register_plugin(ServerInfoFSPlugin::new()).await;
            #[cfg(feature = "s3")]
            fs.register_plugin(S3FSPlugin::new()).await;
        });

        Ok(Self { fs, rt })
    }

    /// Check client health.
    fn health(&self) -> PyResult<HashMap<String, String>> {
        let mut m = HashMap::new();
        m.insert("status".to_string(), "healthy".to_string());
        Ok(m)
    }

    /// Get client capabilities.
    fn get_capabilities(&self) -> PyResult<HashMap<String, Py<PyAny>>> {
        Python::attach(|py| {
            let mut m = HashMap::new();
            m.insert(
                "version".to_string(),
                "ragfs-python".into_pyobject(py)?.into_any().unbind(),
            );
            let features = vec![
                "memfs",
                "kvfs",
                "queuefs",
                "sqlfs",
                "localfs",
                "serverinfofs",
                #[cfg(feature = "s3")]
                "s3fs",
            ];
            m.insert(
                "features".to_string(),
                features.into_pyobject(py)?.into_any().unbind(),
            );
            Ok(m)
        })
    }

    /// List directory contents.
    ///
    /// Returns a list of file info dicts with keys:
    /// name, size, mode, modTime, isDir
    fn ls(&self, path: String) -> PyResult<Py<PyAny>> {
        let fs = self.fs.clone();
        let entries = Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.read_dir(&path).await })
            })
        })
        .map_err(to_py_err)?;

        Python::attach(|py| {
            let list = PyList::empty(py);
            for entry in &entries {
                let dict = file_info_to_py_dict(py, entry)?;
                list.append(dict)?;
            }
            Ok(list.into())
        })
    }

    /// Read file content.
    ///
    /// Args:
    ///     path: File path
    ///     offset: Starting position (default: 0)
    ///     size: Number of bytes to read (default: -1, read all)
    ///     stream: Not supported in binding mode
    #[pyo3(signature = (path, offset=0, size=-1, stream=false))]
    fn read(&self, path: String, offset: i64, size: i64, stream: bool) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs = self.fs.clone();
        let off = if offset < 0 { 0u64 } else { offset as u64 };
        let sz = if size < 0 { 0u64 } else { size as u64 };

        let data = Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.read(&path, off, sz).await })
            })
        })
        .map_err(to_py_err)?;

        Python::attach(|py| Ok(PyBytes::new(py, &data).into()))
    }

    /// Read file content (alias for read).
    #[pyo3(signature = (path, offset=0, size=-1, stream=false))]
    fn cat(&self, path: String, offset: i64, size: i64, stream: bool) -> PyResult<Py<PyAny>> {
        self.read(path, offset, size, stream)
    }

    /// Write data to file.
    ///
    /// Args:
    ///     path: File path
    ///     data: File content as bytes
    #[pyo3(signature = (path, data, max_retries=3))]
    fn write(&self, path: String, data: Vec<u8>, max_retries: i32) -> PyResult<String> {
        let _ = max_retries; // not applicable for local binding
        let fs = self.fs.clone();
        let len = data.len();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.write(&path, &data, 0, WriteFlag::Create).await })
            })
        })
        .map_err(to_py_err)?;

        Ok(format!("Written {} bytes", len))
    }

    /// Create a new empty file.
    fn create(&self, path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.create(&path).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Create a directory.
    #[pyo3(signature = (path, mode="755"))]
    fn mkdir(&self, path: String, mode: &str) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.mkdir(&path, mode_int).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Ensure all parent directories exist for the given path.
    #[pyo3(signature = (path, mode="755"))]
    fn ensure_parent_dirs(&self, path: String, mode: &str) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.ensure_parent_dirs(&path, mode_int).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            "parent directories ensured".to_string(),
        );
        Ok(m)
    }

    /// Remove a file or directory.
    #[pyo3(signature = (path, recursive=false))]
    fn rm(&self, path: String, recursive: bool) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move {
                    if recursive {
                        fs.remove_all(&path).await
                    } else {
                        fs.remove(&path).await
                    }
                })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "deleted".to_string());
        Ok(m)
    }

    /// Get file/directory information.
    fn stat(&self, path: String) -> PyResult<Py<PyAny>> {
        let fs = self.fs.clone();
        let info = Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.stat(&path).await })
            })
        })
        .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = file_info_to_py_dict(py, &info)?;
            Ok(dict.into())
        })
    }

    /// Rename/move a file or directory.
    fn mv(&self, old_path: String, new_path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.rename(&old_path, &new_path).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "renamed".to_string());
        Ok(m)
    }

    /// Change file permissions.
    fn chmod(&self, path: String, mode: u32) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.chmod(&path, mode).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "chmod ok".to_string());
        Ok(m)
    }

    /// Touch a file (create if not exists, or update timestamp).
    fn touch(&self, path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move {
                    // Try create; if already exists, write empty to update mtime
                    match fs.create(&path).await {
                        Ok(_) => Ok(()),
                        Err(_) => {
                            // File exists, write empty bytes to update timestamp
                            fs.write(&path, &[], 0, WriteFlag::None).await.map(|_| ())
                        }
                    }
                })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "touched".to_string());
        Ok(m)
    }

    /// List all mounted plugins.
    fn mounts(&self) -> PyResult<Vec<HashMap<String, String>>> {
        let fs = self.fs.clone();
        let mount_list = Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.list_mounts().await })
            })
        });

        let result: Vec<HashMap<String, String>> = mount_list
            .into_iter()
            .map(|(path, fstype)| {
                let mut m = HashMap::new();
                m.insert("path".to_string(), path);
                m.insert("fstype".to_string(), fstype);
                m
            })
            .collect();

        Ok(result)
    }

    /// Mount a plugin dynamically.
    ///
    /// Args:
    ///     fstype: Filesystem type (e.g., "memfs", "sqlfs", "kvfs", "queuefs")
    ///     path: Mount path
    ///     config: Plugin configuration as dict
    #[pyo3(signature = (fstype, path, config=None))]
    fn mount(
        &self,
        fstype: String,
        path: String,
        config: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<HashMap<String, String>> {
        let params = match config {
            Some(dict) => py_dict_to_config(dict)?,
            None => HashMap::new(),
        };

        let plugin_config = PluginConfig {
            name: fstype.clone(),
            mount_path: path.clone(),
            params,
        };

        let fs = self.fs.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.mount(plugin_config).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            format!("mounted {} at {}", fstype, path),
        );
        Ok(m)
    }

    /// Unmount a plugin.
    fn unmount(&self, path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        let path_clone = path.clone();
        Python::attach(|py| {
            py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.unmount(&path_clone).await })
            })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), format!("unmounted {}", path));
        Ok(m)
    }

    /// List all registered plugin names.
    fn list_plugins(&self) -> PyResult<Vec<String>> {
        // Return names of built-in plugins
        let plugins = vec![
            "memfs".to_string(),
            "kvfs".to_string(),
            "queuefs".to_string(),
            "sqlfs".to_string(),
            "localfs".to_string(),
            "serverinfofs".to_string(),
            #[cfg(feature = "s3")]
            "s3fs".to_string(),
        ];
        Ok(plugins)
    }

    /// Get detailed plugin information.
    fn get_plugins_info(&self) -> PyResult<Vec<String>> {
        self.list_plugins()
    }

    /// Load an external plugin (not supported in Rust binding).
    fn load_plugin(&self, _library_path: String) -> PyResult<HashMap<String, String>> {
        Err(PyRuntimeError::new_err(
            "External plugin loading not supported in ragfs-python binding",
        ))
    }

    /// Unload an external plugin (not supported in Rust binding).
    fn unload_plugin(&self, _library_path: String) -> PyResult<HashMap<String, String>> {
        Err(PyRuntimeError::new_err(
            "External plugin unloading not supported in ragfs-python binding",
        ))
    }

    /// Search for pattern in files using regular expressions.
    ///
    /// Args:
    ///     path: File or directory path to search
    ///     pattern: Regular expression pattern to search for
    ///     recursive: Whether to search recursively in subdirectories (default: false)
    ///     case_insensitive: Whether to perform case-insensitive matching (default: false)
    ///     stream: Not supported in binding mode
    ///     node_limit: Maximum number of matches to return (default: None, no limit)
    ///     exclude_path: Optional path prefix to exclude from search (default: None)
    ///     level_limit: Optional maximum depth relative to query root (default: None)
    ///
    /// Returns:
    ///     A dict with "matches" (list of match dicts) and "count" (total matches)
    #[pyo3(signature = (path, pattern, recursive=false, case_insensitive=false, stream=false, node_limit=None, exclude_path=None, level_limit=None))]
    fn grep(
        &self,
        path: String,
        pattern: String,
        recursive: bool,
        case_insensitive: bool,
        stream: bool,
        node_limit: Option<i32>,
        exclude_path: Option<String>,
        level_limit: Option<i32>,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs = self.fs.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let result = self
            .rt
            .block_on(async move {
                fs.grep(
                    &path,
                    &pattern,
                    recursive,
                    case_insensitive,
                    limit,
                    exclude_path.as_deref(),
                    level_limit_usize,
                )
                .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = grep_result_to_py_dict(py, &result)?;
            Ok(dict.into())
        })
    }

    /// Recursively traverse a directory tree.
    ///
    /// Args:
    ///     path: The root path of the traversal
    ///     show_hidden: Whether to include hidden files (default: False)
    ///     node_limit: Maximum number of nodes to return (default: None, no limit)
    ///     level_limit: Maximum depth relative to query root (default: None, no limit)
    ///
    /// Returns:
    ///     A list of dicts, each with keys: path, rel_path, info, extra
    #[pyo3(signature = (path, show_hidden=false, node_limit=None, level_limit=None))]
    fn tree_directory(
        &self,
        path: String,
        show_hidden: bool,
        node_limit: Option<i32>,
        level_limit: Option<i32>,
    ) -> PyResult<Py<PyAny>> {
        let fs = self.fs.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let entries = self
            .rt
            .block_on(async move {
                fs.tree_directory(&path, show_hidden, limit, level_limit_usize)
                    .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let list = PyList::empty(py);
            for entry in &entries {
                let dict = tree_entry_to_py_dict(py, entry)?;
                list.append(dict)?;
            }
            Ok(list.into())
        })
    }

    /// Calculate file digest (not yet implemented in ragfs).
    #[pyo3(signature = (path, algorithm="xxh3"))]
    fn digest(&self, path: String, algorithm: &str) -> PyResult<HashMap<String, String>> {
        let _ = (path, algorithm);
        Err(PyRuntimeError::new_err(
            "digest not yet implemented in ragfs-python",
        ))
    }

    /// Get filesystem statistics.
    ///
    /// Args:
    ///     path: Optional mount path to get stats for. If None, get stats for all mounts.
    ///
    /// Returns:
    ///     Statistics data as a dict.
    #[pyo3(signature = (path=None))]
    fn get_stats(&self, path: Option<String>) -> PyResult<Py<PyAny>> {
        let fs = self.fs.clone();

        Python::attach(|py| {
            if let Some(mount_path) = path {
                // Get stats for a specific mount
                let fs2 = fs.clone();
                let mount_path_clone = mount_path.clone();
                let stats = py_detach_blocking(py, move || {
                    self.rt
                        .block_on(async move { fs.get_mount_stats(&mount_path_clone).await })
                })
                .map_err(to_py_err)?;

                let mounts = py_detach_blocking(py, move || {
                    self.rt.block_on(async move { fs2.list_mounts().await })
                });

                let plugin_name = mounts
                    .into_iter()
                    .find(|(p, _)| p == &mount_path)
                    .map(|(_, plugin)| plugin)
                    .unwrap_or_default();

                let result = PyDict::new(py);
                result.set_item("path", mount_path)?;
                result.set_item("plugin", plugin_name)?;
                let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                result.set_item("stats", stats_dict)?;
                Ok(result.into())
            } else {
                // Get stats for all mounts
                let all_stats = py_detach_blocking(py, move || {
                    self.rt.block_on(async move { fs.get_all_stats().await })
                });

                let mounts_list = PyList::empty(py);
                for (path, (plugin, stats)) in all_stats {
                    let mount_dict = PyDict::new(py);
                    mount_dict.set_item("path", path)?;
                    mount_dict.set_item("plugin", plugin)?;
                    let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                    mount_dict.set_item("stats", stats_dict)?;
                    mounts_list.append(mount_dict)?;
                }

                let result = PyDict::new(py);
                result.set_item("mounts", mounts_list)?;
                Ok(result.into())
            }
        })
    }
}

/// Python module definition
#[pymodule]
fn ragfs_python(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RAGFSBindingClient>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn detach_blocking_helper_runs_without_python_objects() {
        Python::initialize();
        Python::attach(|py| {
            let value: i32 = py_detach_blocking(py, || 40 + 2);
            assert_eq!(value, 42);
        });
    }

    #[tokio::test]
    async fn cache_provider_factory_creates_memory_provider_from_ov_conf() {
        let path = std::env::temp_dir().join(format!(
            "openviking-cache-config-{}.json",
            std::process::id()
        ));
        fs::write(
            &path,
            r#"{
                "storage": {
                    "agfs": {
                        "cache": {
                            "enabled": true,
                            "provider": "memory",
                            "namespace": "ov-test"
                        }
                    }
                }
            }"#,
        )
        .unwrap();

        let cache_config = cache_config_from_ov_conf(path.to_str().unwrap()).unwrap();
        let provider = CacheProviderFactory::create(&cache_config).await.unwrap();

        assert!(cache_config.enabled);
        assert_eq!(cache_config.provider, CacheProviderKind::Memory);
        assert_eq!(cache_config.namespace, "ov-test");
        assert_eq!(provider.name(), "memory");

        fs::remove_file(path).unwrap();
    }

    #[test]
    fn missing_cache_config_defaults_to_disabled_memory_config() {
        let path = std::env::temp_dir().join(format!(
            "openviking-no-cache-config-{}.json",
            std::process::id()
        ));
        fs::write(&path, r#"{"storage": {"agfs": {"backend": "local"}}}"#).unwrap();

        let cache_config = cache_config_from_ov_conf(path.to_str().unwrap()).unwrap();

        assert!(!cache_config.enabled);
        assert_eq!(cache_config.provider, CacheProviderKind::Memory);
        assert_eq!(cache_config.namespace, "openviking");

        fs::remove_file(path).unwrap();
    }

    #[test]
    fn redis_cache_config_is_parsed_from_ov_conf() {
        let path = std::env::temp_dir().join(format!(
            "openviking-redis-cache-config-{}.json",
            std::process::id()
        ));
        fs::write(
            &path,
            r#"{
                "storage": {
                    "agfs": {
                        "cache": {
                            "enabled": true,
                            "provider": "redis",
                            "namespace": "ov-test",
                            "redis": {
                                "mode": "standalone",
                                "endpoints": ["redis://127.0.0.1:6379"],
                                "pool_size": 8,
                                "connect_timeout_ms": 1000,
                                "command_timeout_ms": 20,
                                "key_prefix": "ragfs-cache",
                                "default_ttl_seconds": 3600,
                                "read_from_replica": false
                            }
                        }
                    }
                }
            }"#,
        )
        .unwrap();

        let cache_config = cache_config_from_ov_conf(path.to_str().unwrap()).unwrap();

        assert!(cache_config.enabled);
        assert_eq!(cache_config.provider, CacheProviderKind::Redis);
        assert_eq!(cache_config.redis.mode, "standalone");
        assert_eq!(cache_config.redis.endpoints, vec!["redis://127.0.0.1:6379"]);
        assert_eq!(cache_config.redis.pool_size, 8);
        assert_eq!(cache_config.redis.connect_timeout_ms, 1000);
        assert_eq!(cache_config.redis.command_timeout_ms, 20);
        assert_eq!(cache_config.redis.key_prefix, "ragfs-cache");
        assert_eq!(cache_config.redis.default_ttl_seconds, 3600);
        assert!(!cache_config.redis.read_from_replica);

        fs::remove_file(path).unwrap();
    }

    #[cfg(not(feature = "cache-redis"))]
    #[tokio::test]
    async fn redis_provider_requires_cache_redis_feature() {
        let config = RagfsCacheConfig {
            enabled: true,
            provider: CacheProviderKind::Redis,
            ..RagfsCacheConfig::default()
        };

        let error = match CacheProviderFactory::create(&config).await {
            Ok(provider) => panic!("unexpected provider: {}", provider.name()),
            Err(error) => error,
        };

        assert!(matches!(
            error,
            CacheError::Unavailable(message)
                if message == "Redis support requires the cache-redis feature"
        ));
    }

    #[cfg(feature = "cache-redis")]
    #[tokio::test]
    async fn cache_provider_factory_creates_redis_provider_from_ov_conf() {
        let Ok(endpoint) = std::env::var("REDIS_URL") else {
            return;
        };
        let path = std::env::temp_dir().join(format!(
            "openviking-cache-redis-factory-{}.json",
            std::process::id()
        ));
        fs::write(
            &path,
            format!(
                r#"{{
                    "storage": {{
                        "agfs": {{
                            "cache": {{
                                "enabled": true,
                                "provider": "redis",
                                "namespace": "ov-test",
                                "redis": {{
                                    "mode": "standalone",
                                    "endpoints": ["{endpoint}"],
                                    "connect_timeout_ms": 30000,
                                    "command_timeout_ms": 1000,
                                    "key_prefix": "ragfs-python-cache-test",
                                    "default_ttl_seconds": 60
                                }}
                            }}
                        }}
                    }}
                }}"#
            ),
        )
        .unwrap();

        let cache_config = cache_config_from_ov_conf(path.to_str().unwrap()).unwrap();
        let provider = CacheProviderFactory::create(&cache_config).await.unwrap();

        assert_eq!(provider.name(), "redis");
        provider.close().await.unwrap();

        fs::remove_file(path).unwrap();
    }
}
