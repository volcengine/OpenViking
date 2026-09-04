//! Internal provider interface used by CacheRuntime.

use super::{
    CacheError, CacheResult, ListInsertRequest, ListMoveRequest, ScriptRequest, ScriptResult,
    SetOptions, SetResult,
};
use async_trait::async_trait;
use bytes::Bytes;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CacheOperation {
    Get,
    Set,
    Del,
    Mget,
    Mset,
    IncrBy,
    Sismember,
    Smembers,
    Scard,
    Lpush,
    Rpush,
    Lpop,
    Rpop,
    Llen,
    Lrange,
    Lindex,
    Lset,
    Ltrim,
    Lrem,
    Linsert,
    Lmove,
    ExecuteScript,
}

impl CacheOperation {
    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Get => "get",
            Self::Set => "set",
            Self::Del => "del",
            Self::Mget => "mget",
            Self::Mset => "mset",
            Self::IncrBy => "incrby",
            Self::Sismember => "sismember",
            Self::Smembers => "smembers",
            Self::Scard => "scard",
            Self::Lpush => "lpush",
            Self::Rpush => "rpush",
            Self::Lpop => "lpop",
            Self::Rpop => "rpop",
            Self::Llen => "llen",
            Self::Lrange => "lrange",
            Self::Lindex => "lindex",
            Self::Lset => "lset",
            Self::Ltrim => "ltrim",
            Self::Lrem => "lrem",
            Self::Linsert => "linsert",
            Self::Lmove => "lmove",
            Self::ExecuteScript => "execute_script",
        }
    }
}

#[async_trait]
pub(crate) trait CacheProvider: Send + Sync {
    fn validate_operations(&self, _operations: &[CacheOperation]) -> CacheResult<()> {
        Ok(())
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>>;
    async fn set(&self, key: &str, value: Bytes, options: SetOptions) -> CacheResult<SetResult>;
    async fn del(&self, keys: &[String]) -> CacheResult<u64>;
    async fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>>;
    async fn mset(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()>;
    async fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64>;
    async fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool>;
    async fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>>;
    async fn scard(&self, key: &str) -> CacheResult<u64>;
    async fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64>;
    async fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64>;
    async fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>>;
    async fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>>;
    async fn llen(&self, key: &str) -> CacheResult<u64>;
    async fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>>;
    async fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>>;
    async fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()>;
    async fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()>;
    async fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64>;
    async fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64>;
    async fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>>;

    async fn execute_script(&self, request: ScriptRequest) -> CacheResult<ScriptResult> {
        Err(CacheError::UnsupportedScript(request.script_id))
    }

    async fn ping(&self) -> CacheResult<()> {
        Ok(())
    }

    async fn close(&self) -> CacheResult<()> {
        Ok(())
    }
}
