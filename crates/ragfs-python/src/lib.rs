//! Python bindings for RAGFS - Rust AGFS filesystem
//!
//! Provides `RAGFSBindingClient`, a PyO3 native class that is API-compatible
//! with the existing Go-based `AGFSBindingClient`. This embeds the ragfs
//! filesystem engine directly in the Python process (no HTTP server needed).

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyType};
use std::collections::HashMap;
use std::fs;
use std::future::Future;
use std::sync::Arc;
use std::time::UNIX_EPOCH;

use ragfs::cache::{
    CacheError, CacheNamespace, CachePolicy, CacheProvider, CacheResult, CacheTraversalMode,
    MemoryCacheProvider,
};
use ragfs::core::builder::EncryptionConfig;
use ragfs::core::{
    build_default_stack, register_builtin_plugins, ConfigValue, FileInfo, FileSystem,
    FilesystemStats, FsContext, FsContextInner, FsOperation, GrepResult, MountableFS,
    OperationStats, PluginConfig, RagfsConfig, StatsWrappedFS, TreeEntry, WriteFlag, FS_CTX,
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
    traversal_mode: CacheTraversalMode,
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
            traversal_mode: CacheTraversalMode::Backend,
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

    match json
        .get("storage")
        .and_then(|storage| storage.get("agfs"))
        .and_then(|agfs| agfs.get("cache"))
    {
        Some(cache) => cache_config_from_value(cache),
        None => Ok(RagfsCacheConfig::default()),
    }
}

fn cache_config_from_value(cache: &serde_json::Value) -> Result<RagfsCacheConfig, String> {
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
    config.traversal_mode = traversal_mode(string_field(cache, "traversal_mode", "backend")?)?;
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

fn traversal_mode(value: String) -> Result<CacheTraversalMode, String> {
    match value.as_str() {
        "backend" => Ok(CacheTraversalMode::Backend),
        "cached_traversal" => Ok(CacheTraversalMode::CachedTraversal),
        other => Err(format!(
            "unsupported storage.agfs.cache.traversal_mode: {other}; expected backend or cached_traversal"
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
        CachePolicy::new(config.max_file_size_bytes).with_traversal_mode(config.traversal_mode),
        |policy, prefix| policy.with_bypass_prefix(prefix),
    )
}

mod git;

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
        ragfs::core::Error::SyncWriteQuorum { .. } => new_py_err("AGFSInternalError", msg),
        ragfs::core::Error::ContextMissing(_) => new_py_err("AGFSInternalError", msg),
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

/// Convert a Python dict to HashMap<String, ConfigValue>, with recursive nesting support.
fn py_dict_to_config(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, ConfigValue>> {
    let mut params = HashMap::new();
    for (k, v) in dict.iter() {
        let key: String = k.extract()?;
        let value = py_any_to_config_value(&v)?;
        params.insert(key, value);
    }
    Ok(params)
}

/// Convert a single Python value to ConfigValue, recursing into dicts/lists.
fn py_any_to_config_value(v: &Bound<'_, PyAny>) -> PyResult<ConfigValue> {
    if let Ok(s) = v.extract::<String>() {
        Ok(ConfigValue::String(s))
    } else if let Ok(b) = v.extract::<bool>() {
        Ok(ConfigValue::Bool(b))
    } else if let Ok(i) = v.extract::<i64>() {
        Ok(ConfigValue::Int(i))
    } else if let Ok(list) = v.downcast::<PyList>() {
        let mut strings = Vec::new();
        let mut has_nested = false;
        for item in list.iter() {
            if let Ok(s) = item.extract::<String>() {
                strings.push(s);
            } else {
                has_nested = true;
                break;
            }
        }
        if has_nested || strings.is_empty() {
            // Convert to serde_json::Value for complex lists
            let json_val = py_to_json_value(v)?;
            Ok(ConfigValue::Json(json_val))
        } else {
            Ok(ConfigValue::StringList(strings))
        }
    } else if v.is_instance_of::<PyDict>() {
        let json_val = py_to_json_value(v)?;
        Ok(ConfigValue::Json(json_val))
    } else {
        // Fallback: try string representation
        Ok(ConfigValue::String(v.str()?.to_string()))
    }
}

/// Convert a Python value to serde_json::Value recursively.
fn py_to_json_value(v: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    if v.is_none() {
        Ok(serde_json::Value::Null)
    } else if let Ok(b) = v.extract::<bool>() {
        Ok(serde_json::Value::Bool(b))
    } else if let Ok(i) = v.extract::<i64>() {
        Ok(serde_json::Value::Number(i.into()))
    } else if let Ok(f) = v.extract::<f64>() {
        if let Some(n) = serde_json::Number::from_f64(f) {
            Ok(serde_json::Value::Number(n))
        } else {
            Ok(serde_json::Value::String(f.to_string()))
        }
    } else if let Ok(s) = v.extract::<String>() {
        Ok(serde_json::Value::String(s))
    } else if let Ok(list) = v.downcast::<PyList>() {
        let mut arr = Vec::new();
        for item in list.iter() {
            arr.push(py_to_json_value(&item)?);
        }
        Ok(serde_json::Value::Array(arr))
    } else if let Ok(dict) = v.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (k, val) in dict.iter() {
            let key: String = k.extract()?;
            map.insert(key, py_to_json_value(&val)?);
        }
        Ok(serde_json::Value::Object(map))
    } else {
        Ok(serde_json::Value::String(v.str()?.to_string()))
    }
}

/// Build an immutable `FsContext` from an optional Python `{"account_id": str}` dict.
///
/// A context is always constructed (even when the dict is absent or lacks `account_id`), so the
/// "every ragfs call carries a ctx" invariant holds. Missing `account_id` is represented as an
/// empty field; `FsContextView::account_id()` treats it as absent, so encrypted content operations
/// fail fast instead of using an accidental empty tenant key.
fn build_fs_context(ctx: Option<HashMap<String, String>>) -> FsContext {
    let account_id = ctx
        .and_then(|m| m.get("account_id").cloned())
        .unwrap_or_default();
    Arc::new(FsContextInner::new(account_id))
}

#[derive(serde::Deserialize)]
struct BindingConfig {
    #[serde(default)]
    git: Option<ragfs::git::GitConfig>,
}

fn build_git_from_cfg(
    git_cfg: ragfs::git::GitConfig,
    fs: &Arc<MountableFS>,
    rt: &tokio::runtime::Runtime,
) -> PyResult<(Option<Arc<ragfs::git::GitService>>, Option<String>)> {
    let backend = git_cfg.backend.clone();
    let vfs = fs.clone() as Arc<dyn ragfs::core::FileSystem>;
    let _guard = rt.enter();
    let svc = git::build_git_service(&git_cfg, vfs)?;
    Ok((svc, Some(backend)))
}

fn load_git_from_config(
    path: &str,
    fs: &Arc<MountableFS>,
    rt: &tokio::runtime::Runtime,
) -> PyResult<(Option<Arc<ragfs::git::GitService>>, Option<String>)> {
    let body = std::fs::read_to_string(path).map_err(|e| {
        PyRuntimeError::new_err(format!("read config_path {}: {}", path, e))
    })?;
    let cfg: BindingConfig = toml::from_str(&body)
        .map_err(|e| PyRuntimeError::new_err(format!("parse config_path: {}", e)))?;
    match cfg.git {
        Some(git_cfg) => build_git_from_cfg(git_cfg, fs, rt),
        None => Ok((None, None)),
    }
}

/// RAGFS Python Binding Client.
///
/// Embeds the ragfs filesystem engine directly in the Python process.
/// API-compatible with the Go-based AGFSBindingClient.
#[pyclass]
struct RAGFSBindingClient {
    /// Mount manager: mount/unmount/list/get_*_stats/register_plugin.
    mountable: Arc<MountableFS>,
    /// Data entry point: `Stats(Encryption(Mountable))` when encrypted, else `Stats(Mountable)`.
    top: Arc<dyn FileSystem>,
    rt: tokio::runtime::Runtime,
    git_service: Option<Arc<ragfs::git::GitService>>,
    git_backend: Option<String>,
}

impl RAGFSBindingClient {
    /// Run an async filesystem op on the runtime, releasing the GIL, inside the FS_CTX scope.
    ///
    /// Centralizes the GIL-detach + `FS_CTX.scope` + `block_on` pattern so every data method
    /// shares one implementation (no per-method duplication).
    fn run_scoped<T, Fut, F>(&self, py: Python<'_>, ctx: FsContext, f: F) -> T
    where
        T: Send,
        Fut: Future<Output = T>,
        F: Send + FnOnce() -> Fut,
    {
        py_detach_blocking(py, move || {
            self.rt
                .block_on(FS_CTX.scope(ctx, async move { f().await }))
        })
    }
}

#[pymethods]
impl RAGFSBindingClient {
    /// Create a new RAGFS binding client.
    ///
    /// `config_path` is a deprecated compatibility parameter kept only so legacy callers using
    /// `RAGFSBindingClient(config_path=...)` do not fail. `config` is an optional sectioned dict
    /// (mirrors ov.conf). The `encryption` section, when present, carries `root_key` (32 bytes) +
    /// `provider_type` (int) and causes the stack to include an `EncryptionWrappedFS` layer.
    /// Runtime `cache` configuration takes precedence over `config_path`.
    #[new]
    #[pyo3(signature = (config_path=None, config=None, git_config_path=None))]
    fn new(
        py: Python<'_>,
        config_path: Option<&str>,
        config: Option<HashMap<String, Py<PyAny>>>,
        git_config_path: Option<&str>
    ) -> PyResult<Self> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create runtime: {}", e)))?;

        // Phase A (holding GIL): parse the sectioned config into an owned RagfsConfig.
        let mut ragfs_cfg = RagfsConfig::default();
        let mut runtime_cache_config = None;
        let mut inline_git_cfg: Option<ragfs::git::GitConfig> = None;
        if let Some(cfg) = config {
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
                runtime_cache_config =
                    Some(cache_config_from_value(&cache_value).map_err(|error| {
                        PyRuntimeError::new_err(format!("Invalid cache config: {error}"))
                    })?);
            }
            if let Some(git_obj) = cfg.get("git") {
                let git_value = py_to_json_value(git_obj.bind(py))?;
                let git_cfg: ragfs::git::GitConfig = serde_json::from_value(git_value)
                    .map_err(|e| PyRuntimeError::new_err(format!("Invalid git config: {e}")))?;
                inline_git_cfg = Some(git_cfg);
            }
        }

        let cache_config = match runtime_cache_config {
            Some(config) => config,
            None => match config_path {
                Some(path) => cache_config_from_ov_conf(path).map_err(|error| {
                    PyRuntimeError::new_err(format!("Invalid cache config: {error}"))
                })?,
                None => RagfsCacheConfig::default(),
            },
        };

        // Phase B: build the stack. Cache, when enabled, replaces the mountable
        // layer with a cache-aware mountable while preserving the same plugin
        // registration and per-backend encryption setup.
        let stack = if cache_config.enabled {
            let provider = rt
                .block_on(CacheProviderFactory::create(&cache_config))
                .map_err(|error| {
                    PyRuntimeError::new_err(format!("Failed to initialize cache provider: {error}"))
                })?;
            let mountable = Arc::new(MountableFS::with_cache(
                provider,
                CacheNamespace::new(&cache_config.namespace),
                cache_policy_from_config(&cache_config),
            ));
            rt.block_on(async {
                register_builtin_plugins(&mountable).await;
                if let Some(enc) = &ragfs_cfg.encryption {
                    mountable
                        .set_encryption_config(Some(enc.root_key), Some(enc.provider_type))
                        .await;
                }
            });
            let top: Arc<dyn FileSystem> = Arc::new(StatsWrappedFS::with_arc(
                mountable.clone() as Arc<dyn FileSystem>
            ));
            ragfs::core::RagfsStack { mountable, top }
        } else {
            rt.block_on(build_default_stack(ragfs_cfg))
        };

        // Build the git service from inline config when present; otherwise fall
        // back to loading the [git] section from a config file path.
        let (git_service, git_backend) = match inline_git_cfg {
            Some(git_cfg) => build_git_from_cfg(git_cfg, &stack.mountable, &rt)?,
            None => match git_config_path {
                Some(path) => load_git_from_config(path, &stack.mountable, &rt)?,
                None => (None, None),
            },
        };

        Ok(Self {
            mountable: stack.mountable,
            top: stack.top,
            rt,
            git_service,
            git_backend,
        })
    }

    /// Check client health.
    fn health(&self) -> PyResult<HashMap<String, String>> {
        let mut m = HashMap::new();
        m.insert("status".to_string(), "healthy".to_string());
        m.insert(
            "git_enabled".to_string(),
            if self.git_service.is_some() { "true".into() } else { "false".into() },
        );
        if let Some(b) = &self.git_backend {
            m.insert("git_backend".to_string(), b.clone());
        }
        Ok(m)
    }

    /// Commit a snapshot of the account's tree.
    #[pyo3(signature = (**kwargs))]
    fn git_commit(
        &self,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_commit_request(kw)?;
        let resp = py_detach_blocking(py, move || self.rt.block_on(svc.commit(req)))
            .map_err(|e| git::map_git_error(py, e))?;
        git::commit_response_to_pydict(py, resp)
    }

    /// Restore a project_dir subtree to the state at source_commit.
    #[pyo3(signature = (**kwargs))]
    fn git_restore(
        &self,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_restore_request(kw)?;
        let resp = py_detach_blocking(py, move || self.rt.block_on(svc.restore(req)))
            .map_err(|e| git::map_git_error(py, e))?;
        git::restore_response_to_pydict(py, resp)
    }

    /// Read a commit's metadata or a blob's bytes at a path.
    #[pyo3(signature = (**kwargs))]
    fn git_show(
        &self,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_show_request(kw)?;
        let resp = py_detach_blocking(py, move || self.rt.block_on(svc.show(req)))
            .map_err(|e| git::map_git_error(py, e))?;
        git::show_response_to_pydict(py, resp)
    }

    /// Get client capabilities.
    fn get_capabilities(&self) -> PyResult<HashMap<String, Py<PyAny>>> {
        Python::attach(|py| {
            let mut m = HashMap::new();
            m.insert(
                "version".to_string(),
                "ragfs-python".into_pyobject(py)?.into_any().unbind(),
            );
            let mut features = vec![
                "memfs",
                "kvfs",
                "queuefs",
                "sqlfs",
                "localfs",
                "serverinfofs",
            ];
            #[cfg(feature = "s3")]
            features.push("s3fs");
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
    #[pyo3(signature = (path, ctx=None))]
    fn ls(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let entries = self
            .run_scoped(py, fs_ctx, move || async move { top.read_dir(&path).await })
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
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    #[pyo3(signature = (path, offset=0, size=-1, stream=false, ctx=None))]
    fn read(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
        stream: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let off = if offset < 0 { 0u64 } else { offset as u64 };
        let sz = if size < 0 { 0u64 } else { size as u64 };

        let data = self
            .run_scoped(
                py,
                fs_ctx,
                move || async move { top.read(&path, off, sz).await },
            )
            .map_err(to_py_err)?;

        Python::attach(|py| Ok(PyBytes::new(py, &data).into()))
    }

    /// Read file content (alias for read).
    #[pyo3(signature = (path, offset=0, size=-1, stream=false, ctx=None))]
    fn cat(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
        stream: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        self.read(py, path, offset, size, stream, ctx)
    }

    /// Write data to file.
    ///
    /// Args:
    ///     path: File path
    ///     data: File content as bytes
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    #[pyo3(signature = (path, data, max_retries=3, ctx=None))]
    fn write(
        &self,
        py: Python<'_>,
        path: String,
        data: Vec<u8>,
        max_retries: i32,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<String> {
        let _ = max_retries; // not applicable for local binding
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let len = data.len();
        self.run_scoped(py, fs_ctx, move || async move {
            top.write(&path, &data, 0, WriteFlag::Create).await
        })
        .map_err(to_py_err)?;

        Ok(format!("Written {} bytes", len))
    }

    /// Create a new empty file.
    #[pyo3(signature = (path, ctx=None))]
    fn create(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move { top.create(&path).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Create a directory.
    #[pyo3(signature = (path, mode="755", ctx=None))]
    fn mkdir(
        &self,
        py: Python<'_>,
        path: String,
        mode: &str,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.mkdir(&path, mode_int).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Ensure all parent directories exist for the given path.
    #[pyo3(signature = (path, mode="755", ctx=None))]
    fn ensure_parent_dirs(
        &self,
        py: Python<'_>,
        path: String,
        mode: &str,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.ensure_parent_dirs(&path, mode_int).await
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
    #[pyo3(signature = (path, recursive=false, ctx=None))]
    fn rm(
        &self,
        py: Python<'_>,
        path: String,
        recursive: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            if recursive {
                top.remove_all(&path).await
            } else {
                top.remove(&path).await
            }
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "deleted".to_string());
        Ok(m)
    }

    /// Get file/directory information.
    #[pyo3(signature = (path, ctx=None))]
    fn stat(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let info = self
            .run_scoped(py, fs_ctx, move || async move { top.stat(&path).await })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = file_info_to_py_dict(py, &info)?;
            Ok(dict.into())
        })
    }

    /// Rename/move a file or directory.
    #[pyo3(signature = (old_path, new_path, ctx=None))]
    fn mv(
        &self,
        py: Python<'_>,
        old_path: String,
        new_path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.rename(&old_path, &new_path).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "renamed".to_string());
        Ok(m)
    }

    /// Attempt a same-mount verbatim copy and report whether the fast-path was used.
    #[pyo3(signature = (src_path, dst_path, ctx=None))]
    fn copy_within_mount(
        &self,
        py: Python<'_>,
        src_path: String,
        dst_path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, bool>> {
        let fs_ctx = build_fs_context(ctx);
        let mountable = self.mountable.clone();
        let performed = self
            .run_scoped(py, fs_ctx, move || async move {
                mountable.copy_within_mount(&src_path, &dst_path).await
            })
            .map_err(to_py_err)?;

        let mut result = HashMap::new();
        result.insert("performed".to_string(), performed);
        Ok(result)
    }

    /// Change file permissions.
    #[pyo3(signature = (path, mode, ctx=None))]
    fn chmod(
        &self,
        py: Python<'_>,
        path: String,
        mode: u32,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(
            py,
            fs_ctx,
            move || async move { top.chmod(&path, mode).await },
        )
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "chmod ok".to_string());
        Ok(m)
    }

    /// Touch a file (create if not exists, or update timestamp).
    #[pyo3(signature = (path, ctx=None))]
    fn touch(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            // Try create; if already exists, write empty to update mtime
            match top.create(&path).await {
                Ok(_) => Ok(()),
                Err(_) => {
                    let existing = top.read(&path, 0, 0).await?;
                    top.write(&path, &existing, 0, WriteFlag::Create).await?;
                    Ok(())
                }
            }
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "touched".to_string());
        Ok(m)
    }

    /// List all mounted plugins.
    fn mounts(&self, py: Python<'_>) -> PyResult<Vec<HashMap<String, String>>> {
        let mountable = self.mountable.clone();
        let mount_list = py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.list_mounts().await })
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
    ///     config: Plugin configuration as dict. For multi-write, include:
    ///         - "backups": nested dict matching BackendsConfig schema
    ///         - "server_encryption_enabled": bool
    ///         - "primary_encryption_enabled": bool
    ///         - "primary_redirects": list of redirect policy dicts
    #[pyo3(signature = (fstype, path, config=None))]
    fn mount(
        &self,
        py: Python<'_>,
        fstype: String,
        path: String,
        config: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<HashMap<String, String>> {
        let params = match config {
            Some(dict) => py_dict_to_config(dict)?,
            None => HashMap::new(),
        };
        let plugin_config = PluginConfig::from_raw_parts(fstype.clone(), path.clone(), params)
            .map_err(to_py_err)?;

        let mountable = self.mountable.clone();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.mount(plugin_config).await })
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
    fn unmount(&self, py: Python<'_>, path: String) -> PyResult<HashMap<String, String>> {
        let mountable = self.mountable.clone();
        let path_clone = path.clone();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.unmount(&path_clone).await })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), format!("unmounted {}", path));
        Ok(m)
    }

    /// List all registered plugin names.
    fn list_plugins(&self) -> PyResult<Vec<String>> {
        // Return names of built-in plugins
        let mut plugins = vec![
            "memfs".to_string(),
            "kvfs".to_string(),
            "queuefs".to_string(),
            "sqlfs".to_string(),
            "localfs".to_string(),
            "serverinfofs".to_string(),
        ];
        #[cfg(feature = "s3")]
        plugins.push("s3fs".to_string());
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
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A dict with "matches" (list of match dicts) and "count" (total matches)
    #[pyo3(signature = (path, pattern, recursive=false, case_insensitive=false, stream=false, node_limit=None, exclude_path=None, level_limit=None, ctx=None))]
    #[allow(clippy::too_many_arguments)]
    fn grep(
        &self,
        py: Python<'_>,
        path: String,
        pattern: String,
        recursive: bool,
        case_insensitive: bool,
        stream: bool,
        node_limit: Option<i32>,
        exclude_path: Option<String>,
        level_limit: Option<i32>,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let result = self
            .run_scoped(py, fs_ctx, move || async move {
                top.grep(
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
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A list of dicts, each with keys: path, rel_path, info, extra
    #[pyo3(signature = (path, show_hidden=false, node_limit=None, level_limit=None, ctx=None))]
    fn tree_directory(
        &self,
        py: Python<'_>,
        path: String,
        show_hidden: bool,
        node_limit: Option<i32>,
        level_limit: Option<i32>,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let entries = self
            .run_scoped(py, fs_ctx, move || async move {
                top.tree_directory(&path, show_hidden, limit, level_limit_usize)
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

    /// Query multi-write sync status under a file or directory path.
    ///
    /// Args:
    ///     path: Absolute AGFS path rooted at the mounted namespace
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A JSON-like dict describing pending and acknowledged backup sync state.
    #[pyo3(signature = (path, ctx=None))]
    fn system_sync_status(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let mountable = self.mountable.clone();
        let result = self
            .run_scoped(py, fs_ctx, move || async move {
                mountable.system_sync_status(&path).await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| serde_json_to_py(py, &result))
    }

    /// Manually retry lagging multi-write backup sync operations under a path.
    ///
    /// Args:
    ///     path: Absolute AGFS path rooted at the mounted namespace
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A JSON-like dict summarizing retry results per target backend.
    #[pyo3(signature = (path, ctx=None))]
    fn system_sync_retry(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let mountable = self.mountable.clone();
        let result = self
            .run_scoped(py, fs_ctx, move || async move {
                mountable.system_sync_retry(&path).await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| serde_json_to_py(py, &result))
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
    fn get_stats(&self, py: Python<'_>, path: Option<String>) -> PyResult<Py<PyAny>> {
        let fs = self.mountable.clone();

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

            Python::attach(|py| {
                let result = PyDict::new(py);
                result.set_item("path", mount_path)?;
                result.set_item("plugin", plugin_name)?;
                let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                result.set_item("stats", stats_dict)?;
                Ok(result.into())
            })
        } else {
            // Get stats for all mounts
            let all_stats = py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.get_all_stats().await })
            });

            Python::attach(|py| {
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
            })
        }
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
    use pyo3::types::PyDict;
    use std::fs;

    #[test]
    fn detach_blocking_helper_runs_without_python_objects() {
        Python::attach(|py| {
            let value: i32 = py_detach_blocking(py, || 40 + 2);
            assert_eq!(value, 42);
        });
    }

    #[test]
    fn constructor_accepts_legacy_config_path_keyword() {
        let path = std::env::temp_dir().join(format!(
            "openviking-legacy-config-path-{}.json",
            std::process::id()
        ));
        fs::write(&path, r#"{"storage": {"agfs": {"backend": "local"}}}"#).unwrap();

        Python::attach(|py| {
            let ty = py.get_type::<RAGFSBindingClient>();
            let kwargs = PyDict::new(py);
            kwargs
                .set_item("config_path", path.to_str().unwrap())
                .unwrap();
            let instance = ty.call((), Some(&kwargs));
            assert!(instance.is_ok());
        });

        fs::remove_file(path).unwrap();
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
                            "namespace": "ov-test",
                            "traversal_mode": "cached_traversal"
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
        assert_eq!(
            cache_config.traversal_mode,
            CacheTraversalMode::CachedTraversal
        );
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
        assert_eq!(cache_config.traversal_mode, CacheTraversalMode::Backend);

        fs::remove_file(path).unwrap();
    }

    #[test]
    fn cache_config_rejects_invalid_traversal_mode() {
        let path = std::env::temp_dir().join(format!(
            "openviking-invalid-cache-traversal-{}.json",
            std::process::id()
        ));
        fs::write(
            &path,
            r#"{"storage": {"agfs": {"cache": {"traversal_mode": "bogus"}}}}"#,
        )
        .unwrap();

        let error = cache_config_from_ov_conf(path.to_str().unwrap()).unwrap_err();

        assert!(error.contains("traversal_mode"));

        fs::remove_file(path).unwrap();
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
            r#"{"storage": {"agfs": {"cache": {"enabled": true, "provider": "invalid"}}}}"#,
        )
        .unwrap();

        Python::attach(|py| {
            let ty = py.get_type::<RAGFSBindingClient>();
            let cache = PyDict::new(py);
            cache.set_item("enabled", true).unwrap();
            cache.set_item("provider", "memory").unwrap();
            cache.set_item("namespace", "runtime-cache").unwrap();
            let config = PyDict::new(py);
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
