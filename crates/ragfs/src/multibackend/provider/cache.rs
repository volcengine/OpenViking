//! CacheRuntime-backed V2 multi-write persistence.

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bytes::Bytes;
use serde::Deserialize;
use uuid::Uuid;

use crate::cache_runtime::{
    CacheError, CacheOperation, CacheRuntime, ScriptDefinition, ScriptRequest, ScriptValue,
};
use crate::core::errors::{Error, Result};
use crate::core::internal_names::is_multiwrite_internal_path;
use crate::multibackend::codec::{decode_segment, encode_segment, sha256_hex};
use crate::multibackend::constants::{
    HEAD_SEGMENT_FILE_PREFIX, MAX_SEGMENT_RECORDS, SEALED_SEGMENT_FILE_PREFIX,
    SEGMENT_FILE_EXTENSION,
};
use crate::multibackend::meta::MetadataStore;
use crate::multibackend::model::{
    BackendState, CheckpointsManifest, FlushResult, MarkerPosition, PartitionContext,
    PartitionsManifest, PendingEvent, PendingEventKind, ScopeKey, SegmentDescriptor,
    SegmentEventType, SegmentManifest, SegmentRecord,
};
use crate::multibackend::router::AccountRouter;

use super::{read_sealed_segment, select_record_range, MultiWriteProvider};

const FLUSH_SCRIPT_ID: &str = "multiwrite.flush.v1";
const SEAL_SCRIPT_ID: &str = "multiwrite.seal.v1";
const ADVANCE_SCRIPT_ID: &str = "multiwrite.advance.v1";

const FLUSH_SCRIPT: &str = r#"
if redis.call("EXISTS", KEYS[1]) == 0 then
    return { "not_found" }
end
if redis.call("HGET", KEYS[1], "account") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "partition") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "epoch") ~= ARGV[3] then
    return { "scope_mismatch" }
end
local requested = #ARGV - 6
if ARGV[4] == "1" and requested ~= 1 then
    return { "invalid", "marker flush requires exactly one record" }
end
local owner = redis.call("GET", KEYS[3])
if ARGV[6] == "1" then
    if owner ~= ARGV[5] then
        return { "fenced" }
    end
elseif owner and owner ~= ARGV[5] then
    return { "busy" }
else
    redis.call("SET", KEYS[3], ARGV[5], "PX", 30000)
end
redis.call("PEXPIRE", KEYS[3], 30000)
local marker_seq = tonumber(redis.call("HGET", KEYS[1], "marker_seq"))
if marker_seq ~= 0 then
    return { "seal_required" }
end
local head_len = redis.call("LLEN", KEYS[2])
local capacity = 8192 - head_len
if capacity <= 0 then
    return { "seal_required" }
end
local count = math.min(requested, capacity)
local next_seq = tonumber(redis.call("HGET", KEYS[1], "next_seq"))
local first_seq = next_seq
for index = 1, count do
    local record = cjson.decode(ARGV[index + 6])
    record.seq = next_seq
    redis.call("RPUSH", KEYS[2], cjson.encode(record))
    next_seq = next_seq + 1
end
if ARGV[4] == "1" then
    marker_seq = next_seq - 1
end
local next_seq_text = string.format("%.0f", next_seq)
local marker_seq_text = string.format("%.0f", marker_seq)
redis.call("HSET", KEYS[1], "next_seq", next_seq_text,
    "marker_seq", marker_seq_text)
local needs_seal = 0
if redis.call("LLEN", KEYS[2]) == 8192 or marker_seq ~= 0 then
    needs_seal = 1
end
if needs_seal == 0 then
    redis.call("DEL", KEYS[3])
else
    redis.call("PEXPIRE", KEYS[3], 30000)
end
return { "ok", string.format("%.0f", first_seq),
    string.format("%.0f", next_seq - 1), count, needs_seal }
"#;

const SEAL_SCRIPT: &str = r#"
local mode = ARGV[1]
if mode == "bootstrap" then
    if redis.call("EXISTS", KEYS[1]) ~= 0 then
        return { "exists" }
    end
    redis.call("DEL", KEYS[1])
    redis.call("HSET", KEYS[1],
        "account", ARGV[2],
        "partition", ARGV[3],
        "epoch", ARGV[4],
        "version", ARGV[5],
        "next_seq", ARGV[6],
        "marker_seq", "0",
        "segments", ARGV[7],
        "backend_states", ARGV[8])
    redis.call("DEL", KEYS[2])
    redis.call("DEL", KEYS[3])
    return { "ok" }
end
if redis.call("EXISTS", KEYS[1]) == 0 then
    return { "not_found" }
end
if redis.call("HGET", KEYS[1], "account") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "partition") ~= ARGV[3]
    or redis.call("HGET", KEYS[1], "epoch") ~= ARGV[4] then
    return { "scope_mismatch" }
end
if mode == "read" then
    return {
        "ok",
        redis.call("HGET", KEYS[1], "account"),
        redis.call("HGET", KEYS[1], "partition"),
        redis.call("HGET", KEYS[1], "epoch"),
        redis.call("HGET", KEYS[1], "version"),
        redis.call("HGET", KEYS[1], "next_seq"),
        redis.call("HGET", KEYS[1], "marker_seq"),
        redis.call("HGET", KEYS[1], "segments"),
        redis.call("HGET", KEYS[1], "backend_states")
    }
end
local segments = cjson.decode(redis.call("HGET", KEYS[1], "segments"))
if mode == "commit" then
    local first_seq = tonumber(ARGV[5])
    local last_seq = tonumber(ARGV[6])
    local count = tonumber(ARGV[7])
    local descriptor = cjson.decode(ARGV[8])
    local owner = ARGV[9]
    local keep_owner = ARGV[10]
    for _, current in ipairs(segments) do
        if current.path == descriptor.path then
            if current.segment_from_seq == descriptor.segment_from_seq
                and current.segment_to_seq == descriptor.segment_to_seq
                and current.record_count == descriptor.record_count
                and current.byte_len == descriptor.byte_len
                and current.checksum == descriptor.checksum then
                if owner ~= "" and redis.call("GET", KEYS[3]) == owner then
                    if keep_owner == "1" then
                        redis.call("PEXPIRE", KEYS[3], 30000)
                    else
                        redis.call("DEL", KEYS[3])
                    end
                end
                return { "already" }
            end
            return { "invalid", "sealed descriptor path conflicts" }
        end
    end
    local current_owner = redis.call("GET", KEYS[3])
    if owner == "" then
        if current_owner then
            return { "busy" }
        end
    elseif current_owner ~= owner then
        return { "fenced" }
    end
    if #segments > 0
        and segments[#segments].segment_to_seq + 1 ~= first_seq then
        return { "invalid", "sealed descriptor is not contiguous" }
    end
    if count ~= last_seq - first_seq + 1 then
        return { "invalid", "sealed descriptor count is invalid" }
    end
    if redis.call("LLEN", KEYS[2]) < count then
        return { "changed" }
    end
    for index = 1, count do
        if redis.call("LINDEX", KEYS[2], index - 1) ~= ARGV[index + 10] then
            return { "changed" }
        end
    end
    segments[#segments + 1] = descriptor
    redis.call("HSET", KEYS[1], "segments", cjson.encode(segments))
    redis.call("LTRIM", KEYS[2], count, -1)
    local marker_seq = tonumber(redis.call("HGET", KEYS[1], "marker_seq"))
    if marker_seq ~= 0 and marker_seq <= last_seq then
        redis.call("HSET", KEYS[1], "marker_seq", "0")
    end
    if owner ~= "" then
        if keep_owner == "1" then
            redis.call("PEXPIRE", KEYS[3], 30000)
        else
            redis.call("DEL", KEYS[3])
        end
    end
    return { "ok" }
end
if mode == "prune" then
    local requested = cjson.decode(ARGV[5])
    local requested_set = {}
    for _, path in ipairs(requested) do
        if requested_set[path] then
            return { "invalid", "prune paths contain duplicates" }
        end
        requested_set[path] = true
    end
    local removed = {}
    local kept = {}
    for _, descriptor in ipairs(segments) do
        if requested_set[descriptor.path] then
            removed[#removed + 1] = descriptor
            requested_set[descriptor.path] = nil
        else
            kept[#kept + 1] = descriptor
        end
    end
    for path, _ in pairs(requested_set) do
        return { "invalid", "sealed segment is not referenced: " .. path }
    end
    for index = 2, #kept do
        if kept[index].segment_from_seq ~= kept[index - 1].segment_to_seq + 1 then
            return { "invalid", "prune would create a segment gap" }
        end
    end
    local head = redis.call("LINDEX", KEYS[2], 0)
    if head and #kept > 0 then
        local first_head = cjson.decode(head)
        if first_head.seq ~= kept[#kept].segment_to_seq + 1 then
            return { "invalid", "prune would create a head gap" }
        end
    elseif not head and #kept > 0 then
        local next_seq = tonumber(redis.call("HGET", KEYS[1], "next_seq"))
        if next_seq ~= kept[#kept].segment_to_seq + 1 then
            return { "invalid", "prune would detach next_seq" }
        end
    end
    local encoded_kept = "[]"
    if #kept > 0 then
        encoded_kept = cjson.encode(kept)
    end
    local encoded_removed = "[]"
    if #removed > 0 then
        encoded_removed = cjson.encode(removed)
    end
    redis.call("HSET", KEYS[1], "segments", encoded_kept)
    return { "ok", encoded_removed }
end
return { "invalid", "unknown seal operation" }
"#;

const ADVANCE_SCRIPT: &str = r#"
if redis.call("EXISTS", KEYS[1]) == 0 then
    return { "not_found" }
end
if redis.call("HGET", KEYS[1], "account") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "partition") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "epoch") ~= ARGV[3] then
    return { "scope_mismatch" }
end
local states = cjson.decode(redis.call("HGET", KEYS[1], "backend_states"))
local state = states[ARGV[4]]
if not state then
    return { "invalid", "unknown backend id: " .. ARGV[4] }
end
local synced_seq = tonumber(ARGV[5])
if synced_seq < state.synced_seq then
    return { "invalid", "backend progress cannot regress" }
end
local next_seq = tonumber(redis.call("HGET", KEYS[1], "next_seq"))
if synced_seq >= next_seq then
    return { "invalid", "backend progress must remain below next_seq" }
end
state.synced_seq = synced_seq
redis.call("HSET", KEYS[1], "backend_states", cjson.encode(states))
return { "ok" }
"#;

const SCRIPT_DEFINITIONS: &[ScriptDefinition] = &[
    ScriptDefinition {
        id: FLUSH_SCRIPT_ID,
        redis_lua: FLUSH_SCRIPT,
    },
    ScriptDefinition {
        id: SEAL_SCRIPT_ID,
        redis_lua: SEAL_SCRIPT,
    },
    ScriptDefinition {
        id: ADVANCE_SCRIPT_ID,
        redis_lua: ADVANCE_SCRIPT,
    },
];

/// Cache keys owned by one account partition.
pub(crate) struct CachePartitionKeys {
    pub(crate) state: String,
    pub(crate) head: String,
    pub(crate) flush_gate: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CacheState {
    scope: ScopeKey,
    version: u32,
    next_seq: u64,
    marker_seq: u64,
    segments: Vec<SegmentDescriptor>,
    backend_states: BTreeMap<String, BackendState>,
}

struct FlushScriptResult {
    first_seq: u64,
    last_seq: u64,
    appended: usize,
    needs_seal: bool,
}

enum FlushScriptOutcome {
    Appended(FlushScriptResult),
    SealRequired,
    Busy,
}

/// Persists V2 mutable state through CacheRuntime and immutable segments on the primary filesystem.
pub struct CacheProvider {
    store: Arc<MetadataStore>,
    router: AccountRouter,
    runtime: Arc<CacheRuntime>,
    namespace: String,
}

impl CacheProvider {
    /// Creates a cache-backed metadata provider.
    pub fn new(
        store: Arc<MetadataStore>,
        runtime: Arc<CacheRuntime>,
        namespace: String,
    ) -> Result<Self> {
        Self::build(store, runtime, namespace)
    }

    /// Validates runtime capabilities and registers stable provider scripts.
    fn build(
        store: Arc<MetadataStore>,
        runtime: Arc<CacheRuntime>,
        namespace: String,
    ) -> Result<Self> {
        Self::validate_namespace(&namespace)?;
        runtime
            .require_operations(&[CacheOperation::Lrange, CacheOperation::ExecuteScript])
            .map_err(|error| {
                Error::config(format!(
                    "Cache-backed multi-write provider is incompatible: {error}"
                ))
            })?;
        for definition in SCRIPT_DEFINITIONS {
            runtime.register_script(*definition).map_err(|error| {
                Error::config(format!(
                    "Cache-backed multi-write script registration failed: {error}"
                ))
            })?;
        }
        Ok(Self {
            router: AccountRouter::new(store.clone()),
            store,
            runtime,
            namespace,
        })
    }

    /// Validate the Redis hash tag namespace used by multi-write cache keys.
    fn validate_namespace(namespace: &str) -> Result<()> {
        if namespace.trim().is_empty() || namespace.contains(['{', '}']) {
            return Err(Error::config(
                "cache-backed multi-write namespace must be non-empty and must not contain '{' or '}'",
            ));
        }
        Ok(())
    }

    /// Builds deterministic same-slot keys for one validated scope.
    pub(crate) fn keys_for_scope(&self, scope: &ScopeKey) -> Result<CachePartitionKeys> {
        scope.validate()?;
        if matches!(scope.account_id.as_str(), "." | "..")
            || scope.account_id.contains('/')
            || scope
                .account_id
                .bytes()
                .any(|byte| byte.is_ascii_control() || matches!(byte, b'{' | b'}'))
        {
            return Err(Error::invalid_path(
                "Cache-backed multi-write account id contains an invalid key character",
            ));
        }
        let prefix = format!(
            "{{{}}}:ov:multiwrite:{}:{}",
            self.namespace, scope.account_id, scope.partition_id
        );
        Ok(CachePartitionKeys {
            state: format!("{prefix}:state"),
            head: format!("{prefix}:head"),
            flush_gate: format!("{prefix}:flush-gate"),
        })
    }

    /// Validates normal flush input before routing or cache access.
    fn validate_flush_events(events: &[PendingEvent]) -> Result<()> {
        let first = events
            .first()
            .ok_or_else(|| Error::invalid_operation("flush requires at least one event"))?;
        for event in events {
            if event.account_id != first.account_id {
                return Err(Error::invalid_operation(
                    "flush events must belong to one account",
                ));
            }
            if is_multiwrite_internal_path(&event.path) {
                return Err(Error::invalid_path(format!(
                    "cannot flush internal path: {}",
                    event.path
                )));
            }
            match event.kind {
                PendingEventKind::Data(SegmentEventType::Write | SegmentEventType::Remove) => {}
                _ => {
                    return Err(Error::invalid_operation(
                        "normal flush accepts only write and remove events",
                    ))
                }
            }
            if event.destination_path.is_some() {
                return Err(Error::invalid_operation(
                    "normal data event cannot have a destination",
                ));
            }
        }
        Ok(())
    }

    /// Routes every event and requires one current partition scope.
    async fn route_events(&self, events: &[PendingEvent]) -> Result<PartitionContext> {
        let mut selected = None;
        for event in events {
            let current = self.router.route(&event.account_id, &event.path).await?;
            if selected
                .as_ref()
                .is_some_and(|existing: &PartitionContext| existing.scope != current.scope)
            {
                return Err(Error::invalid_operation(
                    "flush events route to different partition scopes",
                ));
            }
            selected.get_or_insert(current);
        }
        selected.ok_or_else(|| Error::invalid_operation("flush requires at least one event"))
    }

    /// Validates a scope against its current account partitions manifest.
    async fn validate_scope(&self, scope: &ScopeKey) -> Result<()> {
        self.keys_for_scope(scope)?;
        let path = self.store.paths().account_manifest(&scope.account_id)?.0;
        let manifest: PartitionsManifest = self.store.read_json(&path).await?;
        manifest.validate()?;
        scope.validate_against_manifest(&manifest)
    }

    /// Converts one pending event into a validated record template.
    fn record(event: &PendingEvent) -> Result<SegmentRecord> {
        let event_type = match event.kind {
            PendingEventKind::Data(kind) => kind,
            PendingEventKind::DeleteAccount => {
                return Err(Error::invalid_operation(
                    "account deletion is not a segment record",
                ))
            }
        };
        let marker = matches!(
            event_type,
            SegmentEventType::RemoveTree | SegmentEventType::MoveTree
        );
        let record = SegmentRecord {
            seq: 1,
            event_type,
            path: event.path.clone(),
            op_id: marker.then_some(event.operation_id),
            destination_path: event.destination_path.clone(),
        };
        record.validate()?;
        encode_segment(std::slice::from_ref(&record))?;
        Ok(record)
    }

    /// Validates one marker against its caller-selected scope.
    fn validate_marker(&self, scope: &ScopeKey, event: &PendingEvent) -> Result<()> {
        if event.account_id != scope.account_id {
            return Err(Error::invalid_operation(
                "marker account does not match supplied scope",
            ));
        }
        match event.kind {
            PendingEventKind::Data(SegmentEventType::RemoveTree | SegmentEventType::MoveTree) => {}
            _ => {
                return Err(Error::invalid_operation(
                    "append_marker accepts only directory markers",
                ))
            }
        }
        if is_multiwrite_internal_path(&event.path) {
            return Err(Error::invalid_path(format!(
                "cannot append marker for internal path: {}",
                event.path
            )));
        }
        self.store
            .paths()
            .backend_path(&scope.account_id, &event.path)?;
        if let Some(destination) = &event.destination_path {
            if is_multiwrite_internal_path(destination) {
                return Err(Error::invalid_path(format!(
                    "cannot append marker for internal destination: {destination}"
                )));
            }
            self.store
                .paths()
                .backend_path(&scope.account_id, destination)?;
        }
        Self::record(event).map(|_| ())
    }

    /// Executes one registered script and requires an array result.
    async fn execute_array(
        &self,
        script_id: &str,
        keys: &CachePartitionKeys,
        args: Vec<Bytes>,
    ) -> Result<Vec<ScriptValue>> {
        let result = self
            .runtime
            .execute_script(ScriptRequest {
                script_id: script_id.to_string(),
                keys: vec![
                    keys.state.clone(),
                    keys.head.clone(),
                    keys.flush_gate.clone(),
                ],
                args,
            })
            .await
            .and_then(|result| result.decode())
            .map_err(|error| Self::cache_error(script_id, error))?;
        match result {
            ScriptValue::Array(values) => Ok(values),
            other => Err(Error::Serialization(format!(
                "Cache-backed multi-write {script_id} returned non-array result: {other:?}"
            ))),
        }
    }

    /// Executes one bounded atomic append attempt.
    async fn flush_once(
        &self,
        scope: &ScopeKey,
        records: &[SegmentRecord],
        marker: bool,
        owner: &str,
        continuing: bool,
    ) -> Result<FlushScriptOutcome> {
        let keys = self.keys_for_scope(scope)?;
        let mut args = Self::scope_args(scope);
        args.push(Bytes::from(if marker { "1" } else { "0" }));
        args.push(Bytes::from(owner.to_string()));
        args.push(Bytes::from(if continuing { "1" } else { "0" }));
        for record in records {
            args.push(Bytes::from(serde_json::to_vec(record)?));
        }
        let values = self.execute_array(FLUSH_SCRIPT_ID, &keys, args).await?;
        match Self::status(&values, "flush")? {
            "ok" => Ok(FlushScriptOutcome::Appended(FlushScriptResult {
                first_seq: Self::u64_value(&values, 1, "flush first_seq")?,
                last_seq: Self::u64_value(&values, 2, "flush last_seq")?,
                appended: Self::usize_value(&values, 3, "flush appended count")?,
                needs_seal: Self::u64_value(&values, 4, "flush seal flag")? == 1,
            })),
            "seal_required" => Ok(FlushScriptOutcome::SealRequired),
            "busy" => Ok(FlushScriptOutcome::Busy),
            "fenced" => Err(Error::would_block(
                "cache-backed flush owner was fenced by another writer",
            )),
            "not_found" => Err(Error::not_found(keys.state)),
            "scope_mismatch" => Err(Error::invalid_operation(
                "Cache-backed multi-write state scope does not match",
            )),
            "invalid" => Err(Self::script_invalid(&values, "flush")),
            other => Err(Self::unknown_status("flush", other)),
        }
    }

    /// Revalidates routing and executes one append while holding the account manifest lock.
    async fn flush_routed_once(
        &self,
        events: &[PendingEvent],
        expected_scope: Option<&ScopeKey>,
        records: &[SegmentRecord],
        owner: &str,
        continuing: bool,
    ) -> Result<(PartitionContext, FlushScriptOutcome)> {
        let account_id = &events
            .first()
            .ok_or_else(|| Error::invalid_operation("flush requires at least one event"))?
            .account_id;
        let manifest_path = self.store.paths().account_manifest(account_id)?.0;
        let lock_timeout = self
            .store
            .pathlock_manager()
            .default_lock_timeout()
            .max(std::time::Duration::from_secs(30));
        let lease = self
            .store
            .pathlock_manager()
            .acquire_exact(&manifest_path, lock_timeout, None)
            .await?;
        let operation = async {
            let route = self.route_events(events).await?;
            if expected_scope.is_some_and(|scope| scope != &route.scope) {
                return Err(Error::would_block(
                    "multi-write route changed during cache-backed flush",
                ));
            }
            let outcome = self
                .flush_once(&route.scope, records, false, owner, continuing)
                .await?;
            Ok((route, outcome))
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&lease)
            .await
            .map_err(Error::from);
        match (operation, release) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error),
            (Ok(result), Ok(())) => Ok(result),
        }
    }

    /// Revalidates a supplied scope and appends one marker under its manifest lock.
    async fn flush_marker_once(
        &self,
        scope: &ScopeKey,
        record: &SegmentRecord,
        owner: &str,
        continuing: bool,
    ) -> Result<FlushScriptOutcome> {
        let manifest_path = self.store.paths().account_manifest(&scope.account_id)?.0;
        let lock_timeout = self
            .store
            .pathlock_manager()
            .default_lock_timeout()
            .max(std::time::Duration::from_secs(30));
        let lease = self
            .store
            .pathlock_manager()
            .acquire_exact(&manifest_path, lock_timeout, None)
            .await?;
        let operation = async {
            self.validate_scope(scope).await?;
            self.flush_once(scope, std::slice::from_ref(record), true, owner, continuing)
                .await
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&lease)
            .await
            .map_err(Error::from);
        match (operation, release) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error),
            (Ok(result), Ok(())) => Ok(result),
        }
    }

    /// Reads one stable cache state and head-list snapshot.
    async fn snapshot(
        &self,
        scope: &ScopeKey,
    ) -> Result<(CacheState, Vec<SegmentRecord>, Vec<Bytes>)> {
        self.validate_scope(scope).await?;
        let keys = self.keys_for_scope(scope)?;
        loop {
            let before = self.read_state_once(scope, &keys).await?;
            let raw_records = self
                .runtime
                .lrange(&keys.head, 0, -1)
                .await
                .map_err(|error| Self::cache_error("read head", error))?;
            let after = self.read_state_once(scope, &keys).await?;
            if before == after {
                let records = raw_records
                    .iter()
                    .map(|raw| {
                        serde_json::from_slice::<SegmentRecord>(raw)
                            .map_err(|error| Error::Serialization(error.to_string()))
                    })
                    .collect::<Result<Vec<_>>>()?;
                Self::validate_snapshot(&before, &records)?;
                return Ok((before, records, raw_records));
            }
        }
    }

    /// Reads one cache state value through the shared seal script.
    async fn read_state_once(
        &self,
        scope: &ScopeKey,
        keys: &CachePartitionKeys,
    ) -> Result<CacheState> {
        let mut args = vec![Bytes::from_static(b"read")];
        args.extend(Self::scope_args(scope));
        let values = self.execute_array(SEAL_SCRIPT_ID, keys, args).await?;
        match Self::status(&values, "read state")? {
            "ok" => {
                let state = CacheState {
                    scope: ScopeKey {
                        account_id: Self::string_value(&values, 1, "state account")?.to_string(),
                        partition_id: Self::u32_value(&values, 2, "state partition")?,
                        epoch: Self::u64_value(&values, 3, "state epoch")?,
                    },
                    version: Self::u32_value(&values, 4, "state version")?,
                    next_seq: Self::u64_value(&values, 5, "state next_seq")?,
                    marker_seq: Self::u64_value(&values, 6, "state marker_seq")?,
                    segments: Self::json_value(&values, 7, "state segments")?,
                    backend_states: Self::json_value(&values, 8, "state backend states")?,
                };
                Self::validate_state(scope, &state)?;
                Ok(state)
            }
            "not_found" => Err(Error::not_found(keys.state.clone())),
            "scope_mismatch" => Err(Error::invalid_operation(
                "Cache-backed multi-write state scope does not match",
            )),
            other => Err(Self::unknown_status("read state", other)),
        }
    }

    /// Validates cache state fields that do not depend on the mutable head.
    fn validate_state(scope: &ScopeKey, state: &CacheState) -> Result<()> {
        if &state.scope != scope {
            return Err(Error::Serialization(
                "Cache-backed multi-write state has the wrong scope".to_string(),
            ));
        }
        if state.version != 1 || state.next_seq == 0 {
            return Err(Error::Serialization(
                "Cache-backed multi-write state has invalid version or next_seq".to_string(),
            ));
        }
        let mut previous_to = None;
        let mut paths = HashSet::new();
        for descriptor in &state.segments {
            descriptor.validate()?;
            if descriptor.segment_to_seq.is_none() || !paths.insert(&descriptor.path) {
                return Err(Error::Serialization(
                    "cache state contains an invalid sealed descriptor".to_string(),
                ));
            }
            if previous_to.is_some_and(|to| descriptor.segment_from_seq != to + 1) {
                return Err(Error::Serialization(
                    "cache sealed descriptors are not contiguous".to_string(),
                ));
            }
            previous_to = descriptor.segment_to_seq;
        }
        for (backend, backend_state) in &state.backend_states {
            if backend.trim().is_empty() {
                return Err(Error::Serialization(
                    "cache backend id must not be empty".to_string(),
                ));
            }
            backend_state.validate(state.next_seq - 1)?;
        }
        if state.marker_seq >= state.next_seq {
            return Err(Error::Serialization(
                "cache marker is ahead of next_seq".to_string(),
            ));
        }
        Ok(())
    }

    /// Validates head ordering and its continuity with cache manifest state.
    fn validate_snapshot(state: &CacheState, records: &[SegmentRecord]) -> Result<()> {
        if records.len() > MAX_SEGMENT_RECORDS {
            return Err(Error::Serialization(
                "cache head contains too many records".to_string(),
            ));
        }
        for (index, record) in records.iter().enumerate() {
            record.validate()?;
            if index > 0 && record.seq != records[index - 1].seq + 1 {
                return Err(Error::Serialization(
                    "cache head contains a sequence gap".to_string(),
                ));
            }
        }
        if let Some(first) = records.first() {
            if state
                .segments
                .last()
                .and_then(|descriptor| descriptor.segment_to_seq)
                .is_some_and(|last| first.seq != last + 1)
            {
                return Err(Error::Serialization(
                    "cache head does not follow sealed segments".to_string(),
                ));
            }
            if records.last().unwrap().seq.checked_add(1) != Some(state.next_seq) {
                return Err(Error::Serialization(
                    "cache head does not end at next_seq".to_string(),
                ));
            }
        } else if state
            .segments
            .last()
            .and_then(|descriptor| descriptor.segment_to_seq)
            .is_some_and(|last| last.checked_add(1) != Some(state.next_seq))
        {
            return Err(Error::Serialization(
                "cache next_seq does not follow sealed segments".to_string(),
            ));
        }
        if state.marker_seq != 0
            && records.last().map(|record| record.seq) != Some(state.marker_seq)
        {
            return Err(Error::Serialization(
                "cache marker is not the final head record".to_string(),
            ));
        }
        Ok(())
    }

    /// Converts a stable snapshot into the public validated manifest.
    fn manifest(state: CacheState, records: &[SegmentRecord]) -> Result<SegmentManifest> {
        let mut manifest = SegmentManifest {
            version: state.version,
            next_seq: state.next_seq,
            segments: state.segments,
            backend_states: state.backend_states,
        };
        if let Some(first) = records.first() {
            let bytes = encode_segment(records)?;
            manifest.segments.push(SegmentDescriptor {
                path: Self::head_name(first.seq),
                segment_from_seq: first.seq,
                segment_to_seq: None,
                record_count: records.len() as u32,
                byte_len: bytes.len() as u64,
                checksum: None,
            });
        }
        manifest.validate()?;
        Ok(manifest)
    }

    /// Persists one immutable segment snapshot on the shared raw primary.
    async fn persist_sealed(
        &self,
        scope: &ScopeKey,
        records: &[SegmentRecord],
    ) -> Result<SegmentDescriptor> {
        let first = records
            .first()
            .ok_or_else(|| Error::invalid_operation("cannot seal an empty cache head"))?
            .seq;
        let last = records.last().unwrap().seq;
        let bytes = encode_segment(records)?;
        let descriptor = SegmentDescriptor {
            path: Self::sealed_name(first, last),
            segment_from_seq: first,
            segment_to_seq: Some(last),
            record_count: records.len() as u32,
            byte_len: bytes.len() as u64,
            checksum: Some(sha256_hex(&bytes)),
        };
        let directory = self
            .store
            .paths()
            .segments_dir(&scope.account_id, scope.partition_id)?
            .0;
        self.store
            .write_immutable(&format!("{directory}/{}", descriptor.path), &bytes)
            .await?;
        Ok(descriptor)
    }

    /// Commits one immutable descriptor and trims its exact cache head prefix.
    pub(crate) async fn commit_seal(
        &self,
        scope: &ScopeKey,
        raw_records: &[Bytes],
        descriptor: &SegmentDescriptor,
        owner: Option<&str>,
        keep_owner: bool,
    ) -> Result<()> {
        let keys = self.keys_for_scope(scope)?;
        let mut args = vec![Bytes::from_static(b"commit")];
        args.extend(Self::scope_args(scope));
        args.push(Bytes::from(descriptor.segment_from_seq.to_string()));
        args.push(Bytes::from(descriptor.segment_to_seq.unwrap().to_string()));
        args.push(Bytes::from(descriptor.record_count.to_string()));
        args.push(Bytes::from(serde_json::to_vec(descriptor)?));
        args.push(Bytes::from(owner.unwrap_or_default().to_string()));
        args.push(Bytes::from(if keep_owner { "1" } else { "0" }));
        args.extend(raw_records.iter().cloned());
        let values = self.execute_array(SEAL_SCRIPT_ID, &keys, args).await?;
        match Self::status(&values, "seal")? {
            "ok" | "already" => Ok(()),
            "not_found" => Err(Error::not_found(keys.state)),
            "busy" => Err(Error::would_block("cache head is owned by an active flush")),
            "fenced" => Err(Error::would_block(
                "cache seal owner was fenced by another writer",
            )),
            "changed" => Err(Error::would_block(
                "cache head changed while committing a sealed prefix",
            )),
            "scope_mismatch" => Err(Error::invalid_operation(
                "Cache-backed multi-write state scope does not match",
            )),
            "invalid" => Err(Self::script_invalid(&values, "seal")),
            other => Err(Self::unknown_status("seal", other)),
        }
    }

    /// Seals one snapshot while enforcing an optional flush-owner fence.
    async fn seal_head_with_owner(
        &self,
        scope: &ScopeKey,
        owner: Option<&str>,
        keep_owner: bool,
    ) -> Result<Option<SegmentDescriptor>> {
        let (_, records, raw_records) = self.snapshot(scope).await?;
        if records.is_empty() {
            return Ok(None);
        }
        let directory = self
            .store
            .paths()
            .segments_dir(&scope.account_id, scope.partition_id)?
            .0;
        let path = format!(
            "{directory}/{}",
            Self::sealed_name(records.first().unwrap().seq, records.last().unwrap().seq)
        );
        let lease = self
            .store
            .pathlock_manager()
            .acquire_exact(
                &path,
                self.store
                    .pathlock_manager()
                    .default_lock_timeout()
                    .max(std::time::Duration::from_secs(30)),
                None,
            )
            .await?;
        let operation = async {
            let descriptor = self.persist_sealed(scope, &records).await?;
            self.commit_seal(scope, &raw_records, &descriptor, owner, keep_owner)
                .await?;
            Ok(Some(descriptor))
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&lease)
            .await
            .map_err(Error::from);
        match operation {
            Ok(result) => release.map(|()| result),
            Err(error) => Err(error),
        }
    }

    /// Returns whether one marker is the end of a referenced sealed segment.
    async fn marker_is_sealed(&self, scope: &ScopeKey, seq: u64) -> Result<bool> {
        Ok(self
            .read_manifest(scope)
            .await?
            .segments
            .iter()
            .any(|descriptor| descriptor.segment_to_seq == Some(seq)))
    }

    /// Returns the deterministic mutable head filename.
    fn head_name(from_seq: u64) -> String {
        format!("{HEAD_SEGMENT_FILE_PREFIX}{from_seq:020}{SEGMENT_FILE_EXTENSION}")
    }

    /// Returns the deterministic immutable segment filename.
    fn sealed_name(from_seq: u64, to_seq: u64) -> String {
        format!("{SEALED_SEGMENT_FILE_PREFIX}{from_seq:020}-{to_seq:020}{SEGMENT_FILE_EXTENSION}")
    }

    /// Rebuild missing mutable state from the latest checkpoint and retained segments.
    async fn recovery_manifest(
        &self,
        scope: &ScopeKey,
        mut manifest: SegmentManifest,
    ) -> Result<SegmentManifest> {
        manifest.validate()?;
        for state in manifest.backend_states.values_mut() {
            state.synced_seq = 0;
        }
        manifest.segments.clear();
        manifest.next_seq = match self
            .store
            .read_json::<CheckpointsManifest>(
                &self
                    .store
                    .paths()
                    .checkpoints_manifest(&scope.account_id, scope.partition_id)?
                    .0,
            )
            .await
        {
            Ok(pointer) => {
                pointer.validate()?;
                pointer.latest_checkpoint.map_or(1, |latest| {
                    latest.checkpoint_to_seq.checked_add(1).unwrap_or(u64::MAX)
                })
            }
            Err(Error::NotFound(_)) => 1,
            Err(error) => return Err(error),
        };
        let directory = self
            .store
            .paths()
            .segments_dir(&scope.account_id, scope.partition_id)?
            .0;
        let mut candidates = match self.store.list_directory(&directory).await {
            Ok(entries) => entries
                .into_iter()
                .filter_map(|entry| {
                    (!entry.is_dir)
                        .then(|| Self::sealed_range(&entry.name).map(|range| (range, entry.name)))
                        .flatten()
                })
                .collect::<Vec<_>>(),
            Err(Error::NotFound(_)) => Vec::new(),
            Err(error) => return Err(error),
        };
        candidates.sort_by_key(|(range, _)| *range);
        for ((from, to), path) in candidates {
            if to < manifest.next_seq {
                continue;
            }
            if from != manifest.next_seq {
                break;
            }
            let bytes = self
                .store
                .read_bytes(&format!("{directory}/{path}"))
                .await?;
            let decoded = decode_segment(&bytes)?;
            if decoded.truncated_tail
                || decoded.records.first().map(|record| record.seq) != Some(from)
                || decoded.records.last().map(|record| record.seq) != Some(to)
            {
                return Err(Error::Serialization(
                    "retained cache segment does not match its filename".to_string(),
                ));
            }
            manifest.segments.push(SegmentDescriptor {
                path,
                segment_from_seq: from,
                segment_to_seq: Some(to),
                record_count: decoded.records.len() as u32,
                byte_len: bytes.len() as u64,
                checksum: Some(sha256_hex(&bytes)),
            });
            manifest.next_seq = to
                .checked_add(1)
                .ok_or_else(|| Error::Serialization("segment sequence overflow".to_string()))?;
        }
        manifest.validate()?;
        Ok(manifest)
    }

    /// Parse one deterministic sealed segment filename.
    fn sealed_range(name: &str) -> Option<(u64, u64)> {
        let range = name
            .strip_prefix(SEALED_SEGMENT_FILE_PREFIX)?
            .strip_suffix(SEGMENT_FILE_EXTENSION)?;
        let (from, to) = range.split_once('-')?;
        Some((from.parse().ok()?, to.parse().ok()?))
    }

    /// Encodes account partition identity as script arguments.
    fn scope_args(scope: &ScopeKey) -> Vec<Bytes> {
        vec![
            Bytes::from(scope.account_id.clone()),
            Bytes::from(scope.partition_id.to_string()),
            Bytes::from(scope.epoch.to_string()),
        ]
    }

    /// Returns the leading UTF-8 status from one script result.
    fn status<'a>(values: &'a [ScriptValue], operation: &str) -> Result<&'a str> {
        Self::string_value(values, 0, operation)
    }

    /// Reads one UTF-8 byte result at the requested index.
    fn string_value<'a>(values: &'a [ScriptValue], index: usize, field: &str) -> Result<&'a str> {
        match values.get(index) {
            Some(ScriptValue::Bytes(value)) => std::str::from_utf8(value).map_err(|error| {
                Error::Serialization(format!(
                    "Cache-backed multi-write {field} is not UTF-8: {error}"
                ))
            }),
            other => Err(Error::Serialization(format!(
                "Cache-backed multi-write {field} has invalid type: {other:?}"
            ))),
        }
    }

    /// Reads one non-negative integer script result as u64.
    fn u64_value(values: &[ScriptValue], index: usize, field: &str) -> Result<u64> {
        match values.get(index) {
            Some(ScriptValue::Integer(value)) => u64::try_from(*value).map_err(|_| {
                Error::Serialization(format!("Cache-backed multi-write {field} is negative"))
            }),
            Some(ScriptValue::Bytes(value)) => std::str::from_utf8(value)
                .ok()
                .and_then(|value| value.parse().ok())
                .ok_or_else(|| {
                    Error::Serialization(format!(
                        "Cache-backed multi-write {field} is not an integer"
                    ))
                }),
            other => Err(Error::Serialization(format!(
                "Cache-backed multi-write {field} has invalid type: {other:?}"
            ))),
        }
    }

    /// Reads one script integer as u32.
    fn u32_value(values: &[ScriptValue], index: usize, field: &str) -> Result<u32> {
        u32::try_from(Self::u64_value(values, index, field)?).map_err(|_| {
            Error::Serialization(format!("Cache-backed multi-write {field} exceeds u32"))
        })
    }

    /// Reads one script integer as usize.
    fn usize_value(values: &[ScriptValue], index: usize, field: &str) -> Result<usize> {
        usize::try_from(Self::u64_value(values, index, field)?).map_err(|_| {
            Error::Serialization(format!("Cache-backed multi-write {field} exceeds usize"))
        })
    }

    /// Deserializes one JSON byte result.
    fn json_value<T>(values: &[ScriptValue], index: usize, field: &str) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
    {
        match values.get(index) {
            Some(ScriptValue::Bytes(value)) => serde_json::from_slice(value)
                .map_err(|error| Error::Serialization(format!("{field}: {error}"))),
            other => Err(Error::Serialization(format!(
                "Cache-backed multi-write {field} has invalid type: {other:?}"
            ))),
        }
    }

    /// Builds a validated operation error from a script response.
    fn script_invalid(values: &[ScriptValue], operation: &str) -> Error {
        match Self::string_value(values, 1, operation) {
            Ok(message) => Error::invalid_operation(message),
            Err(error) => error,
        }
    }

    /// Builds an error for an unknown script status.
    fn unknown_status(operation: &str, status: &str) -> Error {
        Error::Serialization(format!(
            "Cache-backed multi-write {operation} returned unknown status: {status}"
        ))
    }

    /// Maps cache runtime failures into existing RAGFS error categories.
    fn cache_error(operation: &str, error: CacheError) -> Error {
        match error {
            CacheError::Timeout(message) => {
                Error::Timeout(format!("Cache-backed multi-write {operation}: {message}"))
            }
            CacheError::Unavailable(message) => {
                Error::Network(format!("Cache-backed multi-write {operation}: {message}"))
            }
            CacheError::Closed => Error::Network(format!(
                "Cache-backed multi-write {operation}: runtime is closed"
            )),
            CacheError::InvalidData(message) => {
                Error::Serialization(format!("Cache-backed multi-write {operation}: {message}"))
            }
            other => Error::internal(format!("Cache-backed multi-write {operation}: {other}")),
        }
    }
}

#[async_trait]
impl MultiWriteProvider for CacheProvider {
    /// Atomically installs reconstructed state only when cache state is absent.
    async fn bootstrap_manifest(&self, scope: &ScopeKey, manifest: SegmentManifest) -> Result<()> {
        self.validate_scope(scope).await?;
        match self.read_manifest(scope).await {
            Ok(_) => return Ok(()),
            Err(Error::NotFound(_)) => {}
            Err(error) => return Err(error),
        }
        let manifest = self.recovery_manifest(scope, manifest).await?;
        let keys = self.keys_for_scope(scope)?;
        let mut args = vec![Bytes::from_static(b"bootstrap")];
        args.extend(Self::scope_args(scope));
        args.push(Bytes::from(manifest.version.to_string()));
        args.push(Bytes::from(manifest.next_seq.to_string()));
        args.push(Bytes::from(serde_json::to_vec(&manifest.segments)?));
        args.push(Bytes::from(serde_json::to_vec(&manifest.backend_states)?));
        let values = self.execute_array(SEAL_SCRIPT_ID, &keys, args).await?;
        match Self::status(&values, "bootstrap")? {
            "ok" | "exists" => self.read_manifest(scope).await.map(|_| ()),
            "invalid" => Err(Self::script_invalid(&values, "bootstrap")),
            other => Err(Self::unknown_status("bootstrap", other)),
        }
    }

    /// Flushes normal events through bounded atomic cache appends.
    async fn flush(
        &self,
        _route_hint: Option<&PartitionContext>,
        events: Vec<PendingEvent>,
    ) -> Result<FlushResult> {
        Self::validate_flush_events(&events)?;
        let records = events
            .iter()
            .map(Self::record)
            .collect::<Result<Vec<_>>>()?;
        let mut offset = 0;
        let mut route = None;
        let mut continuing = false;
        let mut first_seq = None;
        let mut last_seq = None;
        let mut sealed_segments = Vec::new();
        let owner = Uuid::new_v4().to_string();
        while offset < records.len() {
            let (current_route, outcome) = self
                .flush_routed_once(
                    &events,
                    route.as_ref().map(|route: &PartitionContext| &route.scope),
                    &records[offset..],
                    &owner,
                    continuing,
                )
                .await?;
            match outcome {
                FlushScriptOutcome::SealRequired => {
                    route.get_or_insert(current_route);
                    continuing = true;
                    if let Some(descriptor) = self
                        .seal_head_with_owner(&route.as_ref().unwrap().scope, Some(&owner), true)
                        .await?
                    {
                        if !sealed_segments
                            .iter()
                            .any(|sealed: &SegmentDescriptor| sealed.path == descriptor.path)
                        {
                            sealed_segments.push(descriptor);
                        }
                    }
                }
                FlushScriptOutcome::Busy => {
                    tokio::time::sleep(std::time::Duration::from_millis(1)).await;
                }
                FlushScriptOutcome::Appended(result) => {
                    if result.appended == 0 {
                        return Err(Error::Serialization(
                            "cache-backed flush appended no records".to_string(),
                        ));
                    }
                    route.get_or_insert(current_route);
                    first_seq.get_or_insert(result.first_seq);
                    last_seq = Some(result.last_seq);
                    offset += result.appended;
                    if result.needs_seal {
                        let keep_owner = offset < records.len();
                        if let Some(descriptor) = self
                            .seal_head_with_owner(
                                &route.as_ref().unwrap().scope,
                                Some(&owner),
                                keep_owner,
                            )
                            .await?
                        {
                            if !sealed_segments
                                .iter()
                                .any(|sealed| sealed.path == descriptor.path)
                            {
                                sealed_segments.push(descriptor);
                            }
                        }
                        continuing = keep_owner;
                    } else if offset < records.len() {
                        return Err(Error::Serialization(
                            "cache-backed flush released its owner before completing the batch"
                                .to_string(),
                        ));
                    }
                }
            }
        }
        Ok(FlushResult {
            scope: route.unwrap().scope,
            first_seq: first_seq.unwrap(),
            last_seq: last_seq.unwrap(),
            sealed_segments,
        })
    }

    /// Reads and validates committed cache head records in one half-open range.
    async fn read_committed_head(
        &self,
        scope: &ScopeKey,
        from_seq_inclusive: u64,
        to_seq_exclusive: u64,
    ) -> Result<Vec<SegmentRecord>> {
        let (state, records, _) = self.snapshot(scope).await?;
        if from_seq_inclusive > to_seq_exclusive || to_seq_exclusive > state.next_seq {
            return Err(Error::invalid_operation(
                "committed head range is outside the manifest",
            ));
        }
        let head_from = records
            .first()
            .map(|record| record.seq)
            .unwrap_or(state.next_seq);
        if from_seq_inclusive < head_from {
            return Err(Error::invalid_operation(
                "committed head range starts before the current cache head",
            ));
        }
        let expected_len = usize::try_from(to_seq_exclusive - from_seq_inclusive)
            .map_err(|_| Error::invalid_operation("committed head range is too large"))?;
        let selected = records
            .into_iter()
            .filter(|record| record.seq >= from_seq_inclusive && record.seq < to_seq_exclusive)
            .collect::<Vec<_>>();
        if selected.len() != expected_len
            || selected.iter().enumerate().any(|(index, record)| {
                record.seq
                    != from_seq_inclusive
                        .checked_add(index as u64)
                        .unwrap_or(u64::MAX)
            })
        {
            return Err(Error::Serialization(
                "cache committed head range is incomplete".to_string(),
            ));
        }
        Ok(selected)
    }

    /// Reads one complete committed range from sealed storage and the cache head.
    async fn read_committed_range(
        &self,
        scope: &ScopeKey,
        from_seq_inclusive: u64,
        to_seq_exclusive: u64,
    ) -> Result<Vec<SegmentRecord>> {
        let (state, head, _) = self.snapshot(scope).await?;
        if from_seq_inclusive > to_seq_exclusive || to_seq_exclusive > state.next_seq {
            return Err(Error::invalid_operation(
                "committed range is outside the manifest",
            ));
        }
        let mut records = Vec::new();
        for descriptor in &state.segments {
            if descriptor.segment_from_seq >= to_seq_exclusive
                || descriptor
                    .segment_to_seq
                    .is_some_and(|to| to < from_seq_inclusive)
            {
                continue;
            }
            records.extend(read_sealed_segment(&self.store, scope, descriptor).await?);
        }
        records.extend(head);
        select_record_range(records, from_seq_inclusive, to_seq_exclusive)
    }

    /// Reads one immutable sealed segment named by a validated descriptor.
    async fn read_sealed_segment(
        &self,
        scope: &ScopeKey,
        descriptor: &SegmentDescriptor,
    ) -> Result<Vec<SegmentRecord>> {
        read_sealed_segment(&self.store, scope, descriptor).await
    }

    /// Returns validated sealed state plus a derived mutable-head descriptor.
    async fn read_manifest(&self, scope: &ScopeKey) -> Result<SegmentManifest> {
        let (state, records, _) = self.snapshot(scope).await?;
        Self::manifest(state, &records)
    }

    /// Appends one caller-scoped marker and seals it before returning.
    async fn append_marker(
        &self,
        scope: &ScopeKey,
        event: &PendingEvent,
    ) -> Result<MarkerPosition> {
        self.validate_scope(scope).await?;
        self.validate_marker(scope, event)?;
        let record = Self::record(event)?;
        let owner = Uuid::new_v4().to_string();
        let mut continuing = false;
        loop {
            match self
                .flush_marker_once(scope, &record, &owner, continuing)
                .await?
            {
                FlushScriptOutcome::SealRequired => {
                    continuing = true;
                    self.seal_head_with_owner(scope, Some(&owner), true).await?;
                }
                FlushScriptOutcome::Busy => {
                    tokio::time::sleep(std::time::Duration::from_millis(1)).await;
                }
                FlushScriptOutcome::Appended(result) => {
                    if result.appended != 1 || !result.needs_seal {
                        return Err(Error::Serialization(
                            "cache marker append returned an invalid result".to_string(),
                        ));
                    }
                    self.seal_head_with_owner(scope, Some(&owner), false)
                        .await?;
                    if !self.marker_is_sealed(scope, result.first_seq).await? {
                        return Err(Error::Serialization(
                            "cache marker is not referenced by a sealed segment".to_string(),
                        ));
                    }
                    return Ok(MarkerPosition {
                        partition_id: scope.partition_id,
                        epoch: scope.epoch,
                        seq: result.first_seq,
                    });
                }
            }
        }
    }

    /// Advances one backend's monotonic progress atomically through CacheRuntime.
    async fn advance_backend_state(
        &self,
        scope: &ScopeKey,
        backend_id: &str,
        synced_seq: u64,
    ) -> Result<()> {
        self.validate_scope(scope).await?;
        if backend_id.trim().is_empty() {
            return Err(Error::invalid_operation("backend id must not be empty"));
        }
        let keys = self.keys_for_scope(scope)?;
        let mut args = Self::scope_args(scope);
        args.push(Bytes::from(backend_id.to_string()));
        args.push(Bytes::from(synced_seq.to_string()));
        let values = self.execute_array(ADVANCE_SCRIPT_ID, &keys, args).await?;
        match Self::status(&values, "advance backend")? {
            "ok" => Ok(()),
            "not_found" => Err(Error::not_found(keys.state)),
            "scope_mismatch" => Err(Error::invalid_operation(
                "Cache-backed multi-write state scope does not match",
            )),
            "invalid" => Err(Self::script_invalid(&values, "advance backend")),
            other => Err(Self::unknown_status("advance backend", other)),
        }
    }

    /// Atomically validates and removes exactly the requested sealed descriptors.
    async fn prune_sealed(
        &self,
        scope: &ScopeKey,
        removable_paths: &[String],
    ) -> Result<Vec<SegmentDescriptor>> {
        let path = self.store.paths().account_manifest(&scope.account_id)?.0;
        let lease = self
            .store
            .pathlock_manager()
            .acquire_exact(
                &path,
                self.store
                    .pathlock_manager()
                    .default_lock_timeout()
                    .max(std::time::Duration::from_secs(30)),
                None,
            )
            .await?;
        let operation = async {
            if !self.store.gc_scope_is_safe(scope).await? {
                return Ok(Vec::new());
            }
            let keys = self.keys_for_scope(scope)?;
            let mut args = vec![Bytes::from_static(b"prune")];
            args.extend(Self::scope_args(scope));
            args.push(Bytes::from(serde_json::to_vec(removable_paths)?));
            let values = self.execute_array(SEAL_SCRIPT_ID, &keys, args).await?;
            match Self::status(&values, "prune")? {
                "ok" => Self::json_value(&values, 1, "pruned descriptors"),
                "not_found" => Err(Error::not_found(keys.state)),
                "scope_mismatch" => Err(Error::invalid_operation(
                    "Cache-backed multi-write state scope does not match",
                )),
                "invalid" => Err(Self::script_invalid(&values, "prune")),
                other => Err(Self::unknown_status("prune", other)),
            }
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&lease)
            .await
            .map_err(Error::from);
        match operation {
            Ok(result) => release.map(|()| result),
            Err(error) => Err(error),
        }
    }
}
