//! Stable values stored behind the provider contract.

use crate::cache_runtime::{CacheError, CacheResult};
use bytes::Bytes;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct QueryCacheEnvelope<T> {
    request: Value,
    payload: T,
}

impl<T> QueryCacheEnvelope<T> {
    /// Build a query cache envelope for one canonical request and payload.
    pub fn new(request: Value, payload: T) -> Self {
        Self { request, payload }
    }

    /// Encode this envelope for provider storage.
    pub fn encode(&self) -> CacheResult<Bytes>
    where
        T: Serialize,
    {
        serde_json::to_vec(self)
            .map(Bytes::from)
            .map_err(|error| CacheError::InvalidData(error.to_string()))
    }

    /// Decode one query cache envelope from provider bytes.
    pub fn decode(value: &[u8]) -> CacheResult<Self>
    where
        T: DeserializeOwned,
    {
        serde_json::from_slice(value).map_err(|error| CacheError::InvalidData(error.to_string()))
    }

    /// Return true when the stored request matches the current request.
    pub fn request_matches(&self, request: &Value) -> bool {
        self.request == *request
    }

    /// Consume the envelope and return its cached payload.
    pub fn into_payload(self) -> T {
        self.payload
    }
}
