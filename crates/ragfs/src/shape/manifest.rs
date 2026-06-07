/// Shape guard file stored at the backend root.
pub const SHAPE_MANIFEST_PATH: &str = "/backend_meta.json";

/// Physical storage layout of one backend.
#[derive(Debug, Clone, PartialEq, Eq)]
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
