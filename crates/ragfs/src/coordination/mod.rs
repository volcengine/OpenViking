//! Service-scoped leadership and monotonic revisions.
//!
//! Backend selection, lease references, and shutdown belong to this runtime.
//! Revisions are allocated before work is persisted and published afterwards.
//! A failed publication leaves redundant work eligible; it never supersedes
//! work that was not successfully enqueued. This is coalescing, not write fencing.

mod cache;
mod memory;
mod sqlite;

use crate::cache_runtime::{CacheError, CacheResult, CacheRuntime};
use cache::{ttl_millis, validate_component, CacheBackend};
use memory::MemoryBackend;
use sqlite::SqliteBackend;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{Mutex, OnceCell, RwLock};
use uuid::Uuid;

/// Retain the latest persisted work per key, without resetting the service counter.
const REVISION_TTL: Duration = Duration::from_secs(24 * 60 * 60);
/// Reclaim unrelated expired local records during normal coordination traffic.
const CLEANUP_INTERVAL: Duration = Duration::from_secs(60);

enum Backend {
    Memory(MemoryBackend),
    Cache(CacheBackend),
    Sqlite(SqliteBackend),
}

#[derive(Clone)]
enum Lease {
    Memory { key: String, token: String },
    Cache(cache::LeaderLease),
    Sqlite { key: String, token: String },
}

/// Explicit backend configuration. Memory is the zero-configuration default.
#[derive(Clone, Debug, serde::Deserialize, serde::Serialize)]
#[serde(tag = "provider", rename_all = "lowercase", deny_unknown_fields)]
pub enum CoordinationConfig {
    /// Process-wide state, isolated by service name.
    Memory {},
    /// A user-selected local database shared by participating processes.
    Sqlite {
        /// Absolute path on a local filesystem, independent of the workspace.
        sqlite_path: PathBuf,
    },
    /// Use the client's shared cache runtime.
    Cache {
        /// Maximum duration of one shared-backend operation, including retries.
        #[serde(default = "default_operation_timeout_ms")]
        operation_timeout_ms: u64,
    },
}

fn default_operation_timeout_ms() -> u64 {
    5_000
}

impl Default for CoordinationConfig {
    fn default() -> Self {
        Self::Memory {}
    }
}

/// One coordination service per filesystem client.
pub struct CoordinationRuntime {
    cache: Option<Arc<CacheRuntime>>,
    config: CoordinationConfig,
    service_name: String,
    backend: OnceCell<Backend>,
    leases: Mutex<HashMap<String, Lease>>,
    closing: RwLock<bool>,
}

impl CoordinationRuntime {
    /// Clients with the same backend namespace share coordination.
    /// Initialization is lazy so unused coordination needs no files or capabilities.
    pub fn new(
        cache: Option<Arc<CacheRuntime>>,
        config: CoordinationConfig,
        service_name: String,
    ) -> CacheResult<Self> {
        validate_component("service_name", &service_name)?;
        match &config {
            CoordinationConfig::Sqlite { sqlite_path } if !sqlite_path.is_absolute() => {
                return Err(CacheError::InvalidArgument(
                    "coordination.sqlite_path must be an absolute local file path".into(),
                ));
            }
            CoordinationConfig::Cache { .. } if cache.is_none() => {
                return Err(CacheError::InvalidArgument(
                    "cache coordination requires a shared CacheRuntime".into(),
                ));
            }
            CoordinationConfig::Cache {
                operation_timeout_ms: 0,
            } => {
                return Err(CacheError::InvalidArgument(
                    "coordination.operation_timeout_ms must be positive".into(),
                ));
            }
            _ => {}
        }
        Ok(Self {
            cache,
            config,
            service_name,
            backend: OnceCell::new(),
            leases: Mutex::new(HashMap::new()),
            closing: RwLock::new(false),
        })
    }

    async fn backend(&self) -> CacheResult<&Backend> {
        // Initialization errors leave the cell empty so the same client can retry.
        self.backend
            .get_or_try_init(|| async {
                let config = self.config.clone();
                let cache = self.cache.clone();
                let service_name = self.service_name.clone();
                // Opening SQLite, creating its schema and cleaning expired rows
                // may block. Keep all backend initialization off the executor.
                tokio::task::spawn_blocking(move || {
                    Ok(match config {
                        CoordinationConfig::Memory {} => {
                            Backend::Memory(MemoryBackend::new(&service_name))
                        }
                        CoordinationConfig::Cache {
                            operation_timeout_ms,
                        } => Backend::Cache(CacheBackend::new(
                            cache.unwrap(),
                            &service_name,
                            Duration::from_millis(operation_timeout_ms),
                        )?),
                        CoordinationConfig::Sqlite { sqlite_path } => {
                            Backend::Sqlite(SqliteBackend::new(&sqlite_path, &service_name)?)
                        }
                    })
                })
                .await
                .map_err(|error| CacheError::Internal(error.to_string()))?
            })
            .await
    }

    /// Fail early if the configured backend cannot provide coordination.
    pub async fn validate_async(&self) -> CacheResult<()> {
        let closing = self.closing.read().await;
        if *closing {
            return Err(CacheError::Closed);
        }
        self.backend().await.map(|_| ())
    }

    /// Acquire one term and retain its ownership token behind an opaque reference.
    pub async fn try_acquire(
        &self,
        key: &str,
        holder: &str,
        ttl: Duration,
    ) -> CacheResult<Option<String>> {
        let closing = self.closing.read().await;
        if *closing {
            return Err(CacheError::Closed);
        }
        validate_component("leader key", key)?;
        validate_component("holder_id", holder)?;
        ttl_millis(ttl)?;
        let lease = match self.backend().await? {
            Backend::Memory(backend) => {
                let token = Uuid::new_v4().to_string();
                backend.acquire(key, &token, ttl)?.then(|| Lease::Memory {
                    key: key.into(),
                    token,
                })
            }
            Backend::Cache(backend) => backend
                .try_acquire(key, holder, ttl)
                .await?
                .map(Lease::Cache),
            Backend::Sqlite(backend) => {
                let token = Uuid::new_v4().to_string();
                backend
                    .acquire(key, &token, ttl)
                    .await?
                    .then(|| Lease::Sqlite {
                        key: key.into(),
                        token,
                    })
            }
        };
        let Some(lease) = lease else {
            return Ok(None);
        };
        let reference = Uuid::new_v4().to_string();
        self.leases.lock().await.insert(reference.clone(), lease);
        Ok(Some(reference))
    }

    /// Renew only a still-live term owned by this client.
    pub async fn renew(&self, reference: &str, ttl: Duration) -> CacheResult<bool> {
        let closing = self.closing.read().await;
        if *closing {
            return Err(CacheError::Closed);
        }
        ttl_millis(ttl)?;
        let lease = self.leases.lock().await.get(reference).cloned();
        let Some(lease) = lease else {
            return Ok(false);
        };
        let renewed = match (self.backend().await?, &lease) {
            (Backend::Memory(backend), Lease::Memory { key, token }) => {
                backend.renew(key, token, ttl)?
            }
            (Backend::Cache(backend), Lease::Cache(lease)) => backend.renew(lease, ttl).await?,
            (Backend::Sqlite(backend), Lease::Sqlite { key, token }) => {
                backend.renew(key, token, ttl).await?
            }
            _ => unreachable!(),
        };
        if !renewed {
            self.leases.lock().await.remove(reference);
        }
        Ok(renewed)
    }

    async fn release_lease(&self, lease: &Lease) -> CacheResult<()> {
        match (self.backend().await?, lease) {
            (Backend::Memory(backend), Lease::Memory { key, token }) => {
                backend.release(key, token)?;
            }
            (Backend::Cache(backend), Lease::Cache(lease)) => {
                backend.release(lease).await?;
            }
            (Backend::Sqlite(backend), Lease::Sqlite { key, token }) => {
                backend.release(key, token).await?;
            }
            _ => unreachable!(),
        }
        Ok(())
    }

    /// Release an opaque reference without ever releasing a successor's term.
    pub async fn release(&self, reference: &str) -> CacheResult<()> {
        let closing = self.closing.read().await;
        if *closing {
            return Err(CacheError::Closed);
        }
        let lease = self.leases.lock().await.get(reference).cloned();
        if let Some(lease) = lease {
            self.release_lease(&lease).await?;
            self.leases.lock().await.remove(reference);
        }
        Ok(())
    }

    async fn revision(&self, key: &str, operation: &'static str, value: i64) -> CacheResult<i64> {
        let closing = self.closing.read().await;
        if *closing {
            return Err(CacheError::Closed);
        }
        if key.is_empty() {
            return Err(CacheError::InvalidArgument("revision key is empty".into()));
        }
        match self.backend().await? {
            Backend::Memory(backend) => backend.revision(key, operation, value),
            Backend::Cache(backend) => backend.revision(key, operation, value).await,
            Backend::Sqlite(backend) => backend.revision(key, operation, value).await,
        }
    }

    /// Reserve a positive revision from the service-wide counter. Gaps are allowed.
    pub async fn allocate_revision(&self, key: &str) -> CacheResult<i64> {
        self.revision(key, cache::ALLOCATE, 0).await
    }

    /// Publish persisted work, retaining the maximum for another day.
    pub async fn publish_revision(&self, key: &str, revision: i64) -> CacheResult<i64> {
        if revision <= 0 {
            return Err(CacheError::InvalidArgument(
                "revision must be positive".into(),
            ));
        }
        self.revision(key, cache::PUBLISH, revision).await
    }

    /// Return zero when no unexpired publication exists. Reads do not extend TTL.
    pub async fn latest_revision(&self, key: &str) -> CacheResult<i64> {
        self.revision(key, cache::LATEST, 0).await
    }

    /// Drain in-flight operations and release terms before the cache is closed.
    pub async fn close(&self) -> CacheResult<()> {
        let mut closing = self.closing.write().await;
        *closing = true;
        let mut leases = self.leases.lock().await;
        let mut error = None;
        for lease in leases.values() {
            if let Err(e) = self.release_lease(lease).await {
                error = Some(e);
            }
        }
        leases.clear();
        match error {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }
}
