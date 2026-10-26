//! Built-in Redis CacheRuntime provider.

mod client;
mod config;
mod provider;

use client::RedisClient;
pub use config::{RedisDeploymentMode, RedisProviderConfig};
pub(crate) use provider::RedisProvider;
