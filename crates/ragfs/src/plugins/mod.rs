//! Plugins module
//!
//! This module contains all built-in filesystem plugins.

pub mod kvfs;
pub mod localfs;
pub mod memfs;
pub mod queuefs;
#[cfg(feature = "s3")]
pub mod s3fs;
pub mod serverinfofs;
pub mod sqlfs;

pub use kvfs::{KVFSPlugin, KVFileSystem};
pub use localfs::{LocalFSPlugin, LocalFileSystem};
pub use memfs::{MemFSPlugin, MemFileSystem};
pub use queuefs::{QueueFSPlugin, QueueFileSystem};
#[cfg(feature = "s3")]
pub use s3fs::{S3FSPlugin, S3FileSystem};
pub use serverinfofs::{ServerInfoFSPlugin, ServerInfoFileSystem};
pub use sqlfs::{SQLFSPlugin, SQLFileSystem};
