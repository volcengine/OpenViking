//! Stable values stored behind the provider contract.

use super::{CacheError, CacheResult};
use crate::core::FileInfo;
use bytes::Bytes;
use serde::{Deserialize, Serialize};

const CACHE_ENVELOPE_VERSION: u8 = 1;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) enum CacheObjectKind {
    File,
    Directory,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct GenerationSnapshot {
    pub key: String,
    pub value: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum CachePayload {
    File(Vec<u8>),
    Directory(Vec<FileInfo>),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct CacheEnvelope {
    version: u8,
    kind: CacheObjectKind,
    path: String,
    generations: Vec<GenerationSnapshot>,
    payload: CachePayload,
}

impl CacheEnvelope {
    pub fn file(path: String, data: Vec<u8>, generations: Vec<GenerationSnapshot>) -> Self {
        Self {
            version: CACHE_ENVELOPE_VERSION,
            kind: CacheObjectKind::File,
            path,
            generations,
            payload: CachePayload::File(data),
        }
    }

    pub fn directory(
        path: String,
        entries: Vec<FileInfo>,
        generations: Vec<GenerationSnapshot>,
    ) -> Self {
        Self {
            version: CACHE_ENVELOPE_VERSION,
            kind: CacheObjectKind::Directory,
            path,
            generations,
            payload: CachePayload::Directory(entries),
        }
    }

    pub fn encode(&self) -> CacheResult<Bytes> {
        serde_json::to_vec(self)
            .map(Bytes::from)
            .map_err(|error| CacheError::InvalidData(error.to_string()))
    }

    pub fn decode(value: &[u8]) -> CacheResult<Self> {
        let envelope: Self = serde_json::from_slice(value)
            .map_err(|error| CacheError::InvalidData(error.to_string()))?;
        if envelope.version != CACHE_ENVELOPE_VERSION {
            return Err(CacheError::InvalidData(format!(
                "unsupported envelope version {}",
                envelope.version
            )));
        }
        Ok(envelope)
    }

    pub fn matches(&self, kind: CacheObjectKind, path: &str) -> bool {
        self.kind == kind && self.path == path
    }

    pub fn generations(&self) -> &[GenerationSnapshot] {
        &self.generations
    }

    pub fn into_file(self) -> CacheResult<Vec<u8>> {
        match self.payload {
            CachePayload::File(data) => Ok(data),
            CachePayload::Directory(_) => {
                Err(CacheError::InvalidData("expected file payload".to_string()))
            }
        }
    }

    pub fn into_directory(self) -> CacheResult<Vec<FileInfo>> {
        match self.payload {
            CachePayload::Directory(entries) => Ok(entries),
            CachePayload::File(_) => Err(CacheError::InvalidData(
                "expected directory payload".to_string(),
            )),
        }
    }
}
