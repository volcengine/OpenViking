use serde::{Deserialize, Serialize};

/// Shape manifest file stored at the backend root.
pub const SHAPE_MANIFEST_PATH: &str = "/.ragfs_backend_meta.json";
/// Current shape manifest schema version.
pub const SHAPE_MANIFEST_VERSION: u32 = 1;

/// Physical storage layout of one backend.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum StorageShape {
    /// Backend stores plaintext user files.
    Plaintext,
    /// Backend stores encrypted user files.
    Encrypted {
        /// Crypto provider identifier embedded in the envelope.
        provider_type: u8,
        /// Envelope format version.
        envelope_version: u8,
    },
}

/// Persisted backend-shape manifest.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BackendShapeManifest {
    /// Manifest schema version.
    pub version: u32,
    /// Backend plugin name.
    pub backend_type: String,
    /// Observed storage shape.
    pub shape: StorageShape,
}
