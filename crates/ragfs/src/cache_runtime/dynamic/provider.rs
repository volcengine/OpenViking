use super::abi::*;
use super::config::DynamicProviderConfig;
use super::loader;
use crate::cache_runtime::provider::{CacheOperation, CacheProvider};
use crate::cache_runtime::{
    CacheError, CacheResult, Expiration, ListDirection, ListInsertPosition, ListInsertRequest,
    ListMoveRequest, ScriptRegistry, ScriptRequest, ScriptResult, ScriptValue, SetCondition,
    SetOptions, SetResult,
};
use async_trait::async_trait;
use bytes::Bytes;
use libloading::Library;
use std::alloc::{alloc, dealloc, Layout};
use std::collections::HashMap;
use std::ffi::c_void;
use std::mem::size_of;
use std::ptr;
use std::slice;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock, RwLock};

const LIFECYCLE_OPEN: u8 = 0;
const LIFECYCLE_CLOSING: u8 = 1;
const LIFECYCLE_CLOSED: u8 = 2;
const MAX_HOST_ALLOCATION_BYTES: usize = 128 * 1024 * 1024;
const MAX_BUFFER_BYTES: usize = 64 * 1024 * 1024;
const MAX_ERROR_BYTES: usize = 64 * 1024;
const MAX_ARRAY_ITEMS: usize = 100_000;
const MAX_AGGREGATE_BYTES: usize = 128 * 1024 * 1024;
const MAX_SCRIPT_DEPTH: usize = 64;
const MAX_SCRIPT_NODES: usize = 100_000;

static HOST_API: HostApiV1 = HostApiV1 {
    abi_version: ABI_VERSION_V1,
    struct_size: size_of::<HostApiV1>(),
    alloc: host_alloc,
    dealloc: host_dealloc,
};

pub(crate) struct DynamicProvider {
    state: Arc<DynamicState>,
    scripts: Arc<ScriptRegistry>,
}

struct DynamicState {
    _library: Library,
    api: ProviderApiV1,
    handle: *mut c_void,
    calls: RwLock<()>,
    lifecycle: AtomicU8,
}

// The ABI requires the provider handle to support concurrent calls. A provider
// backed by a non-thread-safe SDK must serialize or pool internally.
unsafe impl Send for DynamicState {}
unsafe impl Sync for DynamicState {}

impl DynamicProvider {
    pub(crate) async fn connect(
        config: DynamicProviderConfig,
        scripts: Arc<ScriptRegistry>,
    ) -> CacheResult<Self> {
        let state = tokio::task::spawn_blocking(move || connect_state(config))
            .await
            .map_err(|error| {
                CacheError::Internal(format!("dynamic provider create task failed: {error}"))
            })??;
        let provider = Self { state, scripts };
        if let Err(error) = provider.ping().await {
            let _ = provider.close().await;
            return Err(error);
        }
        Ok(provider)
    }

    async fn call<T, F>(&self, operation: &'static str, call: F) -> CacheResult<T>
    where
        T: Send + 'static,
        F: FnOnce(&DynamicState) -> CacheResult<T> + Send + 'static,
    {
        let state = Arc::clone(&self.state);
        tokio::task::spawn_blocking(move || call(&state))
            .await
            .map_err(|error| {
                CacheError::Internal(format!("dynamic provider {operation} task failed: {error}"))
            })?
    }

    async fn list_push(
        &self,
        operation: CacheOperation,
        key: &str,
        values: Vec<Bytes>,
    ) -> CacheResult<u64> {
        if values.is_empty() {
            return Err(CacheError::InvalidArgument(format!(
                "{} requires at least one value",
                operation.name()
            )));
        }
        let key = key.to_string();
        self.call(operation.name(), move |state| {
            state.with_call(operation.name(), |api, handle| {
                let callback = match operation {
                    CacheOperation::Lpush => required(api.lpush, operation)?,
                    CacheOperation::Rpush => required(api.rpush, operation)?,
                    _ => unreachable!("list_push only accepts push operations"),
                };
                let raw_values = byte_slices(&values);
                let mut length = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        raw_values.as_ptr(),
                        raw_values.len(),
                        &mut length,
                        &mut error,
                    )
                };
                status_result(status, error, operation.name())?;
                Ok(length)
            })
        })
        .await
    }

    async fn list_pop(
        &self,
        operation: CacheOperation,
        key: &str,
        count: Option<u64>,
    ) -> CacheResult<Vec<Bytes>> {
        let key = key.to_string();
        self.call(operation.name(), move |state| {
            state.with_call(operation.name(), |api, handle| {
                let callback = match operation {
                    CacheOperation::Lpop => required(api.lpop, operation)?,
                    CacheOperation::Rpop => required(api.rpop, operation)?,
                    _ => unreachable!("list_pop only accepts pop operations"),
                };
                let mut output = OwnedBufferArrayV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        u8::from(count.is_some()),
                        count.unwrap_or_default(),
                        &mut output,
                        &mut error,
                    )
                };
                take_output(
                    status,
                    error,
                    operation.name(),
                    output,
                    take_buffer_array,
                    discard_buffer_array,
                )
            })
        })
        .await
    }
}

fn connect_state(config: DynamicProviderConfig) -> CacheResult<Arc<DynamicState>> {
    let loaded = loader::load(&config.library_path)?;
    let create = loaded.api.create.expect("validated create callback");
    let mut handle = ptr::null_mut();
    let mut error = OwnedBufferV1::default();
    let params = ByteSliceV1::from_slice(config.params_json.as_bytes());
    let status = unsafe { create(&HOST_API, params, &mut handle, &mut error) };
    if let Err(error) = status_result(status, error, "create") {
        if !handle.is_null() {
            close_handle(loaded.api, handle);
        }
        return Err(error);
    }
    if handle.is_null() {
        return Err(CacheError::InvalidData(
            "dynamic provider create returned a null handle".into(),
        ));
    }

    Ok(Arc::new(DynamicState {
        _library: loaded.library,
        api: loaded.api,
        handle,
        calls: RwLock::new(()),
        lifecycle: AtomicU8::new(LIFECYCLE_OPEN),
    }))
}

impl DynamicState {
    fn with_call<T>(
        &self,
        operation: &str,
        call: impl FnOnce(&ProviderApiV1, *mut c_void) -> CacheResult<T>,
    ) -> CacheResult<T> {
        let _guard = self
            .calls
            .read()
            .map_err(|_| CacheError::Internal("dynamic provider call lock poisoned".into()))?;
        if self.lifecycle.load(Ordering::Acquire) != LIFECYCLE_OPEN {
            return Err(CacheError::Closed);
        }
        call(&self.api, self.handle).map_err(|error| match error {
            CacheError::UnsupportedOperation(_) => {
                CacheError::UnsupportedOperation(operation.to_string())
            }
            other => other,
        })
    }

    fn supports(&self, operation: CacheOperation) -> bool {
        match operation {
            CacheOperation::Get => self.api.get.is_some(),
            CacheOperation::Set => self.api.set.is_some(),
            CacheOperation::Del => self.api.del.is_some(),
            CacheOperation::Mget => self.api.mget.is_some(),
            CacheOperation::Mset => self.api.mset.is_some(),
            CacheOperation::IncrBy => self.api.incrby.is_some(),
            CacheOperation::Sismember => self.api.sismember.is_some(),
            CacheOperation::Smembers => self.api.smembers.is_some(),
            CacheOperation::Scard => self.api.scard.is_some(),
            CacheOperation::Lpush => self.api.lpush.is_some(),
            CacheOperation::Rpush => self.api.rpush.is_some(),
            CacheOperation::Lpop => self.api.lpop.is_some(),
            CacheOperation::Rpop => self.api.rpop.is_some(),
            CacheOperation::Llen => self.api.llen.is_some(),
            CacheOperation::Lrange => self.api.lrange.is_some(),
            CacheOperation::Lindex => self.api.lindex.is_some(),
            CacheOperation::Lset => self.api.lset.is_some(),
            CacheOperation::Ltrim => self.api.ltrim.is_some(),
            CacheOperation::Lrem => self.api.lrem.is_some(),
            CacheOperation::Linsert => self.api.linsert.is_some(),
            CacheOperation::Lmove => self.api.lmove.is_some(),
            CacheOperation::ExecuteScript => self.api.execute_script.is_some(),
        }
    }

    fn ping(&self) -> CacheResult<()> {
        self.with_call("ping", |api, handle| {
            let callback = api.ping.expect("validated ping callback");
            let mut error = OwnedBufferV1::default();
            let status = unsafe { callback(handle, &mut error) };
            status_result(status, error, "ping")
        })
    }

    fn close(&self) -> CacheResult<()> {
        match self.lifecycle.compare_exchange(
            LIFECYCLE_OPEN,
            LIFECYCLE_CLOSING,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => {}
            Err(state) if matches!(state, LIFECYCLE_CLOSING | LIFECYCLE_CLOSED) => return Ok(()),
            Err(other) => {
                return Err(CacheError::Internal(format!(
                    "dynamic provider has invalid lifecycle state {other}"
                )))
            }
        }
        let _guard = self
            .calls
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let callback = self.api.close.expect("validated close callback");
        let mut error = OwnedBufferV1::default();
        let status = unsafe { callback(self.handle, &mut error) };
        self.lifecycle.store(LIFECYCLE_CLOSED, Ordering::Release);
        status_result(status, error, "close")
    }
}

impl Drop for DynamicState {
    fn drop(&mut self) {
        if self
            .lifecycle
            .compare_exchange(
                LIFECYCLE_OPEN,
                LIFECYCLE_CLOSING,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_ok()
        {
            let _guard = self
                .calls
                .write()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            close_handle(self.api, self.handle);
            self.lifecycle.store(LIFECYCLE_CLOSED, Ordering::Release);
        }
    }
}

#[async_trait]
impl CacheProvider for DynamicProvider {
    fn validate_operations(&self, operations: &[CacheOperation]) -> CacheResult<()> {
        if let Some(operation) = operations
            .iter()
            .copied()
            .find(|operation| !self.state.supports(*operation))
        {
            return Err(CacheError::UnsupportedOperation(
                operation.name().to_string(),
            ));
        }
        Ok(())
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        let key = key.to_string();
        self.call("get", move |state| {
            state.with_call("get", |api, handle| {
                let callback = required(api.get, CacheOperation::Get)?;
                let mut output = OptionalOwnedBufferV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        &mut output,
                        &mut error,
                    )
                };
                take_output(
                    status,
                    error,
                    "get",
                    output,
                    take_optional_buffer,
                    discard_optional_buffer,
                )
            })
        })
        .await
    }

    async fn set(&self, key: &str, value: Bytes, options: SetOptions) -> CacheResult<SetResult> {
        if options.keep_ttl && options.expiration.is_some() {
            return Err(CacheError::InvalidArgument(
                "set cannot combine expiration with keep_ttl".into(),
            ));
        }
        let options = set_options(options)?;
        let key = key.to_string();
        self.call("set", move |state| {
            state.with_call("set", |api, handle| {
                let callback = required(api.set, CacheOperation::Set)?;
                let mut result = SET_APPLIED;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        ByteSliceV1::from_slice(&value),
                        options,
                        &mut result,
                        &mut error,
                    )
                };
                status_result(status, error, "set")?;
                match result {
                    SET_APPLIED => Ok(SetResult::Applied),
                    SET_CONDITION_NOT_MET => Ok(SetResult::ConditionNotMet),
                    other => Err(CacheError::InvalidData(format!(
                        "dynamic provider set returned invalid result {other}"
                    ))),
                }
            })
        })
        .await
    }

    async fn del(&self, keys: &[String]) -> CacheResult<u64> {
        if keys.is_empty() {
            return Ok(0);
        }
        let keys = keys.to_vec();
        self.call("del", move |state| {
            state.with_call("del", |api, handle| {
                let callback = required(api.del, CacheOperation::Del)?;
                let raw_keys = string_slices(&keys);
                let mut removed = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        raw_keys.as_ptr(),
                        raw_keys.len(),
                        &mut removed,
                        &mut error,
                    )
                };
                status_result(status, error, "del")?;
                Ok(removed)
            })
        })
        .await
    }

    async fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        if keys.is_empty() {
            return Ok(Vec::new());
        }
        let keys = keys.to_vec();
        self.call("mget", move |state| {
            state.with_call("mget", |api, handle| {
                let callback = required(api.mget, CacheOperation::Mget)?;
                let raw_keys = string_slices(&keys);
                let mut output = OptionalOwnedBufferArrayV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        raw_keys.as_ptr(),
                        raw_keys.len(),
                        &mut output,
                        &mut error,
                    )
                };
                let values = take_output(
                    status,
                    error,
                    "mget",
                    output,
                    take_optional_buffer_array,
                    discard_optional_buffer_array,
                )?;
                if values.len() != keys.len() {
                    return Err(CacheError::InvalidData(format!(
                        "dynamic provider mget returned {} values for {} keys",
                        values.len(),
                        keys.len()
                    )));
                }
                Ok(values)
            })
        })
        .await
    }

    async fn mset(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        if entries.is_empty() {
            return Ok(());
        }
        self.call("mset", move |state| {
            state.with_call("mset", |api, handle| {
                let callback = required(api.mset, CacheOperation::Mset)?;
                let raw_entries = entries
                    .iter()
                    .map(|(key, value)| KeyValueV1 {
                        key: ByteSliceV1::from_slice(key.as_bytes()),
                        value: ByteSliceV1::from_slice(value),
                    })
                    .collect::<Vec<_>>();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(handle, raw_entries.as_ptr(), raw_entries.len(), &mut error)
                };
                status_result(status, error, "mset")
            })
        })
        .await
    }

    async fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        let key = key.to_string();
        self.call("incrby", move |state| {
            state.with_call("incrby", |api, handle| {
                let callback = required(api.incrby, CacheOperation::IncrBy)?;
                let mut value = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        delta,
                        &mut value,
                        &mut error,
                    )
                };
                status_result(status, error, "incrby")?;
                Ok(value)
            })
        })
        .await
    }

    async fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool> {
        let key = key.to_string();
        let member = member.to_vec();
        self.call("sismember", move |state| {
            state.with_call("sismember", |api, handle| {
                let callback = required(api.sismember, CacheOperation::Sismember)?;
                let mut present = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        ByteSliceV1::from_slice(&member),
                        &mut present,
                        &mut error,
                    )
                };
                status_result(status, error, "sismember")?;
                take_bool(present, "sismember")
            })
        })
        .await
    }

    async fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>> {
        let key = key.to_string();
        self.call("smembers", move |state| {
            state.with_call("smembers", |api, handle| {
                let callback = required(api.smembers, CacheOperation::Smembers)?;
                let mut output = OwnedBufferArrayV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        &mut output,
                        &mut error,
                    )
                };
                take_output(
                    status,
                    error,
                    "smembers",
                    output,
                    take_buffer_array,
                    discard_buffer_array,
                )
            })
        })
        .await
    }

    async fn scard(&self, key: &str) -> CacheResult<u64> {
        scalar_u64(self, CacheOperation::Scard, key).await
    }

    async fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.list_push(CacheOperation::Lpush, key, values).await
    }

    async fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.list_push(CacheOperation::Rpush, key, values).await
    }

    async fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.list_pop(CacheOperation::Lpop, key, count).await
    }

    async fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.list_pop(CacheOperation::Rpop, key, count).await
    }

    async fn llen(&self, key: &str) -> CacheResult<u64> {
        scalar_u64(self, CacheOperation::Llen, key).await
    }

    async fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>> {
        let key = key.to_string();
        self.call("lrange", move |state| {
            state.with_call("lrange", |api, handle| {
                let callback = required(api.lrange, CacheOperation::Lrange)?;
                let mut output = OwnedBufferArrayV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        start,
                        stop,
                        &mut output,
                        &mut error,
                    )
                };
                take_output(
                    status,
                    error,
                    "lrange",
                    output,
                    take_buffer_array,
                    discard_buffer_array,
                )
            })
        })
        .await
    }

    async fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>> {
        let key = key.to_string();
        self.call("lindex", move |state| {
            state.with_call("lindex", |api, handle| {
                let callback = required(api.lindex, CacheOperation::Lindex)?;
                let mut output = OptionalOwnedBufferV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        index,
                        &mut output,
                        &mut error,
                    )
                };
                take_output(
                    status,
                    error,
                    "lindex",
                    output,
                    take_optional_buffer,
                    discard_optional_buffer,
                )
            })
        })
        .await
    }

    async fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()> {
        let key = key.to_string();
        self.call("lset", move |state| {
            state.with_call("lset", |api, handle| {
                let callback = required(api.lset, CacheOperation::Lset)?;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        index,
                        ByteSliceV1::from_slice(&value),
                        &mut error,
                    )
                };
                status_result(status, error, "lset")
            })
        })
        .await
    }

    async fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()> {
        let key = key.to_string();
        self.call("ltrim", move |state| {
            state.with_call("ltrim", |api, handle| {
                let callback = required(api.ltrim, CacheOperation::Ltrim)?;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        start,
                        stop,
                        &mut error,
                    )
                };
                status_result(status, error, "ltrim")
            })
        })
        .await
    }

    async fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64> {
        let key = key.to_string();
        self.call("lrem", move |state| {
            state.with_call("lrem", |api, handle| {
                let callback = required(api.lrem, CacheOperation::Lrem)?;
                let mut removed = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        count,
                        ByteSliceV1::from_slice(&value),
                        &mut removed,
                        &mut error,
                    )
                };
                status_result(status, error, "lrem")?;
                Ok(removed)
            })
        })
        .await
    }

    async fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64> {
        self.call("linsert", move |state| {
            state.with_call("linsert", |api, handle| {
                let callback = required(api.linsert, CacheOperation::Linsert)?;
                let position = match request.position {
                    ListInsertPosition::Before => LIST_BEFORE,
                    ListInsertPosition::After => LIST_AFTER,
                };
                let mut length = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(request.key.as_bytes()),
                        position,
                        ByteSliceV1::from_slice(&request.pivot),
                        ByteSliceV1::from_slice(&request.value),
                        &mut length,
                        &mut error,
                    )
                };
                status_result(status, error, "linsert")?;
                Ok(length)
            })
        })
        .await
    }

    async fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>> {
        self.call("lmove", move |state| {
            state.with_call("lmove", |api, handle| {
                let callback = required(api.lmove, CacheOperation::Lmove)?;
                let mut output = OptionalOwnedBufferV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(request.source.as_bytes()),
                        ByteSliceV1::from_slice(request.destination.as_bytes()),
                        direction(request.source_direction),
                        direction(request.destination_direction),
                        &mut output,
                        &mut error,
                    )
                };
                take_output(
                    status,
                    error,
                    "lmove",
                    output,
                    take_optional_buffer,
                    discard_optional_buffer,
                )
            })
        })
        .await
    }

    async fn execute_script(&self, request: ScriptRequest) -> CacheResult<ScriptResult> {
        let script_source = self.scripts.resolve(&request.script_id)?.to_string();
        self.call("execute_script", move |state| {
            state.with_call("execute_script", |api, handle| {
                let callback = required(api.execute_script, CacheOperation::ExecuteScript)?;
                let raw_keys = string_slices(&request.keys);
                let raw_args = byte_slices(&request.args);
                let mut output = ScriptValueV1::default();
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(request.script_id.as_bytes()),
                        ByteSliceV1::from_slice(script_source.as_bytes()),
                        raw_keys.as_ptr(),
                        raw_keys.len(),
                        raw_args.as_ptr(),
                        raw_args.len(),
                        &mut output,
                        &mut error,
                    )
                };
                let value = take_output(
                    status,
                    error,
                    "execute_script",
                    output,
                    take_script_value,
                    discard_script_value,
                )?;
                let result = ScriptResult::encode(&value)?;
                if result.payload.len() > MAX_AGGREGATE_BYTES {
                    return Err(invalid_output(format!(
                        "encoded script result byte count {} exceeds limit {}",
                        result.payload.len(),
                        MAX_AGGREGATE_BYTES
                    )));
                }
                Ok(result)
            })
        })
        .await
    }

    async fn ping(&self) -> CacheResult<()> {
        self.call("ping", DynamicState::ping).await
    }

    async fn close(&self) -> CacheResult<()> {
        self.call("close", DynamicState::close).await
    }
}

async fn scalar_u64(
    provider: &DynamicProvider,
    operation: CacheOperation,
    key: &str,
) -> CacheResult<u64> {
    let key = key.to_string();
    provider
        .call(operation.name(), move |state| {
            state.with_call(operation.name(), |api, handle| {
                let callback = match operation {
                    CacheOperation::Scard => required(api.scard, operation)?,
                    CacheOperation::Llen => required(api.llen, operation)?,
                    _ => unreachable!("scalar_u64 only accepts scalar operations"),
                };
                let mut value = 0;
                let mut error = OwnedBufferV1::default();
                let status = unsafe {
                    callback(
                        handle,
                        ByteSliceV1::from_slice(key.as_bytes()),
                        &mut value,
                        &mut error,
                    )
                };
                status_result(status, error, operation.name())?;
                Ok(value)
            })
        })
        .await
}

fn required<T: Copy>(callback: Option<T>, operation: CacheOperation) -> CacheResult<T> {
    callback.ok_or_else(|| CacheError::UnsupportedOperation(operation.name().to_string()))
}

fn set_options(options: SetOptions) -> CacheResult<SetOptionsV1> {
    let expiration_ms = match options.expiration {
        None => -1,
        Some(Expiration::After(duration)) => i64::try_from(duration.as_millis())
            .map_err(|_| CacheError::InvalidArgument("set expiration is too large".to_string()))?,
    };
    let condition = match options.condition {
        SetCondition::None => SET_CONDITION_NONE,
        SetCondition::Nx => SET_CONDITION_NX,
        SetCondition::Xx => SET_CONDITION_XX,
    };
    Ok(SetOptionsV1 {
        expiration_ms,
        condition,
        keep_ttl: u8::from(options.keep_ttl),
    })
}

fn direction(direction: ListDirection) -> u32 {
    match direction {
        ListDirection::Left => LIST_LEFT,
        ListDirection::Right => LIST_RIGHT,
    }
}

fn string_slices(values: &[String]) -> Vec<ByteSliceV1> {
    values
        .iter()
        .map(|value| ByteSliceV1::from_slice(value.as_bytes()))
        .collect()
}

fn byte_slices(values: &[Bytes]) -> Vec<ByteSliceV1> {
    values
        .iter()
        .map(|value| ByteSliceV1::from_slice(value))
        .collect()
}

#[derive(Default)]
struct DecodeBudget {
    aggregate_bytes: usize,
    script_nodes: usize,
}

impl DecodeBudget {
    fn charge_bytes(&mut self, bytes: usize, label: &str) -> CacheResult<()> {
        self.aggregate_bytes = self
            .aggregate_bytes
            .checked_add(bytes)
            .ok_or_else(|| invalid_output(format!("{label} aggregate byte count overflowed")))?;
        if self.aggregate_bytes > MAX_AGGREGATE_BYTES {
            return Err(invalid_output(format!(
                "{label} aggregate byte count {} exceeds limit {}",
                self.aggregate_bytes, MAX_AGGREGATE_BYTES
            )));
        }
        Ok(())
    }

    fn charge_script_node(&mut self) -> CacheResult<()> {
        self.script_nodes = self
            .script_nodes
            .checked_add(1)
            .ok_or_else(|| invalid_output("script node count overflowed"))?;
        if self.script_nodes > MAX_SCRIPT_NODES {
            return Err(invalid_output(format!(
                "script node count {} exceeds limit {}",
                self.script_nodes, MAX_SCRIPT_NODES
            )));
        }
        Ok(())
    }
}

#[derive(Debug)]
struct RegisteredAllocation {
    data: *mut u8,
    layout: Layout,
}

impl Drop for RegisteredAllocation {
    fn drop(&mut self) {
        unsafe { dealloc(self.data, self.layout) };
    }
}

struct PendingBuffers {
    items: std::vec::IntoIter<OwnedBufferV1>,
}

impl Drop for PendingBuffers {
    fn drop(&mut self) {
        for item in self.items.by_ref() {
            discard_buffer(item);
        }
    }
}

struct PendingOptionalBuffers {
    items: std::vec::IntoIter<OptionalOwnedBufferV1>,
}

impl Drop for PendingOptionalBuffers {
    fn drop(&mut self) {
        for item in self.items.by_ref() {
            discard_buffer(item.value);
        }
    }
}

struct PendingScriptValues {
    items: std::vec::IntoIter<ScriptValueV1>,
}

impl Drop for PendingScriptValues {
    fn drop(&mut self) {
        for item in self.items.by_ref() {
            discard_script_value(item);
        }
    }
}

fn take_output<T, O>(
    status: i32,
    error: OwnedBufferV1,
    operation: &str,
    output: O,
    take: impl FnOnce(O) -> CacheResult<T>,
    discard: impl FnOnce(O),
) -> CacheResult<T> {
    match status_result(status, error, operation) {
        Ok(()) => take(output),
        Err(error) => {
            discard(output);
            Err(error)
        }
    }
}

fn status_result(status: i32, error: OwnedBufferV1, operation: &str) -> CacheResult<()> {
    let message = take_buffer_with_limit(error, MAX_ERROR_BYTES, "error buffer")?;
    if status == STATUS_OK {
        if message.is_empty() {
            return Ok(());
        }
        return Err(invalid_output(format!(
            "dynamic provider {operation} returned an error payload with success status"
        )));
    }
    let message = String::from_utf8_lossy(&message);
    let details = if message.is_empty() {
        format!("dynamic provider {operation} failed with status {status}")
    } else {
        format!("dynamic provider {operation}: {message}")
    };
    Err(match status {
        STATUS_TIMEOUT => CacheError::Timeout(details),
        STATUS_UNAVAILABLE => CacheError::Unavailable(details),
        STATUS_AUTHENTICATION => CacheError::Authentication(details),
        STATUS_PERMISSION_DENIED => CacheError::PermissionDenied(details),
        STATUS_INVALID_ARGUMENT => CacheError::InvalidArgument(details),
        STATUS_INVALID_DATA => CacheError::InvalidData(details),
        STATUS_CROSS_SLOT => CacheError::CrossSlot(details),
        STATUS_READ_ONLY => CacheError::ReadOnly(details),
        STATUS_UNSUPPORTED_OPERATION => CacheError::UnsupportedOperation(details),
        _ => CacheError::Internal(details),
    })
}

fn take_bool(value: u8, operation: &str) -> CacheResult<bool> {
    match value {
        0 => Ok(false),
        1 => Ok(true),
        other => Err(CacheError::InvalidData(format!(
            "dynamic provider {operation} returned invalid boolean {other}"
        ))),
    }
}

fn take_optional_buffer(value: OptionalOwnedBufferV1) -> CacheResult<Option<Bytes>> {
    match value.present {
        0 if is_empty_buffer(&value.value) => Ok(None),
        0 => {
            discard_buffer(value.value);
            Err(invalid_output(
                "absent optional buffer returned a non-empty value",
            ))
        }
        1 => take_buffer(value.value).map(Some),
        other => {
            discard_buffer(value.value);
            Err(invalid_output(format!(
                "dynamic provider returned invalid optional flag {other}"
            )))
        }
    }
}

fn take_buffer(buffer: OwnedBufferV1) -> CacheResult<Bytes> {
    take_buffer_with_limit(buffer, MAX_BUFFER_BYTES, "buffer")
}

fn take_buffer_with_limit(
    buffer: OwnedBufferV1,
    max_bytes: usize,
    label: &str,
) -> CacheResult<Bytes> {
    let mut budget = DecodeBudget::default();
    take_buffer_with_budget(buffer, max_bytes, label, &mut budget)
}

fn take_buffer_with_budget(
    buffer: OwnedBufferV1,
    max_bytes: usize,
    label: &str,
    budget: &mut DecodeBudget,
) -> CacheResult<Bytes> {
    if buffer.len == 0 {
        if buffer.data.is_null() {
            return Ok(Bytes::new());
        }
        release_registered(buffer.data);
        return Err(invalid_output(format!(
            "dynamic provider returned a non-null empty {label}"
        )));
    }
    if buffer.data.is_null() {
        return Err(invalid_output(format!(
            "dynamic provider returned a null {label}"
        )));
    }
    let allocation = claim_allocation::<u8>(buffer.data, buffer.len, max_bytes, label, budget)?;
    let value = Bytes::copy_from_slice(unsafe { slice::from_raw_parts(buffer.data, buffer.len) });
    drop(allocation);
    Ok(value)
}

fn take_buffer_array(array: OwnedBufferArrayV1) -> CacheResult<Vec<Bytes>> {
    let mut budget = DecodeBudget::default();
    take_buffer_array_with_budget(array, &mut budget)
}

fn take_buffer_array_with_budget(
    array: OwnedBufferArrayV1,
    budget: &mut DecodeBudget,
) -> CacheResult<Vec<Bytes>> {
    if array.len == 0 {
        if array.items.is_null() {
            return Ok(Vec::new());
        }
        release_registered(array.items.cast::<u8>());
        return Err(invalid_output(
            "dynamic provider returned a non-null empty buffer array",
        ));
    }
    if array.items.is_null() {
        return Err(invalid_output(
            "dynamic provider returned a null buffer array",
        ));
    }
    let items = take_raw_array(array.items, array.len, "buffer array", budget)?;
    let mut pending = PendingBuffers {
        items: items.into_iter(),
    };
    let mut values = Vec::with_capacity(array.len);
    while let Some(item) = pending.items.next() {
        values.push(take_buffer_with_budget(
            item,
            MAX_BUFFER_BYTES,
            "buffer array item",
            budget,
        )?);
    }
    Ok(values)
}

fn take_optional_buffer_array(
    array: OptionalOwnedBufferArrayV1,
) -> CacheResult<Vec<Option<Bytes>>> {
    let mut budget = DecodeBudget::default();
    if array.len == 0 {
        if array.items.is_null() {
            return Ok(Vec::new());
        }
        release_registered(array.items.cast::<u8>());
        return Err(invalid_output(
            "dynamic provider returned a non-null empty optional buffer array",
        ));
    }
    if array.items.is_null() {
        return Err(invalid_output(
            "dynamic provider returned a null optional buffer array",
        ));
    }
    let items = take_raw_array(array.items, array.len, "optional buffer array", &mut budget)?;
    let mut pending = PendingOptionalBuffers {
        items: items.into_iter(),
    };
    let mut values = Vec::with_capacity(array.len);
    while let Some(item) = pending.items.next() {
        values.push(take_optional_buffer_with_budget(item, &mut budget)?);
    }
    Ok(values)
}

fn take_script_value(value: ScriptValueV1) -> CacheResult<ScriptValue> {
    let mut budget = DecodeBudget::default();
    take_script_value_with_budget(value, 0, &mut budget)
}

fn take_script_value_with_budget(
    value: ScriptValueV1,
    depth: usize,
    budget: &mut DecodeBudget,
) -> CacheResult<ScriptValue> {
    if depth > MAX_SCRIPT_DEPTH {
        discard_script_value(value);
        return Err(invalid_output(format!(
            "script result depth {depth} exceeds limit {MAX_SCRIPT_DEPTH}"
        )));
    }
    if let Err(error) = budget.charge_script_node() {
        discard_script_value(value);
        return Err(error);
    }
    let ScriptValueV1 {
        bytes,
        items,
        items_len,
        integer,
        kind,
        boolean,
    } = value;
    match kind {
        SCRIPT_NULL => {
            ensure_unused_script_storage(bytes, items, items_len, "null")?;
            Ok(ScriptValue::Null)
        }
        SCRIPT_INTEGER => {
            ensure_unused_script_storage(bytes, items, items_len, "integer")?;
            Ok(ScriptValue::Integer(integer))
        }
        SCRIPT_BYTES => {
            ensure_empty_script_items(items, items_len, "bytes")?;
            take_buffer_with_budget(bytes, MAX_BUFFER_BYTES, "script bytes", budget)
                .map(|value| ScriptValue::Bytes(value.to_vec()))
        }
        SCRIPT_BOOLEAN => {
            ensure_unused_script_storage(bytes, items, items_len, "boolean")?;
            take_bool(boolean, "execute_script").map(ScriptValue::Boolean)
        }
        SCRIPT_ARRAY => {
            ensure_empty_script_buffer(bytes, "array")?;
            if items_len == 0 {
                if items.is_null() {
                    return Ok(ScriptValue::Array(Vec::new()));
                }
                release_registered(items.cast::<u8>());
                return Err(invalid_output(
                    "dynamic provider returned a non-null empty script array",
                ));
            }
            if items.is_null() {
                return Err(invalid_output(
                    "dynamic provider returned a null script array",
                ));
            }
            let raw_items = take_raw_array(items, items_len, "script array", budget)?;
            let mut pending = PendingScriptValues {
                items: raw_items.into_iter(),
            };
            let mut values = Vec::with_capacity(items_len);
            while let Some(item) = pending.items.next() {
                values.push(take_script_value_with_budget(item, depth + 1, budget)?);
            }
            Ok(ScriptValue::Array(values))
        }
        other => {
            discard_buffer(bytes);
            discard_script_items(items, items_len);
            Err(invalid_output(format!(
                "dynamic provider returned invalid script value kind {other}"
            )))
        }
    }
}

fn take_optional_buffer_with_budget(
    value: OptionalOwnedBufferV1,
    budget: &mut DecodeBudget,
) -> CacheResult<Option<Bytes>> {
    match value.present {
        0 if is_empty_buffer(&value.value) => Ok(None),
        0 => {
            discard_buffer(value.value);
            Err(invalid_output(
                "absent optional buffer returned a non-empty value",
            ))
        }
        1 => take_buffer_with_budget(value.value, MAX_BUFFER_BYTES, "optional buffer", budget)
            .map(Some),
        other => {
            discard_buffer(value.value);
            Err(invalid_output(format!(
                "dynamic provider returned invalid optional flag {other}"
            )))
        }
    }
}

fn take_raw_array<T>(
    items: *mut T,
    len: usize,
    label: &str,
    budget: &mut DecodeBudget,
) -> CacheResult<Vec<T>> {
    if len > MAX_ARRAY_ITEMS {
        release_registered(items.cast::<u8>());
        return Err(invalid_output(format!(
            "{label} item count {len} exceeds limit {MAX_ARRAY_ITEMS}"
        )));
    }
    let allocation = claim_allocation::<T>(items, len, MAX_HOST_ALLOCATION_BYTES, label, budget)?;
    let mut values = Vec::with_capacity(len);
    for index in 0..len {
        values.push(unsafe { ptr::read(items.add(index)) });
    }
    drop(allocation);
    Ok(values)
}

fn claim_allocation<T>(
    data: *mut T,
    len: usize,
    max_bytes: usize,
    label: &str,
    budget: &mut DecodeBudget,
) -> CacheResult<RegisteredAllocation> {
    let layout = Layout::array::<T>(len)
        .map_err(|_| invalid_output(format!("{label} allocation size overflowed")))?;
    if layout.size() > isize::MAX as usize {
        release_registered(data.cast::<u8>());
        return Err(invalid_output(format!(
            "{label} allocation exceeds isize::MAX"
        )));
    }
    if layout.size() > max_bytes {
        release_registered(data.cast::<u8>());
        return Err(invalid_output(format!(
            "{label} byte count {} exceeds limit {max_bytes}",
            layout.size()
        )));
    }
    if (data as usize) % layout.align() != 0 {
        release_registered(data.cast::<u8>());
        return Err(invalid_output(format!(
            "{label} pointer is not aligned to {} bytes",
            layout.align()
        )));
    }
    if let Err(error) = budget.charge_bytes(layout.size(), label) {
        release_registered(data.cast::<u8>());
        return Err(error);
    }
    let actual = allocation_registry()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .remove(&(data as usize))
        .ok_or_else(|| invalid_output(format!("{label} was not allocated by host->alloc")))?;
    if actual != layout {
        unsafe { dealloc(data.cast::<u8>(), actual) };
        return Err(invalid_output(format!(
            "{label} allocation layout does not match returned length/alignment"
        )));
    }
    Ok(RegisteredAllocation {
        data: data.cast::<u8>(),
        layout,
    })
}

fn allocation_registry() -> &'static Mutex<HashMap<usize, Layout>> {
    static ALLOCATIONS: OnceLock<Mutex<HashMap<usize, Layout>>> = OnceLock::new();
    ALLOCATIONS.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(test)]
fn host_allocation_is_registered(data: *mut u8) -> bool {
    allocation_registry()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .contains_key(&(data as usize))
}

fn release_registered(data: *mut u8) {
    if data.is_null() {
        return;
    }
    let layout = allocation_registry()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .remove(&(data as usize));
    if let Some(layout) = layout {
        unsafe { dealloc(data, layout) };
    }
}

fn discard_buffer(buffer: OwnedBufferV1) {
    release_registered(buffer.data);
}

fn discard_optional_buffer(buffer: OptionalOwnedBufferV1) {
    discard_buffer(buffer.value);
}

fn discard_buffer_array(array: OwnedBufferArrayV1) {
    if array.items.is_null() || array.len == 0 || array.len > MAX_ARRAY_ITEMS {
        release_registered(array.items.cast::<u8>());
        return;
    }
    let mut budget = DecodeBudget::default();
    match take_raw_array(
        array.items,
        array.len,
        "discarded buffer array",
        &mut budget,
    ) {
        Ok(items) => {
            for item in items {
                discard_buffer(item);
            }
        }
        Err(_) => release_registered(array.items.cast::<u8>()),
    }
}

fn discard_optional_buffer_array(array: OptionalOwnedBufferArrayV1) {
    if array.items.is_null() || array.len == 0 || array.len > MAX_ARRAY_ITEMS {
        release_registered(array.items.cast::<u8>());
        return;
    }
    let mut budget = DecodeBudget::default();
    match take_raw_array(
        array.items,
        array.len,
        "discarded optional buffer array",
        &mut budget,
    ) {
        Ok(items) => {
            for item in items {
                discard_optional_buffer(item);
            }
        }
        Err(_) => release_registered(array.items.cast::<u8>()),
    }
}

fn discard_script_items(items: *mut ScriptValueV1, len: usize) {
    if items.is_null() {
        return;
    }
    if len == 0 || len > MAX_ARRAY_ITEMS {
        release_registered(items.cast::<u8>());
        return;
    }
    let mut budget = DecodeBudget::default();
    match take_raw_array(items, len, "discarded script array", &mut budget) {
        Ok(values) => {
            for value in values {
                discard_script_value(value);
            }
        }
        Err(_) => release_registered(items.cast::<u8>()),
    }
}

fn discard_script_value(value: ScriptValueV1) {
    let mut pending = vec![value];
    let mut visited = 0usize;
    while let Some(value) = pending.pop() {
        visited += 1;
        let ScriptValueV1 {
            bytes,
            items,
            items_len,
            ..
        } = value;
        discard_buffer(bytes);
        if items.is_null() {
            continue;
        }
        if visited > MAX_SCRIPT_NODES || items_len == 0 || items_len > MAX_ARRAY_ITEMS {
            release_registered(items.cast::<u8>());
            continue;
        }
        let mut budget = DecodeBudget::default();
        match take_raw_array(items, items_len, "discarded script array", &mut budget) {
            Ok(mut values) => pending.append(&mut values),
            Err(_) => release_registered(items.cast::<u8>()),
        }
    }
}

fn ensure_unused_script_storage(
    bytes: OwnedBufferV1,
    items: *mut ScriptValueV1,
    items_len: usize,
    kind: &str,
) -> CacheResult<()> {
    ensure_empty_script_buffer(bytes, kind)?;
    ensure_empty_script_items(items, items_len, kind)
}

fn ensure_empty_script_buffer(buffer: OwnedBufferV1, kind: &str) -> CacheResult<()> {
    if is_empty_buffer(&buffer) {
        return Ok(());
    }
    discard_buffer(buffer);
    Err(invalid_output(format!(
        "script {kind} value returned an unexpected byte buffer"
    )))
}

fn ensure_empty_script_items(
    items: *mut ScriptValueV1,
    items_len: usize,
    kind: &str,
) -> CacheResult<()> {
    if items.is_null() && items_len == 0 {
        return Ok(());
    }
    discard_script_items(items, items_len);
    Err(invalid_output(format!(
        "script {kind} value returned unexpected child items"
    )))
}

fn is_empty_buffer(buffer: &OwnedBufferV1) -> bool {
    buffer.data.is_null() && buffer.len == 0
}

fn invalid_output(message: impl Into<String>) -> CacheError {
    CacheError::InvalidData(message.into())
}

fn close_handle(api: ProviderApiV1, handle: *mut c_void) {
    if handle.is_null() {
        return;
    }
    let mut error = OwnedBufferV1::default();
    if let Some(close) = api.close {
        unsafe {
            close(handle, &mut error);
        }
        let _ = take_buffer(error);
    }
}

unsafe extern "C" fn host_alloc(size: usize, alignment: usize) -> *mut u8 {
    if size == 0 || size > MAX_HOST_ALLOCATION_BYTES {
        return ptr::null_mut();
    }
    match Layout::from_size_align(size, alignment) {
        Ok(layout) => {
            let data = unsafe { alloc(layout) };
            if !data.is_null() {
                allocation_registry()
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .insert(data as usize, layout);
            }
            data
        }
        Err(_) => ptr::null_mut(),
    }
}

unsafe extern "C" fn host_dealloc(data: *mut u8, size: usize, alignment: usize) {
    if data.is_null() || size == 0 {
        return;
    }
    if let Ok(expected) = Layout::from_size_align(size, alignment) {
        let mut allocations = allocation_registry()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if allocations.get(&(data as usize)) == Some(&expected) {
            allocations.remove(&(data as usize));
            drop(allocations);
            unsafe { dealloc(data, expected) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn script_result_rejects_excessive_nesting() {
        let mut value = ScriptValueV1 {
            kind: SCRIPT_NULL,
            ..ScriptValueV1::default()
        };
        for _ in 0..80 {
            let layout = Layout::array::<ScriptValueV1>(1).unwrap();
            let items =
                unsafe { host_alloc(layout.size(), layout.align()) }.cast::<ScriptValueV1>();
            assert!(!items.is_null());
            unsafe { ptr::write(items, value) };
            value = ScriptValueV1 {
                items,
                items_len: 1,
                kind: SCRIPT_ARRAY,
                ..ScriptValueV1::default()
            };
        }

        let error = take_script_value(value).unwrap_err();

        assert!(matches!(error, CacheError::InvalidData(message) if message.contains("depth")));
    }

    #[test]
    fn buffer_array_rejects_excessive_item_count() {
        const ITEM_COUNT: usize = 100_001;
        let layout = Layout::array::<OwnedBufferV1>(ITEM_COUNT).unwrap();
        let items = unsafe { host_alloc(layout.size(), layout.align()) }.cast::<OwnedBufferV1>();
        assert!(!items.is_null());
        for index in 0..ITEM_COUNT {
            unsafe { ptr::write(items.add(index), OwnedBufferV1::default()) };
        }

        let error = take_buffer_array(OwnedBufferArrayV1 {
            items,
            len: ITEM_COUNT,
        })
        .unwrap_err();

        assert!(
            matches!(error, CacheError::InvalidData(message) if message.contains("item count"))
        );
    }

    #[test]
    fn optional_array_failure_releases_all_host_allocations() {
        let first_data = unsafe { host_alloc(1, align_of::<u8>()) };
        let second_data = unsafe { host_alloc(1, align_of::<u8>()) };
        assert!(!first_data.is_null());
        assert!(!second_data.is_null());
        unsafe {
            *first_data = b'a';
            *second_data = b'b';
        }
        let layout = Layout::array::<OptionalOwnedBufferV1>(2).unwrap();
        let items =
            unsafe { host_alloc(layout.size(), layout.align()) }.cast::<OptionalOwnedBufferV1>();
        unsafe {
            ptr::write(
                items,
                OptionalOwnedBufferV1 {
                    value: OwnedBufferV1 {
                        data: first_data,
                        len: 1,
                    },
                    present: 2,
                },
            );
            ptr::write(
                items.add(1),
                OptionalOwnedBufferV1 {
                    value: OwnedBufferV1 {
                        data: second_data,
                        len: 1,
                    },
                    present: 1,
                },
            );
        }

        let result = take_optional_buffer_array(OptionalOwnedBufferArrayV1 { items, len: 2 });

        assert!(matches!(result, Err(CacheError::InvalidData(_))));
        assert!(!host_allocation_is_registered(first_data));
        assert!(!host_allocation_is_registered(second_data));
        assert!(!host_allocation_is_registered(items.cast::<u8>()));
    }

    #[test]
    fn checked_reader_rejects_overflow_and_misalignment() {
        let overflow = claim_allocation::<ScriptValueV1>(
            std::ptr::NonNull::dangling().as_ptr(),
            usize::MAX,
            MAX_HOST_ALLOCATION_BYTES,
            "overflow array",
            &mut DecodeBudget::default(),
        )
        .unwrap_err();
        assert!(
            matches!(overflow, CacheError::InvalidData(message) if message.contains("overflow"))
        );

        let misaligned = claim_allocation::<ScriptValueV1>(
            1usize as *mut ScriptValueV1,
            1,
            MAX_HOST_ALLOCATION_BYTES,
            "misaligned array",
            &mut DecodeBudget::default(),
        )
        .unwrap_err();
        assert!(
            matches!(misaligned, CacheError::InvalidData(message) if message.contains("aligned"))
        );
    }
}
