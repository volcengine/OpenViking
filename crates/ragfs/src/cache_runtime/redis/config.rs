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
    /// Command timeout in milliseconds.
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
            connect_timeout_ms: 1_000,
            command_timeout_ms: 20,
            default_ttl_seconds: 3_600,
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
        if self.connect_timeout_ms == 0 || self.command_timeout_ms == 0 {
            return Err(CacheError::InvalidArgument(
                "Redis timeouts must be greater than zero".into(),
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_standalone_cluster_and_sentinel_modes() {
        let standalone = RedisProviderConfig::default();
        assert_eq!(
            standalone.deployment_mode().unwrap(),
            RedisDeploymentMode::Standalone
        );

        let cluster = RedisProviderConfig {
            mode: "cluster".into(),
            endpoints: vec![
                "redis://127.0.0.1:7000".into(),
                "redis://127.0.0.1:7001".into(),
            ],
            db: 0,
            ..RedisProviderConfig::default()
        };
        assert_eq!(
            cluster.deployment_mode().unwrap(),
            RedisDeploymentMode::Cluster
        );
        cluster.validate().unwrap();

        let sentinel = RedisProviderConfig {
            mode: "sentinel".into(),
            endpoints: vec!["redis://127.0.0.1:26379".into()],
            master_name: Some("mymaster".into()),
            ..RedisProviderConfig::default()
        };
        assert_eq!(
            sentinel.deployment_mode().unwrap(),
            RedisDeploymentMode::Sentinel
        );
        sentinel.validate().unwrap();
    }

    #[test]
    fn rejects_removed_singleton_mode() {
        let config = RedisProviderConfig {
            mode: "singleton".into(),
            ..RedisProviderConfig::default()
        };

        assert!(matches!(
            config.validate(),
            Err(CacheError::InvalidArgument(message))
                if message.contains("standalone, cluster, or sentinel")
        ));
    }

    #[test]
    fn validates_topology_specific_settings() {
        let standalone = RedisProviderConfig {
            endpoints: vec![
                "redis://127.0.0.1:6379".into(),
                "redis://127.0.0.1:6380".into(),
            ],
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            standalone.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("exactly one")
        ));

        let cluster = RedisProviderConfig {
            mode: "cluster".into(),
            db: 1,
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            cluster.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("db=0")
        ));

        let sentinel = RedisProviderConfig {
            mode: "sentinel".into(),
            master_name: None,
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            sentinel.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("master_name")
        ));
    }

    #[test]
    fn rejects_endpoint_credentials_and_invalid_tls_settings() {
        let credentials = RedisProviderConfig {
            endpoints: vec!["redis://user:secret@127.0.0.1:6379".into()],
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            credentials.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("credentials")
        ));

        let insecure_without_tls = RedisProviderConfig {
            tls_insecure_skip_verify: true,
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            insecure_without_tls.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("rediss://")
        ));

        let insecure_rediss = RedisProviderConfig {
            endpoints: vec!["rediss://127.0.0.1:6380".into()],
            tls_insecure_skip_verify: true,
            ..RedisProviderConfig::default()
        };
        insecure_rediss.validate().unwrap();

        let mixed_schemes = RedisProviderConfig {
            mode: "cluster".into(),
            endpoints: vec![
                "redis://127.0.0.1:7000".into(),
                "rediss://127.0.0.1:7001".into(),
            ],
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            mixed_schemes.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("same URL scheme")
        ));
    }

    #[test]
    fn rejects_default_ttl_that_cannot_be_sent_as_milliseconds() {
        let config = RedisProviderConfig {
            default_ttl_seconds: u64::MAX,
            ..RedisProviderConfig::default()
        };

        assert!(matches!(
            config.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("TTL")
        ));
    }

    #[test]
    fn rejects_ambiguous_secrets_and_redacts_plaintext_values() {
        let config = RedisProviderConfig {
            password_env: "OV_REDIS_PASSWORD".into(),
            password: "plain-secret".into(),
            ..RedisProviderConfig::default()
        };
        assert!(matches!(
            config.validate(),
            Err(CacheError::InvalidArgument(message)) if message.contains("cannot both")
        ));

        let debug = format!(
            "{:?}",
            RedisProviderConfig {
                password: "plain-secret".into(),
                sentinel_password: "sentinel-secret".into(),
                ..RedisProviderConfig::default()
            }
        );
        assert!(!debug.contains("plain-secret"));
        assert!(!debug.contains("sentinel-secret"));
    }
}
