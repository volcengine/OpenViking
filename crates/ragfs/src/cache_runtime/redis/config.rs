//! Redis provider configuration.

use crate::cache_runtime::{CacheError, CacheResult};
use std::fmt;
use url::Url;

/// Redis deployment topology managed by Fred.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RedisDeploymentMode {
    /// One Redis server.
    Standalone,
    /// Redis Cluster with slot discovery.
    Cluster,
    /// Redis primary discovered through Sentinel.
    Sentinel,
}

/// Connection and execution settings for the built-in Redis provider.
#[derive(Clone, PartialEq, Eq)]
pub struct RedisProviderConfig {
    /// Redis deployment mode.
    pub mode: String,
    /// Redis endpoints.
    pub endpoints: Vec<String>,
    /// Sentinel service name when `mode=sentinel`.
    pub master_name: Option<String>,
    /// Optional ACL username.
    pub username: String,
    /// Environment variable containing the Redis password.
    pub password_env: String,
    /// Legacy plaintext Redis password. Prefer `password_env` for new configurations.
    pub password: String,
    /// Optional Sentinel ACL username.
    pub sentinel_username: String,
    /// Environment variable containing the Sentinel password.
    pub sentinel_password_env: String,
    /// Legacy plaintext Sentinel password. Prefer `sentinel_password_env` for new configurations.
    pub sentinel_password: String,
    /// Redis database number. Cluster requires database zero.
    pub db: i64,
    /// Maximum concurrent commands.
    pub pool_size: usize,
    /// Connection timeout in milliseconds.
    pub connect_timeout_ms: u64,
    /// Command timeout in milliseconds; zero disables command timeouts.
    pub command_timeout_ms: u64,
    /// Default TTL in seconds; zero disables expiration.
    pub default_ttl_seconds: u64,
    /// Disable certificate verification for `rediss://` endpoints.
    pub tls_insecure_skip_verify: bool,
}

impl Default for RedisProviderConfig {
    fn default() -> Self {
        Self {
            mode: "standalone".into(),
            endpoints: vec!["redis://127.0.0.1:6379".into()],
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

impl RedisProviderConfig {
    /// Normalize the configured deployment mode.
    pub fn deployment_mode(&self) -> CacheResult<RedisDeploymentMode> {
        match self.mode.trim().to_ascii_lowercase().as_str() {
            "standalone" => Ok(RedisDeploymentMode::Standalone),
            "cluster" => Ok(RedisDeploymentMode::Cluster),
            "sentinel" => Ok(RedisDeploymentMode::Sentinel),
            other => Err(CacheError::InvalidArgument(format!(
                "unsupported Redis mode {other}; expected standalone, cluster, or sentinel"
            ))),
        }
    }

    pub(super) fn validate(&self) -> CacheResult<()> {
        let mode = self.deployment_mode()?;
        if self.endpoints.is_empty()
            || self
                .endpoints
                .iter()
                .any(|endpoint| endpoint.trim().is_empty())
        {
            return Err(CacheError::InvalidArgument(
                "Redis endpoints must not be empty".into(),
            ));
        }
        let uses_tls = self.uses_tls()?;
        if mode == RedisDeploymentMode::Standalone && self.endpoints.len() != 1 {
            return Err(CacheError::InvalidArgument(
                "Redis standalone mode requires exactly one endpoint".into(),
            ));
        }
        if mode == RedisDeploymentMode::Cluster && self.db != 0 {
            return Err(CacheError::InvalidArgument(
                "Redis cluster mode requires db=0".into(),
            ));
        }
        if mode == RedisDeploymentMode::Sentinel
            && self
                .master_name
                .as_deref()
                .is_none_or(|value| value.trim().is_empty())
        {
            return Err(CacheError::InvalidArgument(
                "Redis sentinel mode requires master_name".into(),
            ));
        }
        if self.db < 0 || self.db > u8::MAX as i64 {
            return Err(CacheError::InvalidArgument(
                "Redis db must be between 0 and 255".into(),
            ));
        }
        if self.pool_size == 0 {
            return Err(CacheError::InvalidArgument(
                "Redis pool_size must be greater than zero".into(),
            ));
        }
        if self.connect_timeout_ms == 0 {
            return Err(CacheError::InvalidArgument(
                "Redis connect_timeout_ms must be greater than zero".into(),
            ));
        }
        if self.default_ttl_seconds.checked_mul(1_000).is_none() {
            return Err(CacheError::InvalidArgument(
                "Redis default TTL is too large".into(),
            ));
        }
        if !self.password_env.trim().is_empty() && !self.password.is_empty() {
            return Err(CacheError::InvalidArgument(
                "Redis password and password_env cannot both be configured".into(),
            ));
        }
        if !self.sentinel_password_env.trim().is_empty() && !self.sentinel_password.is_empty() {
            return Err(CacheError::InvalidArgument(
                "Redis sentinel_password and sentinel_password_env cannot both be configured"
                    .into(),
            ));
        }
        if self.tls_insecure_skip_verify && !uses_tls {
            return Err(CacheError::InvalidArgument(
                "Redis tls_insecure_skip_verify requires rediss:// endpoints".into(),
            ));
        }
        Ok(())
    }

    pub(super) fn uses_tls(&self) -> CacheResult<bool> {
        let mut schemes = self
            .endpoints
            .iter()
            .map(|endpoint| parse_endpoint(endpoint).map(|(_, _, uses_tls)| uses_tls));
        let first = schemes.next().transpose()?.unwrap_or(false);
        for scheme in schemes {
            if scheme? != first {
                return Err(CacheError::InvalidArgument(
                    "Redis endpoints must use the same URL scheme".into(),
                ));
            }
        }
        Ok(first)
    }
}

impl fmt::Debug for RedisProviderConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RedisProviderConfig")
            .field("mode", &self.mode)
            .field("endpoints", &self.endpoints)
            .field("master_name", &self.master_name)
            .field("username", &self.username)
            .field("password_env", &self.password_env)
            .field("password_configured", &!self.password.is_empty())
            .field("sentinel_username", &self.sentinel_username)
            .field("sentinel_password_env", &self.sentinel_password_env)
            .field(
                "sentinel_password_configured",
                &!self.sentinel_password.is_empty(),
            )
            .field("db", &self.db)
            .field("pool_size", &self.pool_size)
            .field("connect_timeout_ms", &self.connect_timeout_ms)
            .field("command_timeout_ms", &self.command_timeout_ms)
            .field("default_ttl_seconds", &self.default_ttl_seconds)
            .field("tls_insecure_skip_verify", &self.tls_insecure_skip_verify)
            .finish()
    }
}

pub(super) fn parse_endpoint(endpoint: &str) -> CacheResult<(String, u16, bool)> {
    let url = Url::parse(endpoint).map_err(|_| {
        CacheError::InvalidArgument(
            "Redis endpoints must use valid redis:// or rediss:// URLs".into(),
        )
    })?;
    if !matches!(url.scheme(), "redis" | "rediss") {
        return Err(CacheError::InvalidArgument(
            "Redis endpoints must use valid redis:// or rediss:// URLs".into(),
        ));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(CacheError::InvalidArgument(
            "Redis endpoints must not include credentials; use dedicated Redis fields".into(),
        ));
    }
    if !matches!(url.path(), "" | "/") || url.query().is_some() || url.fragment().is_some() {
        return Err(CacheError::InvalidArgument(
            "Redis endpoints must not include database paths, query parameters, or fragments"
                .into(),
        ));
    }
    let host = url
        .host_str()
        .filter(|host| !host.is_empty())
        .ok_or_else(|| CacheError::InvalidArgument("Redis endpoint host is missing".into()))?;
    let port = url
        .port()
        .unwrap_or(if url.scheme() == "rediss" { 6380 } else { 6379 });
    if port == 0 {
        return Err(CacheError::InvalidArgument(
            "Redis endpoint port is invalid".into(),
        ));
    }
    Ok((host.to_string(), port, url.scheme() == "rediss"))
}
