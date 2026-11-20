//! Internal named-script registration and result encoding.

use super::{CacheError, CacheResult, ScriptResult};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::RwLock;

/// Scripts registered by business modules during Runtime initialization.
#[derive(Default)]
pub(crate) struct ScriptRegistry {
    definitions: RwLock<HashMap<String, String>>,
}

impl ScriptRegistry {
    /// Register one script with owned identifier and source.
    pub(crate) fn register(&self, script_id: String, redis_lua: String) -> CacheResult<()> {
        let mut definitions = self
            .definitions
            .write()
            .map_err(|_| CacheError::Internal("script registry lock poisoned".into()))?;
        if let Some(existing) = definitions.get(&script_id) {
            if existing != &redis_lua {
                return Err(CacheError::InvalidArgument(format!(
                    "script {} is already registered with different content",
                    script_id
                )));
            }
            return Ok(());
        }
        definitions.insert(script_id, redis_lua);
        Ok(())
    }

    /// Resolve one registered script source.
    pub(crate) fn resolve(&self, script_id: &str) -> CacheResult<String> {
        self.definitions
            .read()
            .map_err(|_| CacheError::Internal("script registry lock poisoned".into()))?
            .get(script_id)
            .cloned()
            .ok_or_else(|| CacheError::UnsupportedScript(script_id.to_string()))
    }
}

/// Provider-neutral representation of an atomic script result.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ScriptValue {
    /// Redis null result.
    Null,
    /// Redis integer result.
    Integer(i64),
    /// Redis byte string result.
    Bytes(Vec<u8>),
    /// Redis nested array result.
    Array(Vec<ScriptValue>),
    /// Redis boolean result.
    Boolean(bool),
}

impl ScriptResult {
    /// Encode one provider-neutral script value.
    pub fn encode(value: &ScriptValue) -> CacheResult<Self> {
        serde_json::to_vec(value)
            .map(bytes::Bytes::from)
            .map(|payload| Self { payload })
            .map_err(|error| CacheError::InvalidData(error.to_string()))
    }

    /// Decode one provider-neutral script value.
    pub fn decode(&self) -> CacheResult<ScriptValue> {
        serde_json::from_slice(&self.payload)
            .map_err(|error| CacheError::InvalidData(error.to_string()))
    }
}
