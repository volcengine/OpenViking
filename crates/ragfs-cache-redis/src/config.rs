use ragfs::cache::{CacheError, CacheResult};

/// Connection and execution settings for a Redis provider.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RedisConfig {
    /// Redis deployment mode. The first adapter stage supports standalone.
    pub mode: String,
    /// Redis endpoints. Standalone mode uses the first endpoint.
    pub endpoints: Vec<String>,
    /// Optional ACL username.
    pub username: String,
    /// Environment variable name containing the Redis password.
    pub password_env: String,
    /// Maximum number of concurrent Redis commands issued by this provider.
    pub pool_size: usize,
    /// Timeout used while establishing the Redis connection.
    pub connect_timeout_ms: u64,
    /// Timeout applied to each Redis command.
    pub command_timeout_ms: u64,
    /// Prefix prepended to every cache key before it reaches Redis.
    pub key_prefix: String,
    /// Redis TTL in seconds. Zero disables native Redis expiration.
    pub default_ttl_seconds: u64,
    /// Whether reads may use replicas. The first adapter stage is primary-only.
    pub read_from_replica: bool,
}

impl Default for RedisConfig {
    fn default() -> Self {
        Self {
            mode: "standalone".into(),
            endpoints: vec!["redis://127.0.0.1:6379".into()],
            username: String::new(),
            password_env: String::new(),
            pool_size: 32,
            connect_timeout_ms: 1_000,
            command_timeout_ms: 20,
            key_prefix: "ragfs-cache".into(),
            default_ttl_seconds: 3_600,
            read_from_replica: false,
        }
    }
}

impl RedisConfig {
    pub(crate) fn validate(&self) -> CacheResult<()> {
        if self.mode != "standalone" {
            return Err(CacheError::InvalidArgument(
                "Redis mode must be standalone in this adapter stage".into(),
            ));
        }
        if self.endpoints.is_empty() {
            return Err(CacheError::InvalidArgument(
                "Redis endpoints must not be empty".into(),
            ));
        }
        if self
            .endpoints
            .iter()
            .any(|endpoint| endpoint.trim().is_empty())
        {
            return Err(CacheError::InvalidArgument(
                "Redis endpoints must not contain empty values".into(),
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
        if self.command_timeout_ms == 0 {
            return Err(CacheError::InvalidArgument(
                "Redis command_timeout_ms must be greater than zero".into(),
            ));
        }
        if self.key_prefix.trim().is_empty() {
            return Err(CacheError::InvalidArgument(
                "Redis key_prefix must not be empty".into(),
            ));
        }
        if self.read_from_replica {
            return Err(CacheError::InvalidArgument(
                "Redis read_from_replica is not supported in standalone mode".into(),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_required_redis_settings() {
        let config = RedisConfig {
            mode: "cluster".into(),
            ..RedisConfig::default()
        };
        assert!(matches!(
            config.validate().unwrap_err(),
            CacheError::InvalidArgument(_)
        ));

        let mut config = RedisConfig::default();
        config.endpoints.clear();
        assert!(matches!(
            config.validate().unwrap_err(),
            CacheError::InvalidArgument(_)
        ));

        let config = RedisConfig {
            pool_size: 0,
            ..RedisConfig::default()
        };
        assert!(matches!(
            config.validate().unwrap_err(),
            CacheError::InvalidArgument(_)
        ));

        let mut config = RedisConfig::default();
        config.key_prefix.clear();
        assert!(matches!(
            config.validate().unwrap_err(),
            CacheError::InvalidArgument(_)
        ));
    }
}
