use std::ffi::c_void;

pub(super) const ABI_VERSION_V1: u32 = 1;
pub(super) const ENTRY_SYMBOL_V1: &[u8] = b"openviking_cache_provider_v1\0";

pub(super) const STATUS_OK: i32 = 0;
pub(super) const STATUS_TIMEOUT: i32 = 2;
pub(super) const STATUS_UNAVAILABLE: i32 = 3;
pub(super) const STATUS_AUTHENTICATION: i32 = 4;
pub(super) const STATUS_PERMISSION_DENIED: i32 = 5;
pub(super) const STATUS_INVALID_ARGUMENT: i32 = 6;
pub(super) const STATUS_INVALID_DATA: i32 = 7;
pub(super) const STATUS_CROSS_SLOT: i32 = 8;
pub(super) const STATUS_READ_ONLY: i32 = 9;
pub(super) const STATUS_UNSUPPORTED_OPERATION: i32 = 10;
pub(super) const SET_CONDITION_NONE: u32 = 0;
pub(super) const SET_CONDITION_NX: u32 = 1;
pub(super) const SET_CONDITION_XX: u32 = 2;
pub(super) const SET_APPLIED: u8 = 0;
pub(super) const SET_CONDITION_NOT_MET: u8 = 1;
pub(super) const LIST_BEFORE: u32 = 0;
pub(super) const LIST_AFTER: u32 = 1;
pub(super) const LIST_LEFT: u32 = 0;
pub(super) const LIST_RIGHT: u32 = 1;
pub(super) const SCRIPT_NULL: u32 = 0;
pub(super) const SCRIPT_INTEGER: u32 = 1;
pub(super) const SCRIPT_BYTES: u32 = 2;
pub(super) const SCRIPT_ARRAY: u32 = 3;
pub(super) const SCRIPT_BOOLEAN: u32 = 4;

#[repr(C)]
#[derive(Clone, Copy)]
pub(super) struct ByteSliceV1 {
    pub(super) data: *const u8,
    pub(super) len: usize,
}

impl ByteSliceV1 {
    pub(super) fn from_slice(value: &[u8]) -> Self {
        Self {
            data: value.as_ptr(),
            len: value.len(),
        }
    }
}

#[repr(C)]
#[derive(Default)]
pub(super) struct OwnedBufferV1 {
    pub(super) data: *mut u8,
    pub(super) len: usize,
}

#[repr(C)]
#[derive(Default)]
pub(super) struct OptionalOwnedBufferV1 {
    pub(super) value: OwnedBufferV1,
    pub(super) present: u8,
}

#[repr(C)]
#[derive(Default)]
pub(super) struct OwnedBufferArrayV1 {
    pub(super) items: *mut OwnedBufferV1,
    pub(super) len: usize,
}

#[repr(C)]
#[derive(Default)]
pub(super) struct OptionalOwnedBufferArrayV1 {
    pub(super) items: *mut OptionalOwnedBufferV1,
    pub(super) len: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(super) struct KeyValueV1 {
    pub(super) key: ByteSliceV1,
    pub(super) value: ByteSliceV1,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(super) struct SetOptionsV1 {
    pub(super) expiration_ms: i64,
    pub(super) condition: u32,
    pub(super) keep_ttl: u8,
}

#[repr(C)]
#[derive(Default)]
pub(super) struct ScriptValueV1 {
    pub(super) bytes: OwnedBufferV1,
    pub(super) items: *mut ScriptValueV1,
    pub(super) items_len: usize,
    pub(super) integer: i64,
    pub(super) kind: u32,
    pub(super) boolean: u8,
}

pub(super) type HostAllocV1 = unsafe extern "C" fn(size: usize, alignment: usize) -> *mut u8;
pub(super) type HostDeallocV1 = unsafe extern "C" fn(data: *mut u8, size: usize, alignment: usize);

#[repr(C)]
pub(super) struct HostApiV1 {
    pub(super) abi_version: u32,
    pub(super) struct_size: usize,
    pub(super) alloc: HostAllocV1,
    pub(super) dealloc: HostDeallocV1,
}

pub(super) type ProviderCreateV1 = unsafe extern "C" fn(
    host: *const HostApiV1,
    params_json: ByteSliceV1,
    provider: *mut *mut c_void,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderPingV1 =
    unsafe extern "C" fn(provider: *mut c_void, error: *mut OwnedBufferV1) -> i32;
pub(super) type ProviderCloseV1 = ProviderPingV1;
pub(super) type ProviderGetV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    output: *mut OptionalOwnedBufferV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderSetV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    value: ByteSliceV1,
    options: SetOptionsV1,
    result: *mut u8,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderDelV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    keys: *const ByteSliceV1,
    keys_len: usize,
    removed: *mut u64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderMgetV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    keys: *const ByteSliceV1,
    keys_len: usize,
    output: *mut OptionalOwnedBufferArrayV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderMsetV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    entries: *const KeyValueV1,
    entries_len: usize,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderIncrbyV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    delta: i64,
    value: *mut i64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderSismemberV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    member: ByteSliceV1,
    present: *mut u8,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderSmembersV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    output: *mut OwnedBufferArrayV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderScardV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    count: *mut u64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderListPushV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    values: *const ByteSliceV1,
    values_len: usize,
    length: *mut u64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderListPopV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    has_count: u8,
    count: u64,
    output: *mut OwnedBufferArrayV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLlenV1 = ProviderScardV1;
pub(super) type ProviderLrangeV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    start: i64,
    stop: i64,
    output: *mut OwnedBufferArrayV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLindexV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    index: i64,
    output: *mut OptionalOwnedBufferV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLsetV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    index: i64,
    value: ByteSliceV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLtrimV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    start: i64,
    stop: i64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLremV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    count: i64,
    value: ByteSliceV1,
    removed: *mut u64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLinsertV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    key: ByteSliceV1,
    position: u32,
    pivot: ByteSliceV1,
    value: ByteSliceV1,
    length: *mut i64,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderLmoveV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    source: ByteSliceV1,
    destination: ByteSliceV1,
    source_direction: u32,
    destination_direction: u32,
    output: *mut OptionalOwnedBufferV1,
    error: *mut OwnedBufferV1,
) -> i32;
pub(super) type ProviderExecuteScriptV1 = unsafe extern "C" fn(
    provider: *mut c_void,
    script_id: ByteSliceV1,
    script_source: ByteSliceV1,
    keys: *const ByteSliceV1,
    keys_len: usize,
    args: *const ByteSliceV1,
    args_len: usize,
    output: *mut ScriptValueV1,
    error: *mut OwnedBufferV1,
) -> i32;

#[repr(C)]
#[derive(Clone, Copy)]
pub(super) struct ProviderApiV1 {
    pub(super) abi_version: u32,
    pub(super) struct_size: usize,
    pub(super) create: Option<ProviderCreateV1>,
    pub(super) ping: Option<ProviderPingV1>,
    pub(super) close: Option<ProviderCloseV1>,
    pub(super) get: Option<ProviderGetV1>,
    pub(super) set: Option<ProviderSetV1>,
    pub(super) del: Option<ProviderDelV1>,
    pub(super) mget: Option<ProviderMgetV1>,
    pub(super) mset: Option<ProviderMsetV1>,
    pub(super) incrby: Option<ProviderIncrbyV1>,
    pub(super) sismember: Option<ProviderSismemberV1>,
    pub(super) smembers: Option<ProviderSmembersV1>,
    pub(super) scard: Option<ProviderScardV1>,
    pub(super) lpush: Option<ProviderListPushV1>,
    pub(super) rpush: Option<ProviderListPushV1>,
    pub(super) lpop: Option<ProviderListPopV1>,
    pub(super) rpop: Option<ProviderListPopV1>,
    pub(super) llen: Option<ProviderLlenV1>,
    pub(super) lrange: Option<ProviderLrangeV1>,
    pub(super) lindex: Option<ProviderLindexV1>,
    pub(super) lset: Option<ProviderLsetV1>,
    pub(super) ltrim: Option<ProviderLtrimV1>,
    pub(super) lrem: Option<ProviderLremV1>,
    pub(super) linsert: Option<ProviderLinsertV1>,
    pub(super) lmove: Option<ProviderLmoveV1>,
    pub(super) execute_script: Option<ProviderExecuteScriptV1>,
}

pub(super) type ProviderEntryV1 = unsafe extern "C" fn() -> *const ProviderApiV1;
