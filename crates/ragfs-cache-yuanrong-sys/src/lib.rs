//! Unsafe bindings for OpenViking's stable Yuanrong C ABI bridge.

#[cfg(feature = "native")]
use std::ffi::{c_char, c_void};
use std::ffi::{c_int, c_uchar};

/// Operation completed successfully.
pub const YR_OK: c_int = 0;
/// Requested key does not exist.
pub const YR_NOT_FOUND: c_int = 1;
/// Yuanrong rejected an argument.
pub const YR_INVALID_ARGUMENT: c_int = 2;
/// Worker or client connection is unavailable.
pub const YR_UNAVAILABLE: c_int = 3;
/// Yuanrong operation timed out.
pub const YR_TIMEOUT: c_int = 4;
/// Yuanrong returned another internal error.
pub const YR_INTERNAL: c_int = 5;

/// Opaque Yuanrong client owned by the C++ bridge.
#[repr(C)]
pub struct YrClientHandle {
    _private: [u8; 0],
}

/// Buffer allocated by the C++ bridge.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct YrBuffer {
    /// Buffer address, or null when the corresponding key is missing.
    pub data: *mut c_uchar,
    /// Buffer length.
    pub len: usize,
    /// One when the key exists, zero when it is missing.
    pub found: c_uchar,
}

#[cfg(feature = "native")]
unsafe extern "C" {
    /// Create and initialize a Yuanrong `DsClient`.
    pub fn yr_client_create(
        host: *const c_char,
        port: u16,
        connect_timeout_ms: i32,
        request_timeout_ms: i32,
        out: *mut *mut YrClientHandle,
    ) -> c_int;
    /// Check the local worker connection.
    pub fn yr_client_health_check(client: *mut YrClientHandle) -> c_int;
    /// Read one complete KV value.
    pub fn yr_client_get(
        client: *mut YrClientHandle,
        key: *const c_uchar,
        key_len: usize,
        data: *mut *mut c_uchar,
        size: *mut usize,
    ) -> c_int;
    /// Store one complete KV value.
    pub fn yr_client_set(
        client: *mut YrClientHandle,
        key: *const c_uchar,
        key_len: usize,
        data: *const c_uchar,
        size: usize,
    ) -> c_int;
    /// Delete one KV value.
    pub fn yr_client_delete(
        client: *mut YrClientHandle,
        key: *const c_uchar,
        key_len: usize,
    ) -> c_int;
    /// Check whether one KV value exists.
    pub fn yr_client_exists(
        client: *mut YrClientHandle,
        key: *const c_uchar,
        key_len: usize,
        exists: *mut c_uchar,
    ) -> c_int;
    /// Read multiple values, preserving key order.
    pub fn yr_client_mget(
        client: *mut YrClientHandle,
        keys: *const *const c_uchar,
        key_lens: *const usize,
        count: usize,
        values: *mut *mut YrBuffer,
    ) -> c_int;
    /// Store multiple values.
    pub fn yr_client_mset(
        client: *mut YrClientHandle,
        keys: *const *const c_uchar,
        key_lens: *const usize,
        values: *const *const c_uchar,
        value_lens: *const usize,
        count: usize,
    ) -> c_int;
    /// Delete multiple values.
    pub fn yr_client_mdelete(
        client: *mut YrClientHandle,
        keys: *const *const c_uchar,
        key_lens: *const usize,
        count: usize,
    ) -> c_int;
    /// Shut down the SDK client.
    pub fn yr_client_shutdown(client: *mut YrClientHandle) -> c_int;
    /// Destroy the bridge handle.
    pub fn yr_client_destroy(client: *mut YrClientHandle);
    /// Free a single value returned by `yr_client_get`.
    pub fn yr_buffer_free(data: *mut c_void);
    /// Free values returned by `yr_client_mget`.
    pub fn yr_buffers_free(values: *mut YrBuffer, count: usize);
    /// Return diagnostics for the last bridge call on the current thread.
    pub fn yr_last_error(client: *mut YrClientHandle) -> *const c_char;
}
