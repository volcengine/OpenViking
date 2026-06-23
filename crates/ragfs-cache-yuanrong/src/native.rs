use crate::{YuanrongConfig, YuanrongKvStore, YuanrongProvider, YuanrongStoreError};
use ragfs::cache::{CacheError, CacheResult};
use ragfs_cache_yuanrong_sys as sys;
use sha2::{Digest, Sha256};
use std::ffi::{CStr, CString};
use std::ptr::{self, NonNull};
use std::slice;
use std::sync::Arc;
use std::time::Duration;

struct NativeYuanrongStore {
    handle: NonNull<sys::YrClientHandle>,
}

unsafe impl Send for NativeYuanrongStore {}
unsafe impl Sync for NativeYuanrongStore {}

impl NativeYuanrongStore {
    const VALUE_FORMAT_V1: u8 = 1;

    fn connect(config: &YuanrongConfig) -> Result<Self, YuanrongStoreError> {
        let host = CString::new(config.host.as_str()).map_err(|_| {
            YuanrongStoreError::InvalidArgument("host contains an embedded NUL byte".into())
        })?;
        let mut handle = ptr::null_mut();
        let code = unsafe {
            sys::yr_client_create(
                host.as_ptr(),
                config.port,
                config.connect_timeout_ms as i32,
                config.request_timeout_ms as i32,
                &mut handle,
            )
        };
        let handle = NonNull::new(handle);
        if code != sys::YR_OK {
            return Err(map_native_error(code, handle));
        }
        Ok(Self {
            handle: handle.ok_or_else(|| {
                YuanrongStoreError::Internal(
                    "Yuanrong create succeeded without returning a handle".into(),
                )
            })?,
        })
    }

    fn call(&self, code: i32) -> Result<(), YuanrongStoreError> {
        if code == sys::YR_OK {
            Ok(())
        } else {
            Err(map_native_error(code, Some(self.handle)))
        }
    }

    fn native_key(key: &str) -> Result<String, YuanrongStoreError> {
        if key.is_empty() {
            return Err(YuanrongStoreError::InvalidArgument(
                "Yuanrong cache key must not be empty".into(),
            ));
        }
        let digest = Sha256::digest(key.as_bytes());
        let mut encoded = String::with_capacity(67);
        encoded.push_str("ov_");
        for byte in digest {
            use std::fmt::Write;
            write!(&mut encoded, "{byte:02x}").expect("writing to String cannot fail");
        }
        Ok(encoded)
    }

    fn native_keys(keys: &[String]) -> Result<Vec<String>, YuanrongStoreError> {
        keys.iter().map(|key| Self::native_key(key)).collect()
    }

    fn key_parts(keys: &[String]) -> (Vec<*const u8>, Vec<usize>) {
        (
            keys.iter().map(|key| key.as_ptr()).collect(),
            keys.iter().map(String::len).collect(),
        )
    }

    fn encode_value(value: &[u8]) -> Vec<u8> {
        let mut encoded = Vec::with_capacity(value.len() + 1);
        encoded.push(Self::VALUE_FORMAT_V1);
        encoded.extend_from_slice(value);
        encoded
    }

    fn decode_value(value: &[u8]) -> Result<Vec<u8>, YuanrongStoreError> {
        match value.split_first() {
            Some((&Self::VALUE_FORMAT_V1, data)) => Ok(data.to_vec()),
            _ => Err(YuanrongStoreError::Internal(
                "Yuanrong cache value has an unsupported format".into(),
            )),
        }
    }
}

impl Drop for NativeYuanrongStore {
    fn drop(&mut self) {
        unsafe {
            sys::yr_client_destroy(self.handle.as_ptr());
        }
    }
}

impl YuanrongKvStore for NativeYuanrongStore {
    fn health_check(&self) -> Result<(), YuanrongStoreError> {
        self.call(unsafe { sys::yr_client_health_check(self.handle.as_ptr()) })
    }

    fn get(&self, key: &str) -> Result<Option<Vec<u8>>, YuanrongStoreError> {
        let key = Self::native_key(key)?;
        let mut data = ptr::null_mut();
        let mut size = 0;
        let code = unsafe {
            sys::yr_client_get(
                self.handle.as_ptr(),
                key.as_ptr(),
                key.len(),
                &mut data,
                &mut size,
            )
        };
        if code == sys::YR_NOT_FOUND {
            return Ok(None);
        }
        self.call(code)?;
        let data = NonNull::new(data).ok_or_else(|| {
            YuanrongStoreError::Internal("Yuanrong get succeeded without returning a buffer".into())
        })?;
        let value = Self::decode_value(unsafe { slice::from_raw_parts(data.as_ptr(), size) });
        unsafe {
            sys::yr_buffer_free(data.as_ptr().cast());
        }
        Ok(Some(value?))
    }

    fn set(&self, key: &str, value: &[u8]) -> Result<(), YuanrongStoreError> {
        let key = Self::native_key(key)?;
        let value = Self::encode_value(value);
        self.call(unsafe {
            sys::yr_client_set(
                self.handle.as_ptr(),
                key.as_ptr(),
                key.len(),
                value.as_ptr(),
                value.len(),
            )
        })
    }

    fn delete(&self, key: &str) -> Result<(), YuanrongStoreError> {
        let key = Self::native_key(key)?;
        self.call(unsafe { sys::yr_client_delete(self.handle.as_ptr(), key.as_ptr(), key.len()) })
    }

    fn exists(&self, key: &str) -> Result<bool, YuanrongStoreError> {
        let key = Self::native_key(key)?;
        let mut exists = 0;
        self.call(unsafe {
            sys::yr_client_exists(self.handle.as_ptr(), key.as_ptr(), key.len(), &mut exists)
        })?;
        Ok(exists != 0)
    }

    fn batch_get(&self, keys: &[String]) -> Result<Vec<Option<Vec<u8>>>, YuanrongStoreError> {
        if keys.is_empty() {
            return Ok(Vec::new());
        }
        let native_keys = Self::native_keys(keys)?;
        let (key_ptrs, key_lens) = Self::key_parts(&native_keys);
        let mut values = ptr::null_mut();
        self.call(unsafe {
            sys::yr_client_mget(
                self.handle.as_ptr(),
                key_ptrs.as_ptr(),
                key_lens.as_ptr(),
                keys.len(),
                &mut values,
            )
        })?;
        let values = NonNull::new(values).ok_or_else(|| {
            YuanrongStoreError::Internal("Yuanrong mget succeeded without returning results".into())
        })?;
        let native = unsafe { slice::from_raw_parts(values.as_ptr(), keys.len()) };
        let result = native
            .iter()
            .map(|value| {
                if value.found == 0 {
                    Ok(None)
                } else {
                    let data = NonNull::new(value.data).ok_or_else(|| {
                        YuanrongStoreError::Internal(
                            "Yuanrong mget returned a null value buffer".into(),
                        )
                    })?;
                    Self::decode_value(unsafe { slice::from_raw_parts(data.as_ptr(), value.len) })
                        .map(Some)
                }
            })
            .collect();
        unsafe {
            sys::yr_buffers_free(values.as_ptr(), keys.len());
        }
        result
    }

    fn batch_set(&self, entries: &[(String, Vec<u8>)]) -> Result<(), YuanrongStoreError> {
        const MAX_MSET_KEYS: usize = 1_999;
        const MAX_MSET_ENCODED_VALUE_SIZE: usize = 500 * 1024;

        for chunk in entries.chunks(MAX_MSET_KEYS) {
            if chunk
                .iter()
                .any(|(_, value)| value.len() + 1 >= MAX_MSET_ENCODED_VALUE_SIZE)
            {
                for (key, value) in chunk {
                    self.set(key, value)?;
                }
                continue;
            }
            let logical_keys = chunk.iter().map(|(key, _)| key.clone()).collect::<Vec<_>>();
            let keys = Self::native_keys(&logical_keys)?;
            let (key_ptrs, key_lens) = Self::key_parts(&keys);
            let values = chunk
                .iter()
                .map(|(_, value)| Self::encode_value(value))
                .collect::<Vec<_>>();
            let value_ptrs = values.iter().map(Vec::as_ptr).collect::<Vec<_>>();
            let value_lens = values.iter().map(Vec::len).collect::<Vec<_>>();
            self.call(unsafe {
                sys::yr_client_mset(
                    self.handle.as_ptr(),
                    key_ptrs.as_ptr(),
                    key_lens.as_ptr(),
                    value_ptrs.as_ptr(),
                    value_lens.as_ptr(),
                    chunk.len(),
                )
            })?;
        }
        Ok(())
    }

    fn batch_delete(&self, keys: &[String]) -> Result<(), YuanrongStoreError> {
        const MAX_BATCH_KEYS: usize = 10_000;

        for chunk in keys.chunks(MAX_BATCH_KEYS) {
            if chunk.is_empty() {
                continue;
            }
            let native_keys = Self::native_keys(chunk)?;
            let (key_ptrs, key_lens) = Self::key_parts(&native_keys);
            self.call(unsafe {
                sys::yr_client_mdelete(
                    self.handle.as_ptr(),
                    key_ptrs.as_ptr(),
                    key_lens.as_ptr(),
                    chunk.len(),
                )
            })?;
        }
        Ok(())
    }

    fn shutdown(&self) -> Result<(), YuanrongStoreError> {
        self.call(unsafe { sys::yr_client_shutdown(self.handle.as_ptr()) })
    }
}

impl YuanrongProvider {
    /// Connect to Yuanrong through the native C ABI bridge.
    pub async fn connect(config: YuanrongConfig) -> CacheResult<Self> {
        config.validate()?;
        let setup_config = config.clone();
        let setup_timeout = Duration::from_millis(config.connect_timeout_ms);
        let store = tokio::time::timeout(
            setup_timeout,
            tokio::task::spawn_blocking(move || NativeYuanrongStore::connect(&setup_config)),
        )
        .await
        .map_err(|_| {
            CacheError::Timeout(format!(
                "Yuanrong connection exceeded {} ms",
                setup_timeout.as_millis()
            ))
        })?
        .map_err(|error| CacheError::Internal(format!("Yuanrong connection task failed: {error}")))?
        .map_err(map_cache_error)?;

        // A native provider currently owns one native client handle. The C++
        // bridge serializes SDK calls on that handle, so sdk_concurrency only
        // bounds Rust blocking tasks here; it is not backend concurrency.
        Self::from_store(config, Arc::new(store)).await
    }
}

fn map_native_error(code: i32, handle: Option<NonNull<sys::YrClientHandle>>) -> YuanrongStoreError {
    let raw_handle = handle.map_or(ptr::null_mut(), NonNull::as_ptr);
    let value = unsafe { sys::yr_last_error(raw_handle) };
    let message = (!value.is_null())
        .then(|| {
            unsafe { CStr::from_ptr(value) }
                .to_string_lossy()
                .into_owned()
        })
        .filter(|message| !message.is_empty())
        .unwrap_or_else(|| format!("Yuanrong bridge returned status {code}"));

    match code {
        sys::YR_INVALID_ARGUMENT => YuanrongStoreError::InvalidArgument(message),
        sys::YR_UNAVAILABLE => YuanrongStoreError::Unavailable(message),
        sys::YR_TIMEOUT => YuanrongStoreError::Timeout(message),
        sys::YR_NOT_FOUND => {
            YuanrongStoreError::Internal(format!("unexpected not-found status: {message}"))
        }
        _ => YuanrongStoreError::Internal(message),
    }
}

fn map_cache_error(error: YuanrongStoreError) -> CacheError {
    match error {
        YuanrongStoreError::Unavailable(message) => CacheError::Unavailable(message),
        YuanrongStoreError::Timeout(message) => CacheError::Timeout(message),
        YuanrongStoreError::InvalidArgument(message) => CacheError::InvalidArgument(message),
        YuanrongStoreError::Internal(message) => CacheError::Internal(message),
    }
}

#[cfg(test)]
mod tests {
    use super::NativeYuanrongStore;

    #[test]
    fn native_value_codec_round_trips_empty_and_binary_values() {
        for value in [Vec::new(), vec![0, 1, 2, 255]] {
            let encoded = NativeYuanrongStore::encode_value(&value);
            assert!(!encoded.is_empty());
            assert_eq!(NativeYuanrongStore::decode_value(&encoded).unwrap(), value);
        }
    }
}
