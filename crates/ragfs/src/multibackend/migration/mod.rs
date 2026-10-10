//! V2 protocol initialization and resumable full-data import.

use std::sync::Arc;

use uuid::Uuid;

use crate::core::context::{FsContextInner, FS_CTX};
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::internal_names::is_multiwrite_internal_path;
use crate::multibackend::constants::MULTIWRITE_MOUNT_PREFIX;
use crate::multibackend::meta::MetadataStore;
use crate::multibackend::model::{
    PendingEvent, PendingEventKind, ProtocolState, ProtocolStatus, ProtocolVersion,
    SegmentEventType,
};
use crate::multibackend::provider::{bootstrap_account, MultiWriteProvider};
use crate::multibackend::runtime::read_active_accounts;

/// Reads or initializes the persisted V2 multi-write protocol state.
pub struct ProtocolDetector {
    store: Arc<MetadataStore>,
}

impl ProtocolDetector {
    /// Create a detector backed by the supplied raw-primary metadata store.
    pub fn new(store: Arc<MetadataStore>) -> Self {
        Self { store }
    }

    /// Read the protocol state, creating V2 migrating state when absent.
    pub async fn detect(&self) -> Result<ProtocolState> {
        let (protocol_path, _) = self.store.paths().mount_protocol();
        match self.store.read_bytes(&protocol_path).await {
            Ok(bytes) => decode_protocol(&bytes),
            Err(Error::NotFound(_)) => {
                let state = ProtocolState {
                    protocol_version: ProtocolVersion::V2,
                    status: ProtocolStatus::Migrating,
                };
                let bytes = serde_json::to_vec(&state)?;
                match self.store.write_immutable(&protocol_path, &bytes).await {
                    Ok(()) => Ok(state),
                    Err(Error::AlreadyExists(_)) => {
                        decode_protocol(&self.store.read_bytes(&protocol_path).await?)
                    }
                    Err(error) => Err(error),
                }
            }
            Err(error) => Err(error),
        }
    }
}

/// Imports current primary files into V2 segment metadata.
pub struct FullDataImporter {
    primary: Arc<dyn FileSystem>,
    store: Arc<MetadataStore>,
    provider: Arc<dyn MultiWriteProvider>,
    initial_partitions: u32,
    backup_names: Vec<String>,
}

impl FullDataImporter {
    /// Create an importer for one primary, metadata store, and persistence provider.
    pub fn new(
        primary: Arc<dyn FileSystem>,
        store: Arc<MetadataStore>,
        provider: Arc<dyn MultiWriteProvider>,
        initial_partitions: u32,
        backup_names: Vec<String>,
    ) -> Self {
        Self {
            primary,
            store,
            provider,
            initial_partitions,
            backup_names,
        }
    }

    /// Import every current account file and atomically mark V2 stable.
    pub async fn run(&self) -> Result<()> {
        let (protocol_path, _) = self.store.paths().mount_protocol();
        let migrating_bytes = self.store.read_bytes(&protocol_path).await?;
        if decode_protocol(&migrating_bytes)?.status == ProtocolStatus::Stable {
            return Ok(());
        }

        // ponytail: fixed delay assumes account bootstrap finishes within 10s; use readiness signaling if that changes.
        tokio::time::sleep(std::time::Duration::from_secs(10)).await;
        let mut accounts = read_active_accounts(self.primary.as_ref())
            .await?
            .into_iter()
            .collect::<Vec<_>>();
        accounts.sort();
        for account in &accounts {
            if !read_active_accounts(self.primary.as_ref())
                .await?
                .contains(account)
            {
                continue;
            }
            let partitions = self
                .store
                .initialize_account(account, self.initial_partitions, &self.backup_names)
                .await?;
            bootstrap_account(
                self.provider.as_ref(),
                self.store.as_ref(),
                account,
                &partitions,
            )
            .await?;
            let context = Arc::new(FsContextInner::new(account).with_auto_pathlock_disabled());
            FS_CTX.scope(context, self.import_account(account)).await?;
        }
        self.cleanup_v1_metadata().await?;

        let stable_bytes = serde_json::to_vec(&ProtocolState {
            protocol_version: ProtocolVersion::V2,
            status: ProtocolStatus::Stable,
        })?;
        if self
            .store
            .compare_protocol(&migrating_bytes, &stable_bytes)
            .await?
        {
            return Ok(());
        }
        if decode_protocol(&self.store.read_bytes(&protocol_path).await?)?.status
            == ProtocolStatus::Stable
        {
            Ok(())
        } else {
            Err(Error::invalid_operation(
                "multi-write protocol changed during full-data import",
            ))
        }
    }

    /// Scan one account through the business filesystem.
    async fn import_account(&self, account: &str) -> Result<()> {
        let root = format!("/{account}");
        let mut pending = vec![root];
        while let Some(directory) = pending.pop() {
            let mut entries = self.primary.read_internal_dir(&directory).await?;
            entries.sort_by(|left, right| left.name.cmp(&right.name));
            for entry in entries {
                let path = child_path(&directory, &entry.name);
                let logical_path = format!("{MULTIWRITE_MOUNT_PREFIX}{path}");
                if is_multiwrite_internal_path(&logical_path) {
                    continue;
                }
                if entry.is_dir {
                    pending.push(path);
                    continue;
                }
                self.provider
                    .flush(
                        None,
                        vec![PendingEvent {
                            operation_id: Uuid::new_v4(),
                            account_id: account.to_string(),
                            kind: PendingEventKind::Data(SegmentEventType::Write),
                            path: logical_path,
                            destination_path: None,
                            route_hint: None,
                        }],
                    )
                    .await?;
            }
        }
        Ok(())
    }

    /// Remove V1 metadata through the configured primary filesystem.
    /// Returns an error when scanning or primary removal fails.
    async fn cleanup_v1_metadata(&self) -> Result<()> {
        match self.primary.stat("/_system/.multiwrite.global.json").await {
            Ok(_) => {}
            Err(Error::NotFound(_)) => return Ok(()),
            Err(error) => return Err(error),
        }

        let page = self
            .primary
            .glob_directory("/", "**/.sync_log.json", true, None, None, None)
            .await?;
        for entry in page.entries {
            if entry.is_dir {
                continue;
            }
            match self.primary.remove(&entry.path).await {
                Ok(()) | Err(Error::NotFound(_)) => {}
                Err(error) => return Err(error),
            }
        }

        match self
            .primary
            .remove("/_system/.multiwrite.global.json")
            .await
        {
            Ok(()) | Err(Error::NotFound(_)) => Ok(()),
            Err(error) => Err(error),
        }
    }
}

/// Deserialize and validate exact V2 protocol bytes.
fn decode_protocol(bytes: &[u8]) -> Result<ProtocolState> {
    let state: ProtocolState = serde_json::from_slice(bytes)?;
    state.validate()?;
    Ok(state)
}

/// Join one direct child name to a canonical mount-relative directory.
fn child_path(directory: &str, name: &str) -> String {
    if directory == "/" {
        format!("/{name}")
    } else {
        format!("{directory}/{name}")
    }
}
