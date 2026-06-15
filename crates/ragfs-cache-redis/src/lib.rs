//! Redis cache provider adapter for RAGFS.

mod client;
mod config;
mod provider;

pub use config::RedisConfig;
pub use provider::RedisProvider;
