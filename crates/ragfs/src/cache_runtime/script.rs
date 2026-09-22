//! Internal named-script registration and result encoding.

use super::{CacheError, CacheResult, ScriptResult};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::RwLock;

/// One stable script identifier and its Redis implementation.
#[derive(Debug, Clone, Copy)]
pub(crate) struct ScriptDefinition {
    pub(crate) id: &'static str,
    pub(crate) redis_lua: &'static str,
}

/// Scripts registered by business modules during Runtime initialization.
#[derive(Default)]
pub(crate) struct ScriptRegistry {
    definitions: RwLock<HashMap<&'static str, &'static str>>,
}

impl ScriptRegistry {
    pub(crate) fn register(&self, definition: ScriptDefinition) -> CacheResult<()> {
        let mut definitions = self
            .definitions
            .write()
            .map_err(|_| CacheError::Internal("script registry lock poisoned".into()))?;
        if let Some(existing) = definitions.get(definition.id) {
            if *existing != definition.redis_lua {
                return Err(CacheError::InvalidArgument(format!(
                    "script {} is already registered with different content",
                    definition.id
                )));
            }
            return Ok(());
        }
        definitions.insert(definition.id, definition.redis_lua);
        Ok(())
    }

    pub(crate) fn resolve(&self, script_id: &str) -> CacheResult<&'static str> {
        self.definitions
            .read()
            .map_err(|_| CacheError::Internal("script registry lock poisoned".into()))?
            .get(script_id)
            .copied()
            .ok_or_else(|| CacheError::UnsupportedScript(script_id.to_string()))
    }
}

/// Provider-neutral representation of an atomic script result.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub(crate) enum ScriptValue {
    Null,
    Integer(i64),
    Bytes(Vec<u8>),
    Array(Vec<ScriptValue>),
    Boolean(bool),
}

impl ScriptResult {
    pub(crate) fn encode(value: &ScriptValue) -> CacheResult<Self> {
        serde_json::to_vec(value)
            .map(bytes::Bytes::from)
            .map(|payload| Self { payload })
            .map_err(|error| CacheError::InvalidData(error.to_string()))
    }

    pub(crate) fn decode(&self) -> CacheResult<ScriptValue> {
        serde_json::from_slice(&self.payload)
            .map_err(|error| CacheError::InvalidData(error.to_string()))
    }
}
