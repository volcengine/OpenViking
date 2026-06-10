//! Backend storage shape probing and validation.

/// Shape manifest definitions and constants.
pub mod manifest;
/// Legacy backend shape probing helpers.
pub mod probe;
/// Backend shape validation helpers.
pub mod validate;

pub use manifest::{StorageShape, SHAPE_MANIFEST_PATH};
