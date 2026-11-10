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

    async fn unlink(&self, keys: &[String]) -> CacheResult<u64> {
        self.client.unlink(keys).await
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

    async fn expire(&self, key: &str, duration: Duration) -> CacheResult<bool> {
        self.client.expire(key, duration).await
    }

    async fn hget(&self, key: &str, field: &str) -> CacheResult<Option<Bytes>> {
        self.client.hget(key, field).await
    }

    async fn hset(&self, key: &str, field: &str, value: Bytes) -> CacheResult<u64> {
        self.client.hset(key, field, value).await
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

    async fn zadd(&self, key: &str, score: f64, member: &str) -> CacheResult<u64> {
        self.client.zadd(key, score, member).await
    }

    async fn zscan_match(&self, key: &str, pattern: &str, count: u32) -> CacheResult<Vec<String>> {
        self.client.zscan_match(key, pattern, count).await
    }

    /// Return sorted-set members within one score range.
    async fn zrangebyscore(
        &self,
        key: &str,
        min: f64,
        max: f64,
        limit: u32,
    ) -> CacheResult<Vec<String>> {
        self.client.zrangebyscore(key, min, max, limit).await
    }

    async fn zrem(&self, key: &str, members: &[String]) -> CacheResult<u64> {
        self.client.zrem(key, members).await
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
