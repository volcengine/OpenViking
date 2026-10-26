use super::{RedisClient, RedisProviderConfig};
use crate::cache_runtime::provider::CacheProvider;
use crate::cache_runtime::{
    CacheResult, ListInsertRequest, ListMoveRequest, ScriptRegistry, ScriptRequest, ScriptResult,
    SetOptions, SetResult,
};
use async_trait::async_trait;
use bytes::Bytes;
use std::sync::Arc;
use std::time::Duration;

pub(crate) struct RedisProvider {
    client: Arc<RedisClient>,
    scripts: Arc<ScriptRegistry>,
    default_ttl: Option<Duration>,
}

impl RedisProvider {
    pub(crate) async fn connect(
        config: RedisProviderConfig,
        scripts: Arc<ScriptRegistry>,
    ) -> CacheResult<Self> {
        config.validate()?;
        let default_ttl = if config.default_ttl_seconds == 0 {
            None
        } else {
            Some(Duration::from_secs(config.default_ttl_seconds))
        };
        let client = Arc::new(RedisClient::connect(&config).await?);
        Ok(Self {
            client,
            scripts,
            default_ttl,
        })
    }
}

#[async_trait]
impl CacheProvider for RedisProvider {
    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.client.get(key).await
    }

    async fn set(&self, key: &str, value: Bytes, options: SetOptions) -> CacheResult<SetResult> {
        self.client.set(key, value, options, self.default_ttl).await
    }

    async fn del(&self, keys: &[String]) -> CacheResult<u64> {
        self.client.del(keys).await
    }

    async fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        self.client.mget(keys).await
    }

    async fn mset(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        self.client.mset(entries, self.default_ttl).await
    }

    async fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        self.client.incr_by(key, delta).await
    }

    async fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool> {
        self.client.sismember(key, member).await
    }

    async fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>> {
        self.client.smembers(key).await
    }

    async fn scard(&self, key: &str) -> CacheResult<u64> {
        self.client.scard(key).await
    }

    async fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.client.lpush(key, values).await
    }

    async fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.client.rpush(key, values).await
    }

    async fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.client.lpop(key, count).await
    }

    async fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.client.rpop(key, count).await
    }

    async fn llen(&self, key: &str) -> CacheResult<u64> {
        self.client.llen(key).await
    }

    async fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>> {
        self.client.lrange(key, start, stop).await
    }

    async fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>> {
        self.client.lindex(key, index).await
    }

    async fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()> {
        self.client.lset(key, index, value).await
    }

    async fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()> {
        self.client.ltrim(key, start, stop).await
    }

    async fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64> {
        self.client.lrem(key, count, value).await
    }

    async fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64> {
        self.client.linsert(request).await
    }

    async fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>> {
        self.client.lmove(request).await
    }

    async fn execute_script(&self, request: ScriptRequest) -> CacheResult<ScriptResult> {
        let lua = self.scripts.resolve(&request.script_id)?;
        let value = self
            .client
            .execute_script(&request.script_id, lua, request.keys, request.args)
            .await?;
        ScriptResult::encode(&value)
    }

    async fn close(&self) -> CacheResult<()> {
        self.client.close().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cache_runtime::{ScriptDefinition, ScriptRequest, ScriptValue};

    #[tokio::test]
    async fn executes_registered_script_and_recovers_after_script_flush() {
        let Ok(endpoint) = std::env::var("REDIS_URL") else {
            return;
        };
        let scripts = Arc::new(crate::cache_runtime::ScriptRegistry::default());
        scripts
            .register(ScriptDefinition {
                id: "runtime.test.echo.v1",
                redis_lua: "return {KEYS[1], ARGV[1]}",
            })
            .unwrap();
        let config = RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..RedisProviderConfig::default()
        };
        let expected_key = format!("ragfs-script-test:{}:key", std::process::id());
        let provider = Arc::new(
            RedisProvider::connect(config, Arc::clone(&scripts))
                .await
                .unwrap(),
        );
        let runtime = crate::cache_runtime::CacheRuntime::from_provider(provider.clone());
        let request = ScriptRequest {
            script_id: "runtime.test.echo.v1".into(),
            keys: vec![expected_key.clone()],
            args: vec![Bytes::from_static(b"value")],
        };

        let first = runtime.execute_script(request.clone()).await.unwrap();
        assert_eq!(
            first.decode().unwrap(),
            ScriptValue::Array(vec![
                ScriptValue::Bytes(expected_key.into_bytes()),
                ScriptValue::Bytes(b"value".to_vec()),
            ])
        );

        provider.client.script_flush().await.unwrap();
        let second = runtime.execute_script(request).await.unwrap();
        assert_eq!(second.decode().unwrap(), first.decode().unwrap());
    }

    #[tokio::test]
    async fn set_queries_preserve_binary_members_on_real_redis() {
        let Ok(endpoint) = std::env::var("REDIS_URL") else {
            return;
        };
        let scripts = Arc::new(crate::cache_runtime::ScriptRegistry::default());
        scripts
            .register(ScriptDefinition {
                id: "runtime.test.sadd.v1",
                redis_lua: "return redis.call('SADD', KEYS[1], ARGV[1], ARGV[2])",
            })
            .unwrap();
        let config = RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..RedisProviderConfig::default()
        };
        let provider = Arc::new(
            RedisProvider::connect(config, Arc::clone(&scripts))
                .await
                .unwrap(),
        );
        let runtime = crate::cache_runtime::CacheRuntime::from_provider(provider);
        let set_key = format!("ragfs-set-test:{}:members", std::process::id());
        let binary_member = Bytes::from_static(b"binary\0member");
        let text_member = Bytes::from_static(b"text-member");

        runtime
            .execute_script(ScriptRequest {
                script_id: "runtime.test.sadd.v1".into(),
                keys: vec![set_key.clone()],
                args: vec![binary_member.clone(), text_member.clone()],
            })
            .await
            .unwrap();

        assert!(runtime
            .sismember(&set_key, binary_member.as_ref())
            .await
            .unwrap());
        assert!(!runtime.sismember(&set_key, b"missing").await.unwrap());
        assert_eq!(runtime.scard(&set_key).await.unwrap(), 2);
        let mut members = runtime.smembers(&set_key).await.unwrap();
        members.sort();
        let mut expected = vec![binary_member, text_member];
        expected.sort();
        assert_eq!(members, expected);

        runtime.del(&[set_key]).await.unwrap();
        runtime.close().await.unwrap();
    }
}
