//! Redis-backed PathLock storage.

use std::sync::Arc;

use async_trait::async_trait;
use bytes::Bytes;

use crate::cache_runtime::{
    CacheError, CacheOperation, CacheRuntime, ScriptDefinition, ScriptRequest, ScriptValue,
};

use super::codec::LockTokenCodec;
use super::provider::{AcquisitionChange, AtomicAcquisition, PathLockHandleMode, PathLockProvider};
use super::types::{LockToken, PathLockError, PathLockKind, PathLockRequest, PathLockResult};

const ACQUIRE_BATCH_ID: &str = "pathlock.acquire_batch.v1";
const ROLLBACK_BATCH_ID: &str = "pathlock.rollback_batch.v1";
const COMPARE_WRITE_ID: &str = "pathlock.compare_write.v1";
const COMPARE_REMOVE_ID: &str = "pathlock.compare_remove.v1";
const REFRESH_ID: &str = "pathlock.refresh.v1";
const IS_LOCKED_ID: &str = "pathlock.is_locked.v1";
const SCAN_DESCENDANTS_ID: &str = "pathlock.scan_descendants.v1";
const READ_MANY_ID: &str = "pathlock.read_many.v1";
const CREATE_ID: &str = "pathlock.create.v1";

macro_rules! token_script {
    ($body:literal) => {
        concat!(
            r#"
local u128_max = "340282366920938463463374607431768211455"
local function normalize_decimal(value)
    if not value or not string.match(value, "^%d+$") then
        return nil
    end
    local normalized = string.gsub(value, "^0+", "")
    if normalized == "" then
        return "0"
    end
    if string.len(normalized) > string.len(u128_max)
        or (string.len(normalized) == string.len(u128_max)
            and normalized > u128_max) then
        return nil
    end
    return normalized
end

local function decimal_less(left, right)
    left = normalize_decimal(left)
    right = normalize_decimal(right)
    if not left or not right then
        return nil
    end
    if string.len(left) ~= string.len(right) then
        return string.len(left) < string.len(right)
    end
    return left < right
end
local function decode_token(raw)
    local p1 = string.find(raw, ":", 1, true)
    local p2 = p1 and string.find(raw, ":", p1 + 1, true)
    local p3 = p2 and string.find(raw, ":", p2 + 1, true)
    if not p1 or not p2 or p3 then
        return nil
    end
    local token_owner = string.sub(raw, 1, p1 - 1)
    local time_ns = string.sub(raw, p1 + 1, p2 - 1)
    local kind = string.sub(raw, p2 + 1)
    if token_owner == "" or not normalize_decimal(time_ns)
        or (kind ~= "E" and kind ~= "T") then
        return nil
    end
    return { owner = token_owner, time_ns = time_ns, kind = kind }
end
local function ancestors(path)
    local result = {}
    if path == "/" then
        return result
    end
    table.insert(result, "/")
    local start = 2
    while true do
        local slash = string.find(path, "/", start, true)
        if not slash then
            break
        end
        table.insert(result, string.sub(path, 1, slash - 1))
        start = slash + 1
    end
    return result
end
"#,
            $body
        )
    };
}

const ACQUIRE_BATCH_SCRIPT: &str = token_script!(
    r#"
local key = KEYS[1]
local owner = ARGV[1]
local now_ns = ARGV[2]
local stale_before_ns = ARGV[3]
local ttl_ms = tonumber(ARGV[4])
local function is_descendant(path, root)
    if root == "/" then
        return path ~= "/"
    end
    return string.sub(path, 1, string.len(root) + 1) == root .. "/"
end

if not owner or owner == "" or string.find(owner, ":", 1, true)
    or not normalize_decimal(now_ns) or not normalize_decimal(stale_before_ns)
    or not ttl_ms or ttl_ms <= 0 or (#ARGV - 4) % 2 ~= 0 or #ARGV == 4 then
    return { "invalid_request", "invalid acquire arguments" }
end

local requests = {}
local includes_tree = false
for index = 5, #ARGV, 2 do
    local path = ARGV[index]
    local kind = ARGV[index + 1]
    if not path or string.sub(path, 1, 1) ~= "/"
        or (path ~= "/" and string.sub(path, -1) == "/")
        or (kind ~= "E" and kind ~= "T") then
        return { "invalid_request", "invalid path or lock kind" }
    end
    table.insert(requests, { path = path, kind = kind })
    if kind == "T" then
        includes_tree = true
    end
end

local records = {}
if includes_tree then
    local values = redis.call("HGETALL", key)
    for index = 1, #values, 2 do
        local path = values[index]
        local raw = values[index + 1]
        local token = decode_token(raw)
        if not token then
            return { "invalid_token", path, raw }
        end
        records[path] = { raw = raw, token = token }
    end
else
    local fields = {}
    local seen = {}
    local function add_field(path)
        if not seen[path] then
            seen[path] = true
            table.insert(fields, path)
        end
    end
    for _, request in ipairs(requests) do
        add_field(request.path)
        for _, ancestor in ipairs(ancestors(request.path)) do
            add_field(ancestor)
        end
    end
    local values = redis.call("HMGET", key, unpack(fields))
    for index, raw in ipairs(values) do
        if raw then
            local token = decode_token(raw)
            if not token then
                return { "invalid_token", fields[index], raw }
            end
            records[fields[index]] = { raw = raw, token = token }
        end
    end
end

local stale = {}
local stale_seen = {}
local function is_other_stale(record)
    if not record or record.token.owner == owner then
        return false
    end
    return decimal_less(record.token.time_ns, stale_before_ns) == true
end
local function mark_stale(path, record)
    if is_other_stale(record) and not stale_seen[path] then
        stale_seen[path] = true
        table.insert(stale, path)
    end
end

for path, record in pairs(records) do
    mark_stale(path, record)
end

for _, request in ipairs(requests) do
    local target = records[request.path]
    if target and target.token.owner ~= owner and not is_other_stale(target) then
        return { "conflict", request.path, target.raw }
    end
    for _, ancestor in ipairs(ancestors(request.path)) do
        local record = records[ancestor]
        if record and record.token.owner ~= owner and record.token.kind == "T"
            and not is_other_stale(record) then
            return { "conflict", ancestor, record.raw }
        end
    end
    if request.kind == "T" then
        for path, record in pairs(records) do
            if is_descendant(path, request.path)
                and record.token.owner ~= owner and not is_other_stale(record) then
                return { "conflict", path, record.raw }
            end
        end
    end
end

local changes = {}
for _, request in ipairs(requests) do
    local existing = records[request.path]
    local replacement = owner .. ":" .. now_ns .. ":" .. request.kind
    if existing and existing.token.owner == owner then
        if request.kind == "T" and existing.token.kind == "E" then
            table.insert(changes, {
                path = request.path,
                change = "upgraded",
                previous = existing.raw,
                replacement = replacement,
            })
        else
            table.insert(changes, {
                path = request.path,
                change = "reentrant",
                previous = "",
                replacement = "",
            })
        end
    else
        table.insert(changes, {
            path = request.path,
            change = "created",
            previous = "",
            replacement = replacement,
        })
    end
end

local stale_batch_size = 1000
for start_index = 1, #stale, stale_batch_size do
    local end_index = math.min(start_index + stale_batch_size - 1, #stale)
    redis.call("HDEL", key, unpack(stale, start_index, end_index))
end
for _, change in ipairs(changes) do
    if change.change ~= "reentrant" then
        redis.call("HSET", key, change.path, change.replacement)
    end
end
if redis.call("HLEN", key) > 0 then
    redis.call("PEXPIRE", key, ttl_ms)
end

local result = { "ok" }
for _, change in ipairs(changes) do
    table.insert(result, change.path)
    table.insert(result, change.change)
    table.insert(result, change.previous)
    table.insert(result, change.replacement)
end
return result
"#
);

const ROLLBACK_BATCH_SCRIPT: &str = r#"
local key = KEYS[1]
local owner = ARGV[1]
local ttl_ms = tonumber(ARGV[2])
if not owner or owner == "" or not ttl_ms or ttl_ms <= 0
    or (#ARGV - 2) % 4 ~= 0 then
    return { "invalid_request", "invalid rollback arguments" }
end

local changes = {}
for index = 3, #ARGV, 4 do
    local path = ARGV[index]
    local change = ARGV[index + 1]
    local previous = ARGV[index + 2]
    local replacement = ARGV[index + 3]
    if change ~= "created" and change ~= "reentrant" and change ~= "upgraded" then
        return { "invalid_request", "invalid acquisition change" }
    end
    if change ~= "reentrant" then
        local current = redis.call("HGET", key, path)
        if current ~= replacement then
            return { "changed", path }
        end
        if string.sub(replacement, 1, string.len(owner) + 1) ~= owner .. ":" then
            return { "invalid_request", "replacement owner mismatch" }
        end
    end
    table.insert(changes, {
        path = path,
        change = change,
        previous = previous,
    })
end

for index = #changes, 1, -1 do
    local change = changes[index]
    if change.change == "created" then
        redis.call("HDEL", key, change.path)
    elseif change.change == "upgraded" then
        redis.call("HSET", key, change.path, change.previous)
    end
end
if redis.call("HLEN", key) > 0 then
    redis.call("PEXPIRE", key, ttl_ms)
end
return { "ok" }
"#;

const COMPARE_WRITE_SCRIPT: &str = r#"
local current = redis.call("HGET", KEYS[1], ARGV[1])
if current ~= ARGV[2] then
    return 0
end
redis.call("HSET", KEYS[1], ARGV[1], ARGV[3])
redis.call("PEXPIRE", KEYS[1], ARGV[4])
return 1
"#;

const COMPARE_REMOVE_SCRIPT: &str = r#"
local current = redis.call("HGET", KEYS[1], ARGV[1])
if current ~= ARGV[2] then
    return 0
end
redis.call("HDEL", KEYS[1], ARGV[1])
if redis.call("HLEN", KEYS[1]) > 0 then
    redis.call("PEXPIRE", KEYS[1], ARGV[3])
end
return 1
"#;

const REFRESH_SCRIPT: &str = token_script!(
    r#"
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if not raw then
    return { "missing" }
end
local token = decode_token(raw)
if not token then
    return { "invalid_token", raw }
end
if token.owner ~= ARGV[2] then
    return { "owner_mismatch" }
end
if not normalize_decimal(ARGV[3]) then
    return { "invalid_request", "invalid time_ns" }
end
redis.call("HSET", KEYS[1], ARGV[1], token.owner .. ":" .. ARGV[3] .. ":" .. token.kind)
redis.call("PEXPIRE", KEYS[1], ARGV[4])
return { "ok" }
"#
);

const IS_LOCKED_SCRIPT: &str = token_script!(
    r#"
local path = ARGV[1]
local stale_before_ns = ARGV[2]
local ignore_stale = ARGV[3] == "1"

local fields = { path }
for _, ancestor in ipairs(ancestors(path)) do
    table.insert(fields, ancestor)
end
local values = redis.call("HMGET", KEYS[1], unpack(fields))
for index, raw in ipairs(values) do
    if raw then
        local token = decode_token(raw)
        if not token then
            return { "invalid_token", fields[index], raw }
        end
        local live = not ignore_stale
            or decimal_less(token.time_ns, stale_before_ns) ~= true
        if live and (index == 1 or token.kind == "T") then
            return { "ok", 1 }
        end
    end
end
return { "ok", 0 }
"#
);

const SCAN_DESCENDANTS_SCRIPT: &str = r#"
local root = ARGV[1]
local values = redis.call("HGETALL", KEYS[1])
local result = { "ok" }
for index = 1, #values, 2 do
    local path = values[index]
    local matches = root == "/" or path == root
        or string.sub(path, 1, string.len(root) + 1) == root .. "/"
    if matches then
        table.insert(result, path)
        table.insert(result, values[index + 1])
    end
end
return result
"#;

const READ_MANY_SCRIPT: &str = r#"
if #ARGV == 0 then
    return {}
end
return redis.call("HMGET", KEYS[1], unpack(ARGV))
"#;

const CREATE_SCRIPT: &str = r#"
local current = redis.call("HGET", KEYS[1], ARGV[1])
if current then
    return { "exists", current }
end
redis.call("HSET", KEYS[1], ARGV[1], ARGV[2])
redis.call("PEXPIRE", KEYS[1], ARGV[3])
return { "created" }
"#;

macro_rules! script {
    ($id:expr, $lua:expr) => {
        ScriptDefinition {
            id: $id,
            redis_lua: $lua,
        }
    };
}

const SCRIPT_DEFINITIONS: &[ScriptDefinition] = &[
    script!(ACQUIRE_BATCH_ID, ACQUIRE_BATCH_SCRIPT),
    script!(ROLLBACK_BATCH_ID, ROLLBACK_BATCH_SCRIPT),
    script!(COMPARE_WRITE_ID, COMPARE_WRITE_SCRIPT),
    script!(COMPARE_REMOVE_ID, COMPARE_REMOVE_SCRIPT),
    script!(REFRESH_ID, REFRESH_SCRIPT),
    script!(IS_LOCKED_ID, IS_LOCKED_SCRIPT),
    script!(SCAN_DESCENDANTS_ID, SCAN_DESCENDANTS_SCRIPT),
    script!(READ_MANY_ID, READ_MANY_SCRIPT),
    script!(CREATE_ID, CREATE_SCRIPT),
];

/// Redis-backed PathLock provider.
pub struct RedisPathLockProvider {
    runtime: Arc<CacheRuntime>,
    namespace: String,
    hash_ttl_ms: i64,
}

impl RedisPathLockProvider {
    /// Create Redis-backed PathLock storage and register its Lua scripts.
    pub fn new(
        runtime: Arc<CacheRuntime>,
        namespace: &str,
        lock_expire_secs: f64,
    ) -> PathLockResult<Self> {
        if namespace.is_empty()
            || !namespace
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        {
            return Err(PathLockError::InvalidRequest(
                "pathlock Redis namespace must contain only ASCII letters, digits, '.', '_' or '-'"
                    .to_string(),
            ));
        }
        if !lock_expire_secs.is_finite() || lock_expire_secs < 1.0 {
            return Err(PathLockError::InvalidRequest(
                "pathlock lock_expire_secs must be finite and >= 1.0".to_string(),
            ));
        }
        let hash_ttl_ms = (lock_expire_secs * 2_000.0).ceil();
        if hash_ttl_ms > i64::MAX as f64 {
            return Err(PathLockError::InvalidRequest(
                "pathlock Redis HASH TTL exceeds i64 milliseconds".to_string(),
            ));
        }
        runtime
            .require_operations(&[CacheOperation::ExecuteScript])
            .map_err(Self::cache_error)?;
        for definition in SCRIPT_DEFINITIONS {
            runtime
                .register_script(*definition)
                .map_err(Self::cache_error)?;
        }
        Ok(Self {
            runtime,
            namespace: namespace.to_string(),
            hash_ttl_ms: hash_ttl_ms as i64,
        })
    }

    /// Return the Redis HASH key responsible for one logical path.
    fn tokens_key_for_path(&self, path: &str) -> PathLockResult<String> {
        let suffix = if path == "/" || path == "/local" {
            "global".to_string()
        } else {
            let scope = path
                .strip_prefix("/local/")
                .and_then(|path| path.split('/').next())
                .filter(|scope| !scope.is_empty())
                .ok_or_else(|| {
                    PathLockError::InvalidRequest(format!(
                        "cache PathLock path must be '/' or below '/local': {path}"
                    ))
                })?;
            match scope {
                "_system" => "scope:_system".to_string(),
                account => format!("scope:account:{account}"),
            }
        };
        Ok(format!(
            "ov:pathlock:{{{}}}:{suffix}:tokens",
            self.namespace
        ))
    }

    /// Return the shared Redis HASH key for one same-scope path batch.
    fn tokens_key_for_batch<'a>(
        &self,
        paths: impl IntoIterator<Item = &'a str>,
    ) -> PathLockResult<String> {
        let mut paths = paths.into_iter();
        let first_path = paths.next().ok_or_else(|| {
            PathLockError::InvalidRequest("lock request batch must not be empty".to_string())
        })?;
        let key = self.tokens_key_for_path(first_path)?;
        for path in paths {
            if self.tokens_key_for_path(path)? != key {
                return Err(PathLockError::InvalidRequest(
                    "cache PathLock batch paths must belong to one scope".to_string(),
                ));
            }
        }
        Ok(key)
    }

    /// Execute one registered PathLock script and decode its provider-neutral result.
    async fn execute(
        &self,
        script_id: &str,
        key: String,
        args: Vec<Bytes>,
    ) -> PathLockResult<ScriptValue> {
        self.runtime
            .execute_script(ScriptRequest {
                script_id: script_id.to_string(),
                keys: vec![key],
                args,
            })
            .await
            .and_then(|result| result.decode())
            .map_err(Self::cache_error)
    }

    /// Execute one script and require an array result.
    async fn execute_array(
        &self,
        script_id: &str,
        key: String,
        args: Vec<Bytes>,
    ) -> PathLockResult<Vec<ScriptValue>> {
        match self.execute(script_id, key, args).await? {
            ScriptValue::Array(values) => Ok(values),
            other => Err(PathLockError::Internal(format!(
                "Redis PathLock {script_id} returned non-array result: {other:?}"
            ))),
        }
    }

    /// Execute one script and require an integer result.
    async fn execute_integer(
        &self,
        script_id: &str,
        key: String,
        args: Vec<Bytes>,
    ) -> PathLockResult<i64> {
        match self.execute(script_id, key, args).await? {
            ScriptValue::Integer(value) => Ok(value),
            other => Err(PathLockError::Internal(format!(
                "Redis PathLock {script_id} returned non-integer result: {other:?}"
            ))),
        }
    }

    /// Convert one cache runtime error into a PathLock I/O error.
    fn cache_error(error: CacheError) -> PathLockError {
        PathLockError::Io(format!("Redis PathLock operation failed: {error}"))
    }

    /// Decode one script value as UTF-8 text.
    fn result_string<'a>(value: &'a ScriptValue, operation: &str) -> PathLockResult<&'a str> {
        match value {
            ScriptValue::Bytes(value) => std::str::from_utf8(value).map_err(|error| {
                PathLockError::Internal(format!(
                    "Redis PathLock {operation} returned invalid UTF-8: {error}"
                ))
            }),
            other => Err(PathLockError::Internal(format!(
                "Redis PathLock {operation} returned non-string value: {other:?}"
            ))),
        }
    }

    /// Return the leading status string from one script array.
    fn result_status<'a>(values: &'a [ScriptValue], operation: &str) -> PathLockResult<&'a str> {
        values
            .first()
            .ok_or_else(|| {
                PathLockError::Internal(format!(
                    "Redis PathLock {operation} returned an empty result"
                ))
            })
            .and_then(|value| Self::result_string(value, operation))
    }

    /// Decode one raw Redis token and include its path in decoding errors.
    fn decode_token(path: &str, raw: &str) -> PathLockResult<LockToken> {
        LockTokenCodec::decode(raw)
            .map_err(|_| PathLockError::InvalidToken(format!("invalid token at '{path}': {raw}")))
    }

    /// Decode a conflict or invalid-token script response.
    fn decode_error_result(
        operation: &str,
        values: &[ScriptValue],
    ) -> PathLockResult<PathLockError> {
        let status = Self::result_status(values, operation)?;
        match status {
            "conflict" if values.len() == 3 => {
                let path = Self::result_string(&values[1], operation)?;
                let raw = Self::result_string(&values[2], operation)?;
                let token = Self::decode_token(path, raw)?;
                Ok(PathLockError::Conflict {
                    lock_path: path.to_string(),
                    owner: token.owner_id,
                    kind: token.lock_type,
                })
            }
            "invalid_token" if values.len() == 3 => {
                let path = Self::result_string(&values[1], operation)?;
                let raw = Self::result_string(&values[2], operation)?;
                Ok(PathLockError::InvalidToken(format!(
                    "invalid token at '{path}': {raw}"
                )))
            }
            "invalid_request" if values.len() == 2 => Ok(PathLockError::InvalidRequest(
                Self::result_string(&values[1], operation)?.to_string(),
            )),
            "changed" if values.len() == 2 => Ok(PathLockError::Io(format!(
                "Redis PathLock rollback value changed at '{}'",
                Self::result_string(&values[1], operation)?
            ))),
            _ => Ok(PathLockError::Internal(format!(
                "Redis PathLock {operation} returned invalid status '{status}'"
            ))),
        }
    }

    /// Encode one string-like script argument.
    fn bytes(value: impl AsRef<[u8]>) -> Bytes {
        Bytes::copy_from_slice(value.as_ref())
    }

    /// Return the string code used by Redis for one lock kind.
    fn kind_code(kind: PathLockKind) -> &'static str {
        match kind {
            PathLockKind::Exact => "E",
            PathLockKind::Tree => "T",
        }
    }

    /// Read and decode multiple token fields while preserving input order.
    async fn read_many_tokens(&self, paths: &[String]) -> PathLockResult<Vec<Option<LockToken>>> {
        if paths.is_empty() {
            return Ok(Vec::new());
        }
        let key = self.tokens_key_for_batch(paths.iter().map(String::as_str))?;
        let values = self
            .execute_array(
                READ_MANY_ID,
                key,
                paths.iter().map(Self::bytes).collect::<Vec<_>>(),
            )
            .await?;
        if values.len() != paths.len() {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock read_many returned {} values for {} paths",
                values.len(),
                paths.len()
            )));
        }
        values
            .iter()
            .zip(paths)
            .map(|(value, path)| match value {
                ScriptValue::Null => Ok(None),
                ScriptValue::Bytes(raw) => {
                    let raw = std::str::from_utf8(raw).map_err(|error| {
                        PathLockError::InvalidToken(format!(
                            "invalid UTF-8 token at '{path}': {error}"
                        ))
                    })?;
                    Self::decode_token(path, raw).map(Some)
                }
                other => Err(PathLockError::Internal(format!(
                    "Redis PathLock read_many returned invalid value for '{path}': {other:?}"
                ))),
            })
            .collect()
    }
}

#[async_trait]
impl PathLockProvider for RedisPathLockProvider {
    /// Return the provider name used by configuration and diagnostics.
    fn name(&self) -> &'static str {
        "cache"
    }

    /// Return logical paths because Redis HASH fields are business paths.
    fn handle_mode(&self) -> PathLockHandleMode {
        PathLockHandleMode::LogicalPath
    }

    /// Atomically check conflicts and acquire one normalized request batch.
    async fn try_acquire_batch_atomic(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        now_ns: u128,
        stale_before_ns: u128,
    ) -> PathLockResult<Option<Vec<AtomicAcquisition>>> {
        if requests.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "lock request batch must not be empty".to_string(),
            ));
        }
        let key =
            self.tokens_key_for_batch(requests.iter().map(|request| request.path.as_str()))?;
        let mut args = vec![
            Self::bytes(owner_id),
            Self::bytes(now_ns.to_string()),
            Self::bytes(stale_before_ns.to_string()),
            Self::bytes(self.hash_ttl_ms.to_string()),
        ];
        for request in requests {
            args.push(Self::bytes(&request.path));
            args.push(Self::bytes(Self::kind_code(request.kind)));
        }
        let values = self.execute_array(ACQUIRE_BATCH_ID, key, args).await?;
        let status = Self::result_status(&values, "acquire_batch")?;
        if status != "ok" {
            return Err(Self::decode_error_result("acquire_batch", &values)?);
        }
        if (values.len() - 1) % 4 != 0 {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock acquire_batch returned {} trailing values",
                values.len() - 1
            )));
        }

        let mut acquisitions = Vec::with_capacity((values.len() - 1) / 4);
        for values in values[1..].chunks_exact(4) {
            let handle = Self::result_string(&values[0], "acquire_batch")?.to_string();
            let change = Self::result_string(&values[1], "acquire_batch")?;
            let previous = Self::result_string(&values[2], "acquire_batch")?;
            let replacement = Self::result_string(&values[3], "acquire_batch")?;
            let change = match change {
                "created" => AcquisitionChange::Created {
                    replacement: Self::decode_token(&handle, replacement)?,
                },
                "reentrant" => AcquisitionChange::Reentrant,
                "upgraded" => AcquisitionChange::Upgraded {
                    previous: Self::decode_token(&handle, previous)?,
                    replacement: Self::decode_token(&handle, replacement)?,
                },
                other => {
                    return Err(PathLockError::Internal(format!(
                        "Redis PathLock acquire_batch returned unknown change '{other}'"
                    )));
                }
            };
            acquisitions.push(AtomicAcquisition { handle, change });
        }
        Ok(Some(acquisitions))
    }

    /// Atomically roll back mutations reported by one completed batch.
    async fn rollback_acquisitions_atomic(
        &self,
        acquisitions: &[AtomicAcquisition],
        owner_id: &str,
    ) -> PathLockResult<Option<()>> {
        if acquisitions.is_empty() {
            return Ok(Some(()));
        }
        let key =
            self.tokens_key_for_batch(acquisitions.iter().map(|item| item.handle.as_str()))?;
        let mut args = vec![
            Self::bytes(owner_id),
            Self::bytes(self.hash_ttl_ms.to_string()),
        ];
        for acquisition in acquisitions {
            args.push(Self::bytes(&acquisition.handle));
            match &acquisition.change {
                AcquisitionChange::Created { replacement } => {
                    args.push(Self::bytes("created"));
                    args.push(Self::bytes(""));
                    args.push(Self::bytes(LockTokenCodec::encode(replacement)));
                }
                AcquisitionChange::Reentrant => {
                    args.push(Self::bytes("reentrant"));
                    args.push(Self::bytes(""));
                    args.push(Self::bytes(""));
                }
                AcquisitionChange::Upgraded {
                    previous,
                    replacement,
                } => {
                    args.push(Self::bytes("upgraded"));
                    args.push(Self::bytes(LockTokenCodec::encode(previous)));
                    args.push(Self::bytes(LockTokenCodec::encode(replacement)));
                }
            }
        }
        let values = self.execute_array(ROLLBACK_BATCH_ID, key, args).await?;
        let status = Self::result_status(&values, "rollback_batch")?;
        if status == "ok" && values.len() == 1 {
            return Ok(Some(()));
        }
        Err(Self::decode_error_result("rollback_batch", &values)?)
    }

    /// Check whether a logical path is covered by a matching live token.
    async fn is_path_locked_atomic(
        &self,
        path: &str,
        _now_ns: u128,
        stale_before_ns: u128,
        ignore_stale: bool,
    ) -> PathLockResult<Option<bool>> {
        let key = self.tokens_key_for_path(path)?;
        let values = self
            .execute_array(
                IS_LOCKED_ID,
                key,
                vec![
                    Self::bytes(path),
                    Self::bytes(stale_before_ns.to_string()),
                    Self::bytes(if ignore_stale { "1" } else { "0" }),
                ],
            )
            .await?;
        let status = Self::result_status(&values, "is_locked")?;
        if status != "ok" {
            return Err(Self::decode_error_result("is_locked", &values)?);
        }
        if values.len() != 2 {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock is_locked returned {} values",
                values.len()
            )));
        }
        match values[1] {
            ScriptValue::Integer(value) => Ok(Some(value != 0)),
            ref other => Err(PathLockError::Internal(format!(
                "Redis PathLock is_locked returned non-integer value: {other:?}"
            ))),
        }
    }

    /// Read and decode one logical path token.
    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
        self.read_many_tokens(&[lock_path.to_string()])
            .await
            .map(|mut values| values.pop().flatten())
    }

    /// Atomically create one token when the path is absent.
    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
        let key = self.tokens_key_for_path(lock_path)?;
        let values = self
            .execute_array(
                CREATE_ID,
                key,
                vec![
                    Self::bytes(lock_path),
                    Self::bytes(LockTokenCodec::encode(token)),
                    Self::bytes(self.hash_ttl_ms.to_string()),
                ],
            )
            .await?;
        let status = Self::result_status(&values, "create")?;
        match status {
            "created" if values.len() == 1 => Ok(()),
            "exists" if values.len() == 2 => {
                let raw = Self::result_string(&values[1], "create")?;
                let current = Self::decode_token(lock_path, raw)?;
                Err(PathLockError::Conflict {
                    lock_path: lock_path.to_string(),
                    owner: current.owner_id,
                    kind: current.lock_type,
                })
            }
            _ => Err(PathLockError::Internal(format!(
                "Redis PathLock create returned invalid status '{status}'"
            ))),
        }
    }

    /// Replace one token only when its complete encoded value still matches.
    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        let key = self.tokens_key_for_path(lock_path)?;
        self.execute_integer(
            COMPARE_WRITE_ID,
            key,
            vec![
                Self::bytes(lock_path),
                Self::bytes(LockTokenCodec::encode(expected)),
                Self::bytes(LockTokenCodec::encode(replacement)),
                Self::bytes(self.hash_ttl_ms.to_string()),
            ],
        )
        .await
        .map(|value| value != 0)
    }

    /// Refresh one owned token while preserving its lock kind.
    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool> {
        let key = self.tokens_key_for_path(lock_path)?;
        let values = self
            .execute_array(
                REFRESH_ID,
                key,
                vec![
                    Self::bytes(lock_path),
                    Self::bytes(owner_id),
                    Self::bytes(time_ns.to_string()),
                    Self::bytes(self.hash_ttl_ms.to_string()),
                ],
            )
            .await?;
        let status = Self::result_status(&values, "refresh")?;
        match status {
            "ok" if values.len() == 1 => Ok(true),
            "missing" | "owner_mismatch" if values.len() == 1 => Ok(false),
            "invalid_token" if values.len() == 2 => Err(PathLockError::InvalidToken(format!(
                "invalid token at '{lock_path}': {}",
                Self::result_string(&values[1], "refresh")?
            ))),
            "invalid_request" if values.len() == 2 => Err(PathLockError::InvalidRequest(
                Self::result_string(&values[1], "refresh")?.to_string(),
            )),
            _ => Err(PathLockError::Internal(format!(
                "Redis PathLock refresh returned invalid status '{status}'"
            ))),
        }
    }

    /// Remove one owned token using complete-token compare-and-remove.
    async fn remove_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        _force: bool,
    ) -> PathLockResult<bool> {
        let Some(current) = self.read_token(lock_path).await? else {
            return Ok(false);
        };
        if current.owner_id != owner_id {
            return Ok(false);
        }
        let key = self.tokens_key_for_path(lock_path)?;
        self.execute_integer(
            COMPARE_REMOVE_ID,
            key,
            vec![
                Self::bytes(lock_path),
                Self::bytes(LockTokenCodec::encode(&current)),
                Self::bytes(self.hash_ttl_ms.to_string()),
            ],
        )
        .await
        .map(|value| value != 0)
    }

    /// Return logical paths equal to or below one root.
    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        let key = self.tokens_key_for_path(root)?;
        let values = self
            .execute_array(SCAN_DESCENDANTS_ID, key, vec![Self::bytes(root)])
            .await?;
        let status = Self::result_status(&values, "scan_descendants")?;
        if status != "ok" {
            return Err(Self::decode_error_result("scan_descendants", &values)?);
        }
        if (values.len() - 1) % 2 != 0 {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock scan_descendants returned {} trailing values",
                values.len() - 1
            )));
        }
        let mut paths = Vec::with_capacity((values.len() - 1) / 2);
        for values in values[1..].chunks_exact(2) {
            let path = Self::result_string(&values[0], "scan_descendants")?;
            let raw = Self::result_string(&values[1], "scan_descendants")?;
            Self::decode_token(path, raw)?;
            paths.push(path.to_string());
        }
        Ok(paths)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    use crate::cache_runtime::{
        CacheRuntime, RedisProviderConfig, ScriptDefinition, ScriptRequest, ScriptValue,
    };
    use crate::plugins::memfs::MemFileSystem;

    use super::super::manager::{PathLockConfig, PathLockManager};
    use super::super::provider::{
        AcquisitionChange, AtomicAcquisition, PathLockHandleMode, PathLockProvider,
    };
    use super::super::types::{
        LockToken, PathLockError, PathLockHandoffRef, PathLockKind, PathLockRequest,
    };
    use super::*;

    const TEST_PTTL_ID: &str = "pathlock.test.pttl.v1";
    const TEST_PTTL_SCRIPT: &str = "return redis.call('PTTL', KEYS[1])";
    const TEST_HASH_ID: &str = "pathlock.test.hash.v1";
    const TEST_HASH_SCRIPT: &str = r#"
if ARGV[1] == "set" then
    redis.call("HSET", KEYS[1], ARGV[2], ARGV[3])
    return ARGV[3]
end
return redis.call("HGET", KEYS[1], ARGV[2])
"#;

    enum AtomicFault {
        WriteThenIo,
        Conflict,
    }

    /// Logical test provider for atomic Manager failure paths.
    struct FaultProvider {
        inner: super::super::provider::MemoryPathLockProvider,
        fault: AtomicFault,
        remove_calls: AtomicUsize,
    }

    impl FaultProvider {
        /// Create one provider with the selected injected fault.
        fn new(fault: AtomicFault) -> Self {
            Self {
                inner: super::super::provider::MemoryPathLockProvider::new(),
                fault,
                remove_calls: AtomicUsize::new(0),
            }
        }
    }

    #[async_trait]
    impl PathLockProvider for FaultProvider {
        /// Return the provider name used by test diagnostics.
        fn name(&self) -> &'static str {
            "fault"
        }

        /// Return logical handles to exercise the atomic Manager branch.
        fn handle_mode(&self) -> PathLockHandleMode {
            PathLockHandleMode::LogicalPath
        }

        /// Inject either an unknown write result or a conflict.
        async fn try_acquire_batch_atomic(
            &self,
            requests: &[PathLockRequest],
            owner_id: &str,
            now_ns: u128,
            _stale_before_ns: u128,
        ) -> PathLockResult<Option<Vec<AtomicAcquisition>>> {
            match self.fault {
                AtomicFault::WriteThenIo => {
                    let request = &requests[0];
                    self.inner
                        .try_create_token(&request.path, &token(owner_id, now_ns, request.kind))
                        .await?;
                    Err(PathLockError::Io(
                        "simulated response timeout after write".to_string(),
                    ))
                }
                AtomicFault::Conflict => Err(PathLockError::Conflict {
                    lock_path: "/race".to_string(),
                    owner: "owner-a".to_string(),
                    kind: PathLockKind::Exact,
                }),
            }
        }

        /// Return stored data or the stale conflict snapshot.
        async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
            match self.fault {
                AtomicFault::Conflict => Ok(Some(token("owner-a", 1, PathLockKind::Exact))),
                AtomicFault::WriteThenIo => self.inner.read_token(lock_path).await,
            }
        }

        /// Delegate fallback token creation to memory storage.
        async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
            self.inner.try_create_token(lock_path, token).await
        }

        /// Delegate fallback token replacement to memory storage.
        async fn compare_and_write_token(
            &self,
            lock_path: &str,
            expected: &LockToken,
            replacement: &LockToken,
        ) -> PathLockResult<bool> {
            self.inner
                .compare_and_write_token(lock_path, expected, replacement)
                .await
        }

        /// Delegate refresh to memory storage.
        async fn refresh_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            time_ns: u128,
        ) -> PathLockResult<bool> {
            self.inner.refresh_token(lock_path, owner_id, time_ns).await
        }

        /// Record cleanup calls and delegate removal.
        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            force: bool,
        ) -> PathLockResult<bool> {
            self.remove_calls.fetch_add(1, Ordering::SeqCst);
            self.inner.remove_token(lock_path, owner_id, force).await
        }

        /// Delegate descendant scans to memory storage.
        async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
            self.inner.scan_descendant_locks(root).await
        }
    }

    /// Build a real Redis provider when the integration endpoint is configured.
    async fn test_provider(test_name: &str) -> Option<(Arc<CacheRuntime>, RedisPathLockProvider)> {
        let endpoint = std::env::var("REDIS_URL").ok()?;
        let runtime = CacheRuntime::redis(RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let namespace = format!("pathlock-test-{}-{test_name}", std::process::id());
        let provider = RedisPathLockProvider::new(runtime.clone(), &namespace, 1.0).unwrap();
        Some((runtime, provider))
    }

    /// Build two managers that share one Redis namespace.
    async fn test_managers(
        test_name: &str,
    ) -> Option<(Arc<CacheRuntime>, PathLockManager, PathLockManager, String)> {
        let endpoint = std::env::var("REDIS_URL").ok()?;
        let runtime = CacheRuntime::redis(RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let namespace = format!("pathlock-manager-test-{}-{test_name}", std::process::id());
        let config = PathLockConfig {
            provider: "cache".to_string(),
            namespace: Some(namespace.clone()),
            lock_timeout_secs: 0.0,
            lock_expire_secs: 3.0,
        };
        let first_provider =
            Arc::new(RedisPathLockProvider::new(runtime.clone(), &namespace, 3.0).unwrap());
        let key = first_provider
            .tokens_key_for_path("/local/account-a")
            .unwrap();
        let first = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            first_provider,
            config.clone(),
        );
        let second = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            Arc::new(RedisPathLockProvider::new(runtime.clone(), &namespace, 3.0).unwrap()),
            config,
        );
        Some((runtime, first, second, key))
    }

    /// Read a comma-separated Redis endpoint environment variable.
    fn topology_endpoints(variable: &str) -> Option<Vec<String>> {
        let endpoints = std::env::var(variable).ok()?;
        let endpoints = endpoints
            .split(',')
            .map(str::trim)
            .filter(|endpoint| !endpoint.is_empty())
            .map(str::to_string)
            .collect::<Vec<_>>();
        (!endpoints.is_empty()).then_some(endpoints)
    }

    /// Exercise one provider against a configured Redis topology.
    async fn exercise_topology(
        mode: &str,
        endpoints: Vec<String>,
        master_name: Option<String>,
        test_name: &str,
    ) {
        let runtime = CacheRuntime::redis(RedisProviderConfig {
            mode: mode.to_string(),
            endpoints,
            master_name,
            connect_timeout_ms: 10_000,
            command_timeout_ms: 3_000,
            ..RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let namespace = format!("pathlock-topology-test-{}-{test_name}", std::process::id());
        let provider = RedisPathLockProvider::new(runtime.clone(), &namespace, 3.0).unwrap();

        provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/root", PathLockKind::Tree)],
                "owner-a",
                100,
                0,
            )
            .await
            .unwrap();
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(
                    &[request("/local/account-a/root/child", PathLockKind::Exact)],
                    "owner-b",
                    101,
                    0,
                )
                .await,
            Err(PathLockError::Conflict { .. })
        ));

        runtime.close().await.unwrap();
    }

    /// Build one lock token for provider tests.
    fn token(owner_id: &str, time_ns: u128, lock_type: PathLockKind) -> LockToken {
        LockToken {
            owner_id: owner_id.to_string(),
            time_ns,
            lock_type,
        }
    }

    /// Build one logical lock request.
    fn request(path: &str, kind: PathLockKind) -> PathLockRequest {
        PathLockRequest {
            path: path.to_string(),
            kind,
        }
    }

    /// Verify invalid namespace and expiry values are rejected before Redis access.
    #[test]
    fn provider_rejects_invalid_configuration() {
        let runtime = CacheRuntime::memory();

        assert!(matches!(
            RedisPathLockProvider::new(runtime.clone(), "bad{name}", 1.0),
            Err(PathLockError::InvalidRequest(_))
        ));
        assert!(matches!(
            RedisPathLockProvider::new(runtime, "prod-a", f64::NAN),
            Err(PathLockError::InvalidRequest(_))
        ));
    }

    /// Verify global, system, and account paths use separate Redis HASH keys.
    #[tokio::test]
    async fn provider_routes_tokens_to_scope_hashes() {
        let Some((runtime, provider)) = test_provider("scope-keys").await else {
            return;
        };
        runtime
            .register_script(ScriptDefinition {
                id: TEST_HASH_ID,
                redis_lua: TEST_HASH_SCRIPT,
            })
            .unwrap();

        let cases = [
            ("/", "global"),
            ("/local", "global"),
            ("/local/_system/tasks/task.json", "scope:_system"),
            ("/local/account-a/resources/a.md", "scope:account:account-a"),
            ("/local/account-b/resources/b.md", "scope:account:account-b"),
        ];
        let namespace = format!("pathlock-test-{}-scope-keys", std::process::id());
        for (index, (path, scope)) in cases.iter().enumerate() {
            provider
                .try_create_token(
                    path,
                    &token("owner-a", index as u128 + 1, PathLockKind::Exact),
                )
                .await
                .unwrap();
            let raw = runtime
                .execute_script(ScriptRequest {
                    script_id: TEST_HASH_ID.to_string(),
                    keys: vec![format!("ov:pathlock:{{{namespace}}}:{scope}:tokens")],
                    args: vec![
                        RedisPathLockProvider::bytes("get"),
                        RedisPathLockProvider::bytes(path),
                    ],
                })
                .await
                .unwrap()
                .decode()
                .unwrap();
            assert!(matches!(raw, ScriptValue::Bytes(_)), "{path} missing");
        }
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(
                    &[
                        request("/local/account-a/a", PathLockKind::Exact),
                        request("/local/account-b/b", PathLockKind::Exact),
                    ],
                    "owner-a",
                    10,
                    0,
                )
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));
        assert!(matches!(
            provider
                .try_create_token("/queue", &token("owner-a", 10, PathLockKind::Exact))
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));

        runtime.close().await.unwrap();
    }

    /// Verify CRUD preserves the token format and refreshes the HASH TTL.
    #[tokio::test]
    async fn provider_crud_preserves_tokens_and_hash_ttl() {
        let Some((runtime, provider)) = test_provider("crud").await else {
            return;
        };
        let path = "/local/account-a/a";
        let original = token("owner-a", 100, PathLockKind::Exact);

        provider.try_create_token(path, &original).await.unwrap();
        assert_eq!(
            provider.read_token(path).await.unwrap(),
            Some(original.clone())
        );
        assert!(provider.refresh_token(path, "owner-a", 200).await.unwrap());
        assert_eq!(
            provider.read_token(path).await.unwrap(),
            Some(token("owner-a", 200, PathLockKind::Exact))
        );

        runtime
            .register_script(ScriptDefinition {
                id: TEST_PTTL_ID,
                redis_lua: TEST_PTTL_SCRIPT,
            })
            .unwrap();
        let ttl = runtime
            .execute_script(ScriptRequest {
                script_id: TEST_PTTL_ID.to_string(),
                keys: vec![provider.tokens_key_for_path(path).unwrap()],
                args: Vec::new(),
            })
            .await
            .unwrap()
            .decode()
            .unwrap();
        assert!(matches!(ttl, ScriptValue::Integer(value) if value > 0 && value <= 2_000));

        assert!(provider.remove_token(path, "owner-a", false).await.unwrap());
        assert_eq!(provider.read_token(path).await.unwrap(), None);
        runtime.close().await.unwrap();
    }

    /// Verify Lua rejects conflicting owners without partially writing a batch.
    #[tokio::test]
    async fn provider_atomic_batch_is_all_or_nothing() {
        let Some((runtime, provider)) = test_provider("atomic").await else {
            return;
        };
        let initial = vec![request("/local/account-a/locked", PathLockKind::Tree)];
        let acquired = provider
            .try_acquire_batch_atomic(&initial, "owner-a", 100, 0)
            .await
            .unwrap()
            .unwrap();
        assert!(matches!(
            acquired.as_slice(),
            [AtomicAcquisition {
                handle,
                change: AcquisitionChange::Created { .. },
            }] if handle == "/local/account-a/locked"
        ));

        let conflicting = vec![
            request("/local/account-a/free", PathLockKind::Exact),
            request("/local/account-a/locked/child", PathLockKind::Exact),
        ];
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(&conflicting, "owner-b", 101, 0)
                .await,
            Err(PathLockError::Conflict { lock_path, .. })
                if lock_path == "/local/account-a/locked"
        ));
        assert_eq!(
            provider.read_token("/local/account-a/free").await.unwrap(),
            None
        );

        provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/a", PathLockKind::Exact)],
                "owner-a",
                102,
                0,
            )
            .await
            .unwrap();
        assert!(provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/a/b", PathLockKind::Exact)],
                "owner-b",
                103,
                0,
            )
            .await
            .is_ok());
        assert!(provider
            .try_acquire_batch_atomic(&[request("/", PathLockKind::Tree)], "owner-c", 104, 0,)
            .await
            .is_ok());

        let race = [request("/local/account-a/race", PathLockKind::Exact)];
        let (first, second) = tokio::join!(
            provider.try_acquire_batch_atomic(&race, "owner-a", 200, 0),
            provider.try_acquire_batch_atomic(&race, "owner-b", 200, 0),
        );
        assert_ne!(first.is_ok(), second.is_ok());
        assert!(
            matches!(first, Err(PathLockError::Conflict { .. }))
                || matches!(second, Err(PathLockError::Conflict { .. }))
        );
        runtime.close().await.unwrap();
    }

    /// Verify rollback removes creates and restores same-owner upgrades.
    #[tokio::test]
    async fn provider_atomic_rollback_restores_previous_state() {
        let Some((runtime, provider)) = test_provider("rollback").await else {
            return;
        };
        let exact = vec![request("/local/account-a/a", PathLockKind::Exact)];
        let created = provider
            .try_acquire_batch_atomic(&exact, "owner-a", 100, 0)
            .await
            .unwrap()
            .unwrap();
        provider
            .rollback_acquisitions_atomic(&created, "owner-a")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            provider.read_token("/local/account-a/a").await.unwrap(),
            None
        );

        provider
            .try_acquire_batch_atomic(&exact, "owner-a", 200, 0)
            .await
            .unwrap();
        let tree = vec![request("/local/account-a/a", PathLockKind::Tree)];
        let upgraded = provider
            .try_acquire_batch_atomic(&tree, "owner-a", 300, 0)
            .await
            .unwrap()
            .unwrap();
        provider
            .rollback_acquisitions_atomic(&upgraded, "owner-a")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            provider.read_token("/local/account-a/a").await.unwrap(),
            Some(token("owner-a", 200, PathLockKind::Exact))
        );

        let changed = provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/changed", PathLockKind::Exact)],
                "owner-a",
                400,
                0,
            )
            .await
            .unwrap()
            .unwrap();
        assert!(provider
            .refresh_token("/local/account-a/changed", "owner-a", 500)
            .await
            .unwrap());
        assert!(matches!(
            provider
                .rollback_acquisitions_atomic(&changed, "owner-a")
                .await,
            Err(PathLockError::Io(_))
        ));
        assert_eq!(
            provider
                .read_token("/local/account-a/changed")
                .await
                .unwrap(),
            Some(token("owner-a", 500, PathLockKind::Exact))
        );

        runtime.close().await.unwrap();
    }

    /// Verify Redis handoff can be adopted by another manager using logical handles.
    #[tokio::test]
    async fn manager_redis_adopts_handoff_across_managers() {
        let Some((runtime, first, second, key)) = test_managers("handoff").await else {
            return;
        };
        let lease = first
            .acquire_tree("/local/account-a/a", Duration::ZERO, None)
            .await
            .unwrap();
        let handoff = first.to_handoff(&lease);
        first.handoff(&lease).await.unwrap();

        let adopted = second.adopt(&handoff).await.unwrap();

        assert_eq!(adopted.lease.lock_paths, vec!["/local/account-a/a"]);
        second.release(&adopted).await.unwrap();
        let legacy = PathLockHandoffRef {
            lease_ref: None,
            owner_id: "owner-a".to_string(),
            lock_paths: vec!["/local/account-a/a".to_string()],
            covered_paths: Vec::new(),
        };
        assert!(matches!(
            second.adopt(&legacy).await,
            Err(PathLockError::HandoffFailed(_))
        ));
        runtime.del(&[key]).await.unwrap();
        runtime.close().await.unwrap();
    }

    /// Verify reentrant upgrades, downgrades, refresh, and partial release use logical handles.
    #[tokio::test]
    async fn manager_redis_preserves_lease_lifecycle_semantics() {
        let Some((runtime, first, second, key)) = test_managers("lifecycle").await else {
            return;
        };
        let exact = first
            .acquire_exact("/local/account-a/a/", Duration::ZERO, None)
            .await
            .unwrap();
        assert_eq!(exact.lease.lock_paths, vec!["/local/account-a/a"]);
        assert!(first.is_locked("/local/account-a/a/", true).await.unwrap());
        let tree = first
            .acquire_tree(
                "/local/account-a/a",
                Duration::ZERO,
                Some((&exact.lease.lease_ref, &exact.ownership_ref)),
            )
            .await
            .unwrap();
        assert_eq!(first.refresh(&exact).await.unwrap(), "refreshed");
        first.release(&tree).await.unwrap();

        let child = second
            .acquire_exact("/local/account-a/a/child", Duration::ZERO, None)
            .await
            .unwrap();
        second.release(&child).await.unwrap();
        first.release(&exact).await.unwrap();
        assert!(!first.is_locked("/local/account-a/a", true).await.unwrap());

        let batch = first
            .acquire_exact_batch(
                &[
                    "/local/account-a/x".to_string(),
                    "/local/account-a/y".to_string(),
                ],
                Duration::ZERO,
                None,
            )
            .await
            .unwrap();
        first
            .release_selected(&batch, &["/local/account-a/x".to_string()])
            .await
            .unwrap();
        let released = second
            .acquire_exact("/local/account-a/x", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(second
            .acquire_exact("/local/account-a/y", Duration::from_millis(1), None,)
            .await
            .is_err());
        second.release(&released).await.unwrap();
        first.release(&batch).await.unwrap();

        runtime.del(&[key]).await.unwrap();
        runtime.close().await.unwrap();
    }

    /// Verify stale foreign tokens are replaced while same-owner reentry preserves time.
    #[tokio::test]
    async fn provider_stale_cleanup_preserves_same_owner_reentry() {
        let Some((runtime, provider)) = test_provider("stale").await else {
            return;
        };
        let same_path = "/local/account-a/same";
        provider
            .try_create_token(same_path, &token("owner-a", 1, PathLockKind::Exact))
            .await
            .unwrap();
        let reentrant = provider
            .try_acquire_batch_atomic(
                &[request(same_path, PathLockKind::Exact)],
                "owner-a",
                100,
                50,
            )
            .await
            .unwrap()
            .unwrap();
        assert!(matches!(reentrant[0].change, AcquisitionChange::Reentrant));
        assert_eq!(
            provider.read_token(same_path).await.unwrap(),
            Some(token("owner-a", 1, PathLockKind::Exact))
        );
        assert_eq!(
            provider
                .is_path_locked_atomic(same_path, 100, 50, true)
                .await
                .unwrap(),
            Some(false)
        );
        assert_eq!(
            provider
                .is_path_locked_atomic(same_path, 100, 50, false)
                .await
                .unwrap(),
            Some(true)
        );

        let foreign_path = "/local/account-a/foreign";
        provider
            .try_create_token(foreign_path, &token("owner-b", 1, PathLockKind::Exact))
            .await
            .unwrap();
        provider
            .try_acquire_batch_atomic(
                &[request(foreign_path, PathLockKind::Exact)],
                "owner-a",
                100,
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            provider
                .read_token(foreign_path)
                .await
                .unwrap()
                .unwrap()
                .owner_id,
            "owner-a"
        );

        runtime.close().await.unwrap();
    }

    /// Verify an out-of-range token is rejected before an atomic upgrade writes.
    #[tokio::test]
    async fn provider_invalid_u128_token_does_not_modify_hash() {
        let Some((runtime, provider)) = test_provider("invalid-u128").await else {
            return;
        };
        runtime
            .register_script(ScriptDefinition {
                id: TEST_HASH_ID,
                redis_lua: TEST_HASH_SCRIPT,
            })
            .unwrap();
        let path = "/local/account-a/invalid";
        let key = provider.tokens_key_for_path(path).unwrap();
        let invalid = "owner-a:340282366920938463463374607431768211456:E";
        runtime
            .execute_script(ScriptRequest {
                script_id: TEST_HASH_ID.to_string(),
                keys: vec![key.clone()],
                args: vec![
                    RedisPathLockProvider::bytes("set"),
                    RedisPathLockProvider::bytes(path),
                    RedisPathLockProvider::bytes(invalid),
                ],
            })
            .await
            .unwrap();

        assert!(matches!(
            provider
                .try_acquire_batch_atomic(&[request(path, PathLockKind::Tree)], "owner-a", 100, 0,)
                .await,
            Err(PathLockError::InvalidToken(_))
        ));
        let raw = runtime
            .execute_script(ScriptRequest {
                script_id: TEST_HASH_ID.to_string(),
                keys: vec![key],
                args: vec![
                    RedisPathLockProvider::bytes("get"),
                    RedisPathLockProvider::bytes(path),
                ],
            })
            .await
            .unwrap()
            .decode()
            .unwrap();
        assert_eq!(raw, ScriptValue::Bytes(invalid.as_bytes().to_vec()));

        runtime.close().await.unwrap();
    }

    /// Verify an unknown atomic write result never publishes a local lease.
    #[tokio::test]
    async fn manager_unknown_atomic_result_does_not_publish_lease() {
        let provider = Arc::new(FaultProvider::new(AtomicFault::WriteThenIo));
        let manager = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            provider.clone(),
            PathLockConfig {
                provider: "cache".to_string(),
                namespace: Some("timeout-test".to_string()),
                ..PathLockConfig::default()
            },
        );

        assert!(matches!(
            manager
                .acquire_exact("/timeout", Duration::ZERO, None)
                .await,
            Err(PathLockError::Io(_))
        ));
        assert_eq!(manager.observe().await.active_locks, 0);
        assert!(provider.read_token("/timeout").await.unwrap().is_some());
    }

    /// Verify Manager never performs non-atomic stale cleanup after a logical conflict.
    #[tokio::test]
    async fn manager_logical_conflict_does_not_remove_stale_snapshot() {
        let provider = Arc::new(FaultProvider::new(AtomicFault::Conflict));
        let manager = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            provider.clone(),
            PathLockConfig {
                provider: "cache".to_string(),
                namespace: Some("race-test".to_string()),
                lock_expire_secs: 1.0,
                ..PathLockConfig::default()
            },
        );

        assert!(manager
            .acquire_exact("/race", Duration::from_millis(5), None)
            .await
            .is_err());
        assert_eq!(provider.remove_calls.load(Ordering::SeqCst), 0);
    }

    /// Verify optional cluster and sentinel topology contracts.
    #[tokio::test]
    async fn topology_cluster_and_sentinel() {
        if let Some(endpoints) = topology_endpoints("REDIS_CLUSTER_TEST_URLS") {
            exercise_topology("cluster", endpoints, None, "cluster").await;
        }
        if let (Some(endpoints), Ok(master)) = (
            topology_endpoints("REDIS_SENTINEL_TEST_URLS"),
            std::env::var("REDIS_SENTINEL_TEST_MASTER"),
        ) {
            exercise_topology("sentinel", endpoints, Some(master), "sentinel").await;
        }
    }

    /// Measure HASH-only atomic batch latency for representative lock counts.
    #[tokio::test]
    #[ignore = "manual Redis performance baseline"]
    async fn perf_hash_only_batches() {
        let Some((runtime, provider)) = test_provider("perf").await else {
            return;
        };
        for count in [100usize, 500, 1_000] {
            let requests = (0..count)
                .map(|index| PathLockRequest {
                    path: format!("/local/account-a/perf/{index}"),
                    kind: PathLockKind::Exact,
                })
                .collect::<Vec<_>>();
            let start = Instant::now();
            let acquired = provider
                .try_acquire_batch_atomic(&requests, "perf-owner", 100, 0)
                .await
                .unwrap()
                .unwrap();
            let elapsed = start.elapsed();
            println!(
                "pathlock HASH-only count={count} acquire_ms={}",
                elapsed.as_secs_f64() * 1_000.0
            );
            provider
                .rollback_acquisitions_atomic(&acquired, "perf-owner")
                .await
                .unwrap();
        }

        runtime.close().await.unwrap();
    }
}
