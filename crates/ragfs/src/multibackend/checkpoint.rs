//! Checkpoint construction, validation, publication, and reading.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use tokio::sync::watch;
use tracing::warn;

use crate::core::context::{FsContextInner, FS_CTX};
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::internal_names::is_multiwrite_internal_path;
use crate::multibackend::catch_up::{CheckpointConsumer, CheckpointReadResult, CheckpointSnapshot};
use crate::multibackend::codec::{decode_checkpoint_chunk, encode_checkpoint_chunk, sha256_hex};
use crate::multibackend::constants::{CHECKPOINT_CHUNK_FILE_EXTENSION, CHECKPOINT_CHUNK_FILE_PREFIX, CHECKPOINT_DIR_PREFIX,CHECKPOINT_DIR_TIME_FORMAT, MANIFEST_FILE, MAX_CHECKPOINT_NODES, MULTIWRITE_MOUNT_PREFIX,};
use crate::multibackend::gc::MetadataGc;
use crate::multibackend::meta::{MetadataStore, MultiWriteWorker};
use crate::multibackend::model::{
    is_checkpoint_directory_name, CheckpointManifest, CheckpointNode, CheckpointsManifest,
    ChunkDescriptor, DirectoryOperation, FileState, LatestCheckpoint, PartitionsManifest,
    ProtocolStatus, ScopeKey, ScopedSeq, SegmentEventType, SegmentRecord,
};
use crate::multibackend::provider::MultiWriteProvider;
use crate::multibackend::router::AccountRouter;

/// Periodically builds checkpoints for every Stable account partition.
pub struct CheckpointWorker {
    store: Arc<MetadataStore>,
    builder: CheckpointBuilder,
    gc: MetadataGc,
}
impl CheckpointWorker {
    /// Create a worker over the production primary, store, and provider.
    pub fn new(
        primary: Arc<dyn FileSystem>,
        store: Arc<MetadataStore>,
        provider: Arc<dyn MultiWriteProvider>,
    ) -> Self {
        let builder = CheckpointBuilder::new(primary, store.clone(), provider.clone());
        Self {
            gc: MetadataGc::new(store.clone(), provider),
            builder,
            store,
        }
    }

    /// Run checkpoint rounds at the configured interval until cancellation.
    pub async fn run(&self, interval: Duration, mut cancellation: watch::Receiver<bool>) {
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = ticker.tick() => if let Err(error) = self.run_once(interval).await {
                    warn!(error = %error, "multi-write checkpoint discovery failed");
                },
                changed = cancellation.changed() => {
                    if changed.is_err() || *cancellation.borrow() {
                        break;
                    }
                }
            }
        }
    }

    /// Build one checkpoint round while isolating account and scope failures.
    pub async fn run_once(&self, interval: Duration) -> Result<()> {
        let result = self.run_once_inner(interval).await;
        if result.is_err() {
            self.store.record_worker_error(MultiWriteWorker::Checkpoint);
        }
        result
    }

    /// Execute one checkpoint round and return discovery failures.
    async fn run_once_inner(&self, interval: Duration) -> Result<()> {
        if self.store.protocol_status().await? != ProtocolStatus::Stable {
            return Ok(());
        }
        let now = now_ns()?;
        let interval_ns = u64::try_from(interval.as_nanos()).unwrap_or(u64::MAX);
        for account in self.store.initialized_accounts().await? {
            let path = self.store.paths().account_manifest(&account)?.0;
            let manifest: PartitionsManifest = match self.store.read_json(&path).await {
                Ok(manifest) => manifest,
                Err(Error::NotFound(_)) => continue,
                Err(error) => {
                    self.store.record_worker_error(MultiWriteWorker::Checkpoint);
                    warn!(account = %account, error = %error,
                        "multi-write checkpoint account metadata unavailable");
                    continue;
                }
            };
            if let Err(error) = manifest.validate() {
                self.store.record_worker_error(MultiWriteWorker::Checkpoint);
                warn!(account = %account, error = %error,
                    "multi-write checkpoint account metadata invalid");
                continue;
            }
            for (&partition_id, partition) in &manifest.partitions {
                if partition.state != crate::multibackend::model::PartitionState::Stable {
                    continue;
                }
                let scope = ScopeKey {
                    account_id: account.clone(),
                    partition_id,
                    epoch: manifest.epoch,
                };
                let pointer_path = self
                    .store
                    .paths()
                    .checkpoints_manifest(&account, partition_id)?
                    .0;
                let checkpoints: CheckpointsManifest =
                    match self.store.read_json(&pointer_path).await {
                        Ok(checkpoints) => checkpoints,
                        Err(Error::NotFound(_)) => continue,
                        Err(error) => {
                            self.store.record_worker_error(MultiWriteWorker::Checkpoint);
                            warn!(account = %account, partition = partition_id, error = %error,
                            "multi-write checkpoint pointer unavailable");
                            continue;
                        }
                    };
                if let Err(error) = checkpoints.validate() {
                    self.store.record_worker_error(MultiWriteWorker::Checkpoint);
                    warn!(account = %account, partition = partition_id, error = %error,
                        "multi-write checkpoint pointer invalid");
                    continue;
                }
                if checkpoints
                    .latest_checkpoint
                    .as_ref()
                    .is_some_and(|latest| now < latest.updated_at_ns.saturating_add(interval_ns))
                {
                    continue;
                }
                match self.builder.build_scope(&scope).await {
                    Ok(CheckpointBuildResult::Published(_)) => {
                        if let Err(error) = self.gc.collect_scope(&scope).await {
                            warn!(account = %account, partition = partition_id, error = %error,
                                "multi-write metadata garbage collection failed");
                        }
                    }
                    Ok(_) => {}
                    Err(Error::NotFound(_)) => {}
                    Err(error) => {
                        self.store.record_worker_error(MultiWriteWorker::Checkpoint);
                        warn!(account = %account, partition = partition_id, error = %error,
                            "multi-write checkpoint build failed");
                    }
                }
            }
        }
        Ok(())
    }
}

/// Result of one bounded checkpoint build attempt.
pub enum CheckpointBuildResult {
    /// No new sealed sequence exists for this scope.
    NoSealedChanges,
    /// A validated checkpoint became the latest publication.
    Published(LatestCheckpoint),
    /// Another builder published first and this candidate was discarded.
    Retry,
}

#[derive(Default)]
struct SegmentDelta {
    states: BTreeMap<String, FileState>,
    exact_paths: BTreeSet<String>,
    prefixes: BTreeSet<String>,
}

impl SegmentDelta {
    /// Return whether this segment delta cannot change checkpoint contents.
    fn is_empty(&self) -> bool {
        self.states.is_empty() && self.exact_paths.is_empty() && self.prefixes.is_empty()
    }
}

enum ChunkMergeTarget<'a> {
    All,
    Range(&'a str, Option<&'a str>),
    RootOnly(&'a str),
}

/// Builds immutable checkpoints from sealed records and current primary state.
pub struct CheckpointBuilder {
    primary: Arc<dyn FileSystem>,
    store: Arc<MetadataStore>,
    provider: Arc<dyn MultiWriteProvider>,
    router: AccountRouter,
}
impl CheckpointBuilder {
    /// Create a builder over the encrypted primary and raw metadata store.
    pub fn new(
        primary: Arc<dyn FileSystem>,
        store: Arc<MetadataStore>,
        provider: Arc<dyn MultiWriteProvider>,
    ) -> Self {
        Self {
            primary,
            router: AccountRouter::new(store.clone()),
            store,
            provider,
        }
    }

    /// Build and compare-before-publish one Stable partition checkpoint.
    pub async fn build_scope(&self, scope: &ScopeKey) -> Result<CheckpointBuildResult> {
        scope.validate()?;
        let pointer_path = self
            .store
            .paths()
            .checkpoints_manifest(&scope.account_id, scope.partition_id)?
            .0;
        let lease = self
            .store
            .pathlock_manager()
            .acquire_exact(
                &pointer_path,
                self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
                None,
            )
            .await?;
        let read_pointer = async {
            let start: CheckpointsManifest = self.store.read_json(&pointer_path).await?;
            start.validate()?;
            Ok(start)
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&lease)
            .await
            .map_err(Error::from);
        let start = finish_with_release(read_pointer, release)?;
        let segments = self.provider.read_manifest(scope).await?;
        segments.validate()?;
        let Some(tail) = segments
            .segments
            .iter()
            .filter_map(|segment| segment.segment_to_seq)
            .last()
        else {
            return Ok(CheckpointBuildResult::NoSealedChanges);
        };
        let old_to = start
            .latest_checkpoint
            .as_ref()
            .map_or(0, |latest| latest.checkpoint_to_seq);
        if tail <= old_to {
            return Ok(CheckpointBuildResult::NoSealedChanges);
        }
        let min_synced = segments
            .backend_states
            .values()
            .map(|state| state.synced_seq)
            .min();
        let created_at_ns = now_ns()?;
        let candidate = checkpoint_candidate_name(created_at_ns)?;
        let directory = self
            .store
            .paths()
            .checkpoints_dir(&scope.account_id, scope.partition_id)?
            .0;
        let candidate_path = format!("{directory}/{}", candidate.trim_end_matches('/'));
        let candidate_lease = self
            .store
            .pathlock_manager()
            .acquire_exact(
                &candidate_path,
                self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
                None,
            )
            .await?;
        let operation = async {
            let mut manifest = self
                .prepare_candidate_checkpoint(scope, &start, &candidate, created_at_ns, old_to)
                .await?;
            let mut chunk_index = checkpoint_chunk_count(&manifest.root);
            for descriptor in segments
                .segments
                .iter()
                .filter(|descriptor| descriptor.segment_to_seq.is_some_and(|to| to > old_to))
            {
                let records = self.provider.read_sealed_segment(scope, descriptor).await?;
                let records = records
                    .into_iter()
                    .filter(|record| record.seq > old_to && record.seq <= tail)
                    .collect::<Vec<_>>();
                if records.is_empty() {
                    continue;
                }
                let delta = self.segment_delta(scope, records).await?;
                self.apply_checkpoint_delta(
                    scope,
                    &candidate,
                    &mut manifest,
                    &delta,
                    &mut chunk_index,
                )
                .await?;
            }
            self.prune_checkpoint_tombstones(
                scope,
                &candidate,
                &mut manifest,
                min_synced,
                &mut chunk_index,
            )
            .await?;
            manifest.version = 1;
            manifest.partition_id = scope.partition_id;
            manifest.epoch = scope.epoch;
            manifest.checkpoint_from_seq = old_to + 1;
            manifest.checkpoint_to_seq = tail;
            manifest.created_at_ns = created_at_ns;
            manifest.checksum = tree_checksum(&manifest.root)?;
            manifest.validate_for_scope(scope)?;
            let manifest_bytes = serde_json::to_vec(&manifest)?;
            self.store
                .write_bytes(
                    &format!("{directory}/{candidate}{MANIFEST_FILE}"),
                    &manifest_bytes,
                )
                .await?;
            verify_checkpoint(self.store.as_ref(), scope, &manifest, &candidate, tail).await?;
            let latest = LatestCheckpoint {
                path: candidate,
                checkpoint_to_seq: tail,
                updated_at_ns: created_at_ns,
            };
            let lease = self
                .store
                .pathlock_manager()
                .acquire_exact(
                    &pointer_path,
                    self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
                    None,
                )
                .await?;
            let publish = async {
                let current: CheckpointsManifest = self.store.read_json(&pointer_path).await?;
                if current.latest_checkpoint != start.latest_checkpoint {
                    return Ok(CheckpointBuildResult::Retry);
                }
                self.store
                    .publish_json(
                        &pointer_path,
                        &CheckpointsManifest {
                            version: 1,
                            latest_checkpoint: Some(latest.clone()),
                        },
                        CheckpointsManifest::validate,
                    )
                    .await?;
                Ok(CheckpointBuildResult::Published(latest))
            }
            .await;
            let release = self
                .store
                .pathlock_manager()
                .release(&lease)
                .await
                .map_err(Error::from);
            finish_with_release(publish, release)
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&candidate_lease)
            .await
            .map_err(Error::from);
        finish_with_release(operation, release)
    }

    /// Prepare a candidate checkpoint directory from the latest checkpoint or an empty base.
    async fn prepare_candidate_checkpoint(
        &self,
        scope: &ScopeKey,
        start: &CheckpointsManifest,
        candidate: &str,
        created_at_ns: u64,
        old_to: u64,
    ) -> Result<CheckpointManifest> {
        if let Some(latest) = &start.latest_checkpoint {
            let manifest = read_manifest(self.store.as_ref(), scope, &latest.path).await?;
            if manifest.checkpoint_to_seq != latest.checkpoint_to_seq {
                return Err(Error::Serialization(
                    "checkpoint pointer sequence mismatch".into(),
                ));
            }
            let manifest = copy_checkpoint_files(
                self.store.as_ref(),
                scope,
                &latest.path,
                candidate,
                &manifest,
            )
            .await?;
            return Ok(manifest);
        }

        let mut chunk_index = 0;
        let (root, chunks) = encode_states_with_index(scope, Vec::new(), &mut chunk_index)?;
        let checksum = tree_checksum(&root)?;
        let manifest = CheckpointManifest {
            version: 1,
            partition_id: scope.partition_id,
            epoch: scope.epoch,
            checkpoint_from_seq: 1,
            checkpoint_to_seq: old_to,
            created_at_ns,
            root,
            checksum,
        };
        manifest.validate_for_scope(scope)?;
        write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
        let directory = checkpoint_directory(self.store.as_ref(), scope, candidate)?;
        self.store
            .write_bytes(
                &format!("{directory}/{MANIFEST_FILE}"),
                &serde_json::to_vec(&manifest)?,
            )
            .await?;
        Ok(manifest)
    }

    /// Convert one sealed segment into checkpoint path replacements and prefix invalidations.
    async fn segment_delta(
        &self,
        scope: &ScopeKey,
        records: Vec<SegmentRecord>,
    ) -> Result<SegmentDelta> {
        let mut delta = SegmentDelta::default();
        for record in records {
            match record.event_type {
                SegmentEventType::Write | SegmentEventType::Remove => {
                    let backend_path = self
                        .store
                        .paths()
                        .backend_path(&scope.account_id, &record.path)?;
                    let deleted = match FS_CTX
                        .scope(
                            Arc::new(
                                FsContextInner::new(&scope.account_id)
                                    .with_bypass_cache(true)
                                    .with_auto_pathlock_disabled(),
                            ),
                            self.primary.stat(&backend_path),
                        )
                        .await
                    {
                        Ok(_) => None,
                        Err(Error::NotFound(_)) => Some(true),
                        Err(error) => return Err(error),
                    };
                    delta.exact_paths.insert(record.path.clone());
                    delta.states.insert(
                        record.path.clone(),
                        FileState {
                            path: record.path,
                            latest_seq: ScopedSeq {
                                scope: scope.clone(),
                                seq: record.seq,
                            },
                            deleted,
                        },
                    );
                }
                SegmentEventType::RemoveTree | SegmentEventType::MoveTree => {
                    let mut prefixes = vec![record.path.clone()];
                    if let Some(destination) = record.destination_path {
                        prefixes.push(destination);
                    }
                    for prefix in prefixes {
                        delta.prefixes.insert(prefix.clone());
                        delta
                            .states
                            .retain(|path, _| !is_path_within(path, &prefix));
                        for path in self.scan_prefix(scope, &prefix).await? {
                            delta.states.insert(
                                path.clone(),
                                FileState {
                                    path,
                                    latest_seq: ScopedSeq {
                                        scope: scope.clone(),
                                        seq: record.seq,
                                    },
                                    deleted: None,
                                },
                            );
                        }
                    }
                }
            }
        }
        Ok(delta)
    }

    /// Apply one segment delta to the candidate manifest by rewriting only needed chunks.
    async fn apply_checkpoint_delta(
        &self,
        scope: &ScopeKey,
        candidate: &str,
        manifest: &mut CheckpointManifest,
        delta: &SegmentDelta,
        chunk_index: &mut usize,
    ) -> Result<()> {
        if delta.is_empty() {
            return Ok(());
        }
        if manifest.root.chunks.is_empty() {
            self.rewrite_full_checkpoint_chunk(scope, candidate, manifest, delta, chunk_index)
                .await?;
        } else {
            self.rewrite_split_checkpoint_chunks(scope, candidate, manifest, delta, chunk_index)
                .await?;
        }
        manifest.root.validate()?;
        Ok(())
    }

    /// Rewrite a checkpoint whose complete state still lives in the root chunk.
    async fn rewrite_full_checkpoint_chunk(
        &self,
        scope: &ScopeKey,
        candidate: &str,
        manifest: &mut CheckpointManifest,
        delta: &SegmentDelta,
        chunk_index: &mut usize,
    ) -> Result<()> {
        let old_root = manifest.root.clone();
        let directory = checkpoint_directory(self.store.as_ref(), scope, candidate)?;
        let states =
            read_checkpoint_chunk_states(self.store.as_ref(), scope, &directory, &old_root, true)
                .await?;
        let mut remaining = delta.states.clone();
        let states = merge_checkpoint_states(states, delta, &mut remaining, ChunkMergeTarget::All);
        let (root, chunks) = encode_states_with_index(scope, states, chunk_index)?;
        write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
        manifest.root = root;
        remove_replaced_checkpoint_files(
            self.store.as_ref(),
            scope,
            candidate,
            checkpoint_chunk_files(&old_root),
            &manifest.root,
        )
        .await;
        Ok(())
    }

    /// Rewrite affected child chunks and insert new states not covered by old ranges.
    async fn rewrite_split_checkpoint_chunks(
        &self,
        scope: &ScopeKey,
        candidate: &str,
        manifest: &mut CheckpointManifest,
        delta: &SegmentDelta,
        chunk_index: &mut usize,
    ) -> Result<()> {
        let directory = checkpoint_directory(self.store.as_ref(), scope, candidate)?;
        let account_root = format!("{MULTIWRITE_MOUNT_PREFIX}/{}", scope.account_id);
        let mut remaining = delta.states.clone();

        if checkpoint_state_is_dirty(&account_root, delta) || remaining.contains_key(&account_root)
        {
            let old_root = manifest.root.clone();
            let children = std::mem::take(&mut manifest.root.chunks);
            let states = read_checkpoint_chunk_states(
                self.store.as_ref(),
                scope,
                &directory,
                &old_root,
                true,
            )
            .await?;
            let states = merge_checkpoint_states(
                states,
                delta,
                &mut remaining,
                ChunkMergeTarget::RootOnly(&account_root),
            );
            let (mut root, chunks) = encode_states_with_index(scope, states, chunk_index)?;
            root.chunks = children;
            write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
            manifest.root = root;
            remove_replaced_checkpoint_files(
                self.store.as_ref(),
                scope,
                candidate,
                vec![old_root.file],
                &manifest.root,
            )
            .await;
        }

        let mut next_children = Vec::new();
        let children = std::mem::take(&mut manifest.root.chunks);
        for descriptor in children {
            if checkpoint_descriptor_is_dirty(&descriptor, delta)? {
                let old_files = checkpoint_chunk_files(&descriptor);
                let states = read_checkpoint_chunk_states(
                    self.store.as_ref(),
                    scope,
                    &directory,
                    &descriptor,
                    false,
                )
                .await?;
                let (start, end) = checkpoint_descriptor_range(&descriptor)?;
                let states = merge_checkpoint_states(
                    states,
                    delta,
                    &mut remaining,
                    ChunkMergeTarget::Range(start, end),
                );
                let (mut descriptors, chunks) =
                    encode_child_states_with_index(scope, states, start, end, chunk_index)?;
                write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
                let replacement_root = ChunkDescriptor {
                    file: String::new(),
                    root_path: account_root.clone(),
                    start_path: None,
                    end_path: None,
                    file_state_count: 0,
                    checksum: String::new(),
                    chunks: descriptors.clone(),
                };
                remove_replaced_checkpoint_files(
                    self.store.as_ref(),
                    scope,
                    candidate,
                    old_files,
                    &replacement_root,
                )
                .await;
                next_children.append(&mut descriptors);
            } else {
                next_children.push(descriptor);
            }
        }

        if !remaining.is_empty() {
            return Err(Error::Serialization(
                "checkpoint delta paths were not assigned to a chunk".into(),
            ));
        }
        sort_checkpoint_children(&mut next_children, &account_root)?;
        manifest.root.chunks = next_children;
        Ok(())
    }

    /// Remove checkpoint tombstones that no current backend still needs.
    async fn prune_checkpoint_tombstones(
        &self,
        scope: &ScopeKey,
        candidate: &str,
        manifest: &mut CheckpointManifest,
        min_synced: Option<u64>,
        chunk_index: &mut usize,
    ) -> Result<()> {
        if manifest.root.chunks.is_empty() {
            let old_root = manifest.root.clone();
            let directory = checkpoint_directory(self.store.as_ref(), scope, candidate)?;
            let states = read_checkpoint_chunk_states(
                self.store.as_ref(),
                scope,
                &directory,
                &old_root,
                true,
            )
            .await?;
            let retained = retain_checkpoint_states(states.clone(), min_synced);
            if retained != states {
                let (root, chunks) = encode_states_with_index(scope, retained, chunk_index)?;
                write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
                manifest.root = root;
                remove_replaced_checkpoint_files(
                    self.store.as_ref(),
                    scope,
                    candidate,
                    checkpoint_chunk_files(&old_root),
                    &manifest.root,
                )
                .await;
            }
            return Ok(());
        }

        let directory = checkpoint_directory(self.store.as_ref(), scope, candidate)?;
        let account_root = format!("{MULTIWRITE_MOUNT_PREFIX}/{}", scope.account_id);
        let old_root = manifest.root.clone();
        let children = std::mem::take(&mut manifest.root.chunks);
        let states =
            read_checkpoint_chunk_states(self.store.as_ref(), scope, &directory, &old_root, true)
                .await?;
        let retained = retain_checkpoint_states(states.clone(), min_synced);
        if retained != states {
            let (mut root, chunks) = encode_states_with_index(scope, retained, chunk_index)?;
            root.chunks = children;
            write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
            manifest.root = root;
            remove_replaced_checkpoint_files(
                self.store.as_ref(),
                scope,
                candidate,
                vec![old_root.file],
                &manifest.root,
            )
            .await;
        } else {
            manifest.root.chunks = children;
        }

        let mut next_children = Vec::new();
        for descriptor in std::mem::take(&mut manifest.root.chunks) {
            let old_files = checkpoint_chunk_files(&descriptor);
            let states = read_checkpoint_chunk_states(
                self.store.as_ref(),
                scope,
                &directory,
                &descriptor,
                false,
            )
            .await?;
            let retained = retain_checkpoint_states(states.clone(), min_synced);
            if retained == states {
                next_children.push(descriptor);
                continue;
            }
            let (start, end) = checkpoint_descriptor_range(&descriptor)?;
            let (mut descriptors, chunks) =
                encode_child_states_with_index(scope, retained, start, end, chunk_index)?;
            write_checkpoint_chunks(self.store.as_ref(), scope, candidate, &chunks).await?;
            let replacement_root = ChunkDescriptor {
                file: String::new(),
                root_path: account_root.clone(),
                start_path: None,
                end_path: None,
                file_state_count: 0,
                checksum: String::new(),
                chunks: descriptors.clone(),
            };
            remove_replaced_checkpoint_files(
                self.store.as_ref(),
                scope,
                candidate,
                old_files,
                &replacement_root,
            )
            .await;
            next_children.append(&mut descriptors);
        }
        sort_checkpoint_children(&mut next_children, &account_root)?;
        manifest.root.chunks = next_children;
        Ok(())
    }

    /// Scan one current primary prefix and retain paths owned by this scope.
    async fn scan_prefix(&self, scope: &ScopeKey, logical_prefix: &str) -> Result<Vec<String>> {
        let backend_prefix = self
            .store
            .paths()
            .backend_path(&scope.account_id, logical_prefix)?;
        let context = Arc::new(
            FsContextInner::new(&scope.account_id)
                .with_bypass_cache(true)
                .with_auto_pathlock_disabled(),
        );
        let paths = FS_CTX
            .scope(context, async {
                let root = match self.primary.stat(&backend_prefix).await {
                    Ok(root) => root,
                    Err(Error::NotFound(_)) => return Ok(Vec::new()),
                    Err(error) => return Err(error),
                };
                let mut paths = vec![backend_prefix.clone()];
                if root.is_dir {
                    paths.extend(
                        self.primary
                            .tree_directory(
                                &backend_prefix,
                                true,
                                None,
                                None,
                                None,
                                None,
                                None,
                                false,
                            )
                            .await?
                            .into_iter()
                            .map(|entry| entry.path),
                    );
                }
                Ok(paths)
            })
            .await?;
        let mut selected = Vec::new();
        for path in paths {
            let logical = format!("{MULTIWRITE_MOUNT_PREFIX}{path}");
            if !is_multiwrite_internal_path(&logical)
                && self.router.route(&scope.account_id, &logical).await?.scope == *scope
            {
                selected.push(logical);
            }
        }
        selected.sort();
        selected.dedup();
        Ok(selected)
    }
}

/// Reads complete validated checkpoint snapshots under the publication lease.
pub struct CheckpointReader {
    store: Arc<MetadataStore>,
}
impl CheckpointReader {
    /// Create a reader over the raw metadata store.
    pub fn new(store: Arc<MetadataStore>) -> Self {
        Self { store }
    }
}

#[async_trait]
impl CheckpointConsumer for CheckpointReader {
    /// Read the latest checkpoint or return a typed retry for immutable races.
    async fn read_latest(
        &self,
        scope: &ScopeKey,
        synced_seq: u64,
        tail_seq: u64,
    ) -> Result<CheckpointReadResult> {
        let pointer_path = self
            .store
            .paths()
            .checkpoints_manifest(&scope.account_id, scope.partition_id)?
            .0;
        let lease = self
            .store
            .pathlock_manager()
            .acquire_exact(
                &pointer_path,
                self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
                None,
            )
            .await?;
        let pointer = async {
            let pointer: CheckpointsManifest = self.store.read_json(&pointer_path).await?;
            pointer.validate()?;
            let Some(latest) = pointer.latest_checkpoint else {
                return Ok(None);
            };
            if latest.checkpoint_to_seq <= synced_seq || latest.checkpoint_to_seq > tail_seq {
                return Ok(None);
            }
            Ok(Some(latest))
        }
        .await;
        let release = self
            .store
            .pathlock_manager()
            .release(&lease)
            .await
            .map_err(Error::from);
        let latest = finish_with_release(pointer, release)?;
        let Some(latest) = latest else {
            return Ok(CheckpointReadResult::NoCheckpoint);
        };
        let states = match read_published(self.store.as_ref(), scope, &latest).await {
            Ok(states) => states,
            Err(Error::NotFound(_)) | Err(Error::Serialization(_)) => {
                return Ok(CheckpointReadResult::RetryLatestCheckpoint)
            }
            Err(error) => return Err(error),
        };
        let account_manifest_path = self.store.paths().account_manifest(&scope.account_id)?.0;
        let account_manifest: PartitionsManifest =
            self.store.read_json(&account_manifest_path).await?;
        account_manifest.validate()?;
        let mut directory_prefixes = BTreeSet::new();
        for event in account_manifest.directory_events {
            if event.positions.iter().any(|position| {
                position.partition_id == scope.partition_id
                    && position.epoch == scope.epoch
                    && position.seq <= latest.checkpoint_to_seq
            }) {
                directory_prefixes.insert(event.source_path);
                if event.operation == DirectoryOperation::MoveTree {
                    directory_prefixes.insert(event.destination_path.ok_or_else(|| {
                        Error::Serialization("move directory event lacks destination".into())
                    })?);
                }
            }
        }
        Ok(CheckpointReadResult::Snapshot(CheckpointSnapshot {
            checkpoint_to_seq: latest.checkpoint_to_seq,
            file_states: states,
            directory_prefixes: directory_prefixes.into_iter().collect(),
        }))
    }
}

/// Encode a deterministic checkpoint tree while continuing the caller's chunk index.
fn encode_states_with_index(
    scope: &ScopeKey,
    mut states: Vec<FileState>,
    index: &mut usize,
) -> Result<(ChunkDescriptor, Vec<(String, Vec<u8>)>)> {
    let (node, root_path) = checkpoint_node_from_states(scope, &mut states)?;
    encode_checkpoint_tree(node, &root_path, index)
}

/// Encode states as one or more non-root checkpoint descriptors.
fn encode_child_states_with_index(
    scope: &ScopeKey,
    mut states: Vec<FileState>,
    start_path: &str,
    end_path: Option<&str>,
    index: &mut usize,
) -> Result<(Vec<ChunkDescriptor>, Vec<(String, Vec<u8>)>)> {
    if states.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }
    let (mut node, root_path) = checkpoint_node_from_states(scope, &mut states)?;
    let mut files = Vec::new();
    let mut descriptors = Vec::new();
    let children = std::mem::take(&mut node.children);
    if node.latest_seq.is_some() {
        descriptors.push(encode_node(node, &root_path, false, index, &mut files)?);
    }
    partition_children(children, &root_path, index, &mut files, &mut descriptors)?;
    assign_checkpoint_child_ranges(&mut descriptors, start_path, end_path)?;
    Ok((descriptors, files))
}

/// Build the account-relative checkpoint radix tree from sorted file states.
fn checkpoint_node_from_states(
    scope: &ScopeKey,
    states: &mut Vec<FileState>,
) -> Result<(CheckpointNode, String)> {
    for state in states.iter() {
        state.validate(scope)?;
    }
    states.sort_by(|left, right| left.path.cmp(&right.path));
    if states.windows(2).any(|pair| pair[0].path == pair[1].path) {
        return Err(Error::Serialization(
            "checkpoint contains duplicate paths".into(),
        ));
    }
    let root_path = format!("{MULTIWRITE_MOUNT_PREFIX}/{}", scope.account_id);
    let mut node = CheckpointNode {
        path_fragment: String::new(),
        latest_seq: None,
        deleted: None,
        children: Vec::new(),
    };
    for state in states.iter() {
        if !is_canonical_account_path(&state.path, &root_path) {
            return Err(Error::Serialization(
                "checkpoint path crosses account root".into(),
            ));
        }
        let relative = state
            .path
            .strip_prefix(&root_path)
            .ok_or_else(|| Error::Serialization("checkpoint path crosses account root".into()))?;
        insert_state(&mut node, relative, state)?;
    }
    Ok((node, root_path))
}

/// Encode one preconstructed radix tree through the production chunk splitter.
fn encode_checkpoint_tree(
    mut node: CheckpointNode,
    root_path: &str,
    index: &mut usize,
) -> Result<(ChunkDescriptor, Vec<(String, Vec<u8>)>)> {
    node.validate()?;
    let mut files = Vec::new();
    let root = if checkpoint_node_count(&node) <= MAX_CHECKPOINT_NODES {
        encode_node(node, root_path, true, index, &mut files)?
    } else {
        let children = std::mem::take(&mut node.children);
        let mut descriptor = encode_node(node, root_path, true, index, &mut files)?;
        partition_children(
            children,
            root_path,
            index,
            &mut files,
            &mut descriptor.chunks,
        )?;
        assign_checkpoint_child_ranges(&mut descriptor.chunks, root_path, None)?;
        descriptor
    };
    Ok((root, files))
}

/// Insert one file state into the account-relative component radix tree.
fn insert_state(root: &mut CheckpointNode, relative: &str, state: &FileState) -> Result<()> {
    let mut node = root;
    let mut remaining = relative;
    while !remaining.is_empty() {
        let Some(index) = node
            .children
            .iter()
            .position(|child| common_prefix_len(&child.path_fragment, remaining) > 0)
        else {
            node.children.push(CheckpointNode {
                path_fragment: remaining.to_string(),
                latest_seq: Some(state.latest_seq.seq),
                deleted: state.deleted,
                children: Vec::new(),
            });
            node.children
                .sort_by(|left, right| left.path_fragment.cmp(&right.path_fragment));
            return Ok(());
        };
        let common = common_prefix_len(&node.children[index].path_fragment, remaining);
        if common == node.children[index].path_fragment.len() {
            remaining = &remaining[common..];
            node = &mut node.children[index];
        } else {
            let mut old = node.children.remove(index);
            let suffix = old.path_fragment.split_off(common);
            let prefix = std::mem::replace(&mut old.path_fragment, suffix);
            let mut parent = CheckpointNode {
                path_fragment: prefix,
                latest_seq: None,
                deleted: None,
                children: vec![old],
            };
            remaining = &remaining[common..];
            if remaining.is_empty() {
                parent.latest_seq = Some(state.latest_seq.seq);
                parent.deleted = state.deleted;
            } else {
                parent.children.push(CheckpointNode {
                    path_fragment: remaining.to_string(),
                    latest_seq: Some(state.latest_seq.seq),
                    deleted: state.deleted,
                    children: Vec::new(),
                });
                parent
                    .children
                    .sort_by(|left, right| left.path_fragment.cmp(&right.path_fragment));
            }
            node.children.insert(index, parent);
            return Ok(());
        }
    }
    if node.latest_seq.is_some() {
        return Err(Error::Serialization(
            "checkpoint contains duplicate paths".into(),
        ));
    }
    node.latest_seq = Some(state.latest_seq.seq);
    node.deleted = state.deleted;
    Ok(())
}

/// Return the longest UTF-8-safe common prefix length.
fn common_prefix_len(left: &str, right: &str) -> usize {
    left.char_indices()
        .zip(right.char_indices())
        .take_while(|((_, left), (_, right))| left == right)
        .last()
        .map_or(0, |((index, ch), _)| index + ch.len_utf8())
}

/// Split ordered direct child ranges into bounded immutable chunks.
fn partition_children(
    children: Vec<CheckpointNode>,
    parent_path: &str,
    index: &mut usize,
    files: &mut Vec<(String, Vec<u8>)>,
    descriptors: &mut Vec<ChunkDescriptor>,
) -> Result<()> {
    let mut group = Vec::new();
    let mut group_nodes = 1;
    for mut child in children {
        let child_nodes = checkpoint_node_count(&child);
        if child_nodes + 1 > MAX_CHECKPOINT_NODES {
            flush_child_group(&mut group, parent_path, index, files, descriptors)?;
            let fragment = child.path_fragment.clone();
            let mut grandchildren = std::mem::take(&mut child.children);
            if child.latest_seq.is_some() {
                descriptors.push(encode_node(child, parent_path, false, index, files)?);
            }
            for grandchild in &mut grandchildren {
                grandchild.path_fragment.insert_str(0, &fragment);
            }
            partition_children(grandchildren, parent_path, index, files, descriptors)?;
        } else {
            if group_nodes + child_nodes > MAX_CHECKPOINT_NODES {
                flush_child_group(&mut group, parent_path, index, files, descriptors)?;
                group_nodes = 1;
            }
            group_nodes += child_nodes;
            group.push(child);
        }
    }
    flush_child_group(&mut group, parent_path, index, files, descriptors)
}

/// Encode one non-empty direct child range as a synthetic-root chunk.
fn flush_child_group(
    children: &mut Vec<CheckpointNode>,
    parent_path: &str,
    index: &mut usize,
    files: &mut Vec<(String, Vec<u8>)>,
    descriptors: &mut Vec<ChunkDescriptor>,
) -> Result<()> {
    if children.is_empty() {
        return Ok(());
    }
    let node = CheckpointNode {
        path_fragment: String::new(),
        latest_seq: None,
        deleted: None,
        children: std::mem::take(children),
    };
    descriptors.push(encode_node(node, parent_path, false, index, files)?);
    Ok(())
}

/// Encode one bounded radix subtree and derive its exact path range.
fn encode_node(
    node: CheckpointNode,
    root_path: &str,
    root: bool,
    index: &mut usize,
    files: &mut Vec<(String, Vec<u8>)>,
) -> Result<ChunkDescriptor> {
    let (count, first_path) = checkpoint_node_range(&node, root_path);
    let start_path = if root {
        None
    } else {
        Some(
            first_path
                .or_else(|| checkpoint_node_start_path(&node, root_path))
                .ok_or_else(|| {
                    Error::Serialization("checkpoint child chunk lacks start path".into())
                })?,
        )
    };
    let bytes = encode_checkpoint_chunk(&node)?;
    let checksum = sha256_hex(&bytes);
    let file = checkpoint_chunk_file_name(*index, &checksum);
    *index += 1;
    files.push((file.clone(), bytes));
    Ok(ChunkDescriptor {
        file,
        root_path: root_path.to_string(),
        start_path,
        end_path: None,
        file_state_count: count as u32,
        checksum,
        chunks: Vec::new(),
    })
}

/// Count nodes in one radix subtree.
fn checkpoint_node_count(root: &CheckpointNode) -> usize {
    let mut nodes = vec![root];
    let mut count = 0;
    while let Some(node) = nodes.pop() {
        count += 1;
        nodes.extend(&node.children);
    }
    count
}

/// Return the state count and first ordered path encoded in one chunk.
fn checkpoint_node_range(root: &CheckpointNode, root_path: &str) -> (usize, Option<String>) {
    let mut paths = Vec::new();
    collect_node_paths(root, root_path, &mut paths);
    (paths.len(), paths.first().cloned())
}

/// Return the lowest owned path prefix for one encoded checkpoint subtree.
fn checkpoint_node_start_path(node: &CheckpointNode, root_path: &str) -> Option<String> {
    let path = format!("{root_path}{}", node.path_fragment);
    if node.latest_seq.is_some() || !node.path_fragment.is_empty() {
        return Some(path);
    }
    node.children
        .first()
        .map(|child| format!("{path}{}", child.path_fragment))
}

/// Assign contiguous left-closed and right-open ownership ranges to siblings.
fn assign_checkpoint_child_ranges(
    children: &mut Vec<ChunkDescriptor>,
    start_path: &str,
    end_path: Option<&str>,
) -> Result<()> {
    if children.is_empty() {
        return Ok(());
    }
    children.sort_by(|left, right| left.start_path.cmp(&right.start_path));
    let starts = children
        .iter()
        .map(|child| {
            child.start_path.clone().ok_or_else(|| {
                Error::Serialization("checkpoint child chunk lacks start path".into())
            })
        })
        .collect::<Result<Vec<_>>>()?;
    for index in 0..children.len() {
        children[index].start_path = Some(if index == 0 {
            start_path.to_string()
        } else {
            starts[index].clone()
        });
        children[index].end_path = if index + 1 < starts.len() {
            Some(starts[index + 1].clone())
        } else {
            end_path.map(str::to_string)
        };
    }
    Ok(())
}

/// Collect state paths from one radix subtree in canonical order.
fn collect_node_paths(node: &CheckpointNode, parent: &str, paths: &mut Vec<String>) {
    let path = format!("{parent}{}", node.path_fragment);
    if node.latest_seq.is_some() {
        paths.push(path.clone());
    }
    for child in &node.children {
        collect_node_paths(child, &path, paths);
    }
}

/// Read and validate one published checkpoint reference.
async fn read_published(
    store: &MetadataStore,
    scope: &ScopeKey,
    latest: &LatestCheckpoint,
) -> Result<Vec<FileState>> {
    let manifest = read_manifest(store, scope, &latest.path).await?;
    if manifest.checkpoint_to_seq != latest.checkpoint_to_seq {
        return Err(Error::Serialization(
            "checkpoint pointer sequence mismatch".into(),
        ));
    }
    read_chunks(store, scope, &manifest, &latest.path).await
}

/// Read and validate one immutable checkpoint manifest.
async fn read_manifest(
    store: &MetadataStore,
    scope: &ScopeKey,
    candidate: &str,
) -> Result<CheckpointManifest> {
    let directory = checkpoint_directory(store, scope, candidate)?;
    let manifest: CheckpointManifest = store
        .read_json(&format!("{directory}/{MANIFEST_FILE}"))
        .await?;
    manifest.validate_for_scope(scope)?;
    if tree_checksum(&manifest.root)? != manifest.checksum {
        return Err(Error::Serialization(
            "checkpoint tree checksum mismatch".into(),
        ));
    }
    Ok(manifest)
}

/// Build the second-resolution checkpoint directory name used by new publications.
fn checkpoint_candidate_name(created_at_ns: u64) -> Result<String> {
    let seconds = i64::try_from(created_at_ns / 1_000_000_000)
        .map_err(|_| Error::internal("checkpoint timestamp overflow"))?;
    let datetime = DateTime::<Utc>::from_timestamp(seconds, 0)
        .ok_or_else(|| Error::internal("checkpoint timestamp is out of range"))?;
    Ok(format!(
        "{CHECKPOINT_DIR_PREFIX}{}/",
        datetime.format(CHECKPOINT_DIR_TIME_FORMAT)
    ))
}

/// Build a checkpoint chunk filename without embedding any sequence number.
fn checkpoint_chunk_file_name(index: usize, checksum: &str) -> String {
    format!("{CHECKPOINT_CHUNK_FILE_PREFIX}{index:06}-{checksum}{CHECKPOINT_CHUNK_FILE_EXTENSION}")
}

/// Rename descriptor file references while recording old-to-new chunk copies.
fn rename_checkpoint_chunk_files(
    descriptor: &mut ChunkDescriptor,
    index: &mut usize,
    copies: &mut Vec<(String, String, String)>,
) {
    let source = descriptor.file.clone();
    let candidate = checkpoint_chunk_file_name(*index, &descriptor.checksum);
    *index += 1;
    descriptor.file = candidate.clone();
    copies.push((source, candidate, descriptor.checksum.clone()));
    for child in &mut descriptor.chunks {
        rename_checkpoint_chunk_files(child, index, copies);
    }
}

/// Copy a published checkpoint manifest and referenced chunks into a candidate directory.
async fn copy_checkpoint_files(
    store: &MetadataStore,
    scope: &ScopeKey,
    source: &str,
    candidate: &str,
    manifest: &CheckpointManifest,
) -> Result<CheckpointManifest> {
    let source_directory = checkpoint_directory(store, scope, source)?;
    let candidate_directory = checkpoint_directory(store, scope, candidate)?;
    let mut candidate_manifest = manifest.clone();
    let mut copies = Vec::new();
    let mut index = 0;
    rename_checkpoint_chunk_files(&mut candidate_manifest.root, &mut index, &mut copies);
    candidate_manifest.checksum = tree_checksum(&candidate_manifest.root)?;
    candidate_manifest.validate_for_scope(scope)?;
    for (source_file, candidate_file, checksum) in copies {
        let bytes = store
            .read_bytes(&format!("{source_directory}/{source_file}"))
            .await?;
        if sha256_hex(&bytes) != checksum {
            return Err(Error::Serialization(
                "checkpoint chunk checksum mismatch".into(),
            ));
        }
        store
            .write_bytes(&format!("{candidate_directory}/{candidate_file}"), &bytes)
            .await?;
    }
    store
        .write_bytes(
            &format!("{candidate_directory}/{MANIFEST_FILE}"),
            &serde_json::to_vec(&candidate_manifest)?,
        )
        .await?;
    Ok(candidate_manifest)
}

/// Write checkpoint chunk bytes into one candidate directory.
async fn write_checkpoint_chunks(
    store: &MetadataStore,
    scope: &ScopeKey,
    candidate: &str,
    chunks: &[(String, Vec<u8>)],
) -> Result<()> {
    let directory = checkpoint_directory(store, scope, candidate)?;
    for (file, bytes) in chunks {
        store
            .write_bytes(&format!("{directory}/{file}"), bytes)
            .await?;
    }
    Ok(())
}

/// Read and validate one checkpoint chunk without reading its child descriptors.
async fn read_checkpoint_chunk_states(
    store: &MetadataStore,
    scope: &ScopeKey,
    directory: &str,
    descriptor: &ChunkDescriptor,
    root: bool,
) -> Result<Vec<FileState>> {
    let account_root = format!("{MULTIWRITE_MOUNT_PREFIX}/{}", scope.account_id);
    if descriptor.root_path != account_root {
        return Err(Error::Serialization(
            "checkpoint descriptor root mismatch".into(),
        ));
    }
    let bytes = store
        .read_bytes(&format!("{directory}/{}", descriptor.file))
        .await?;
    if sha256_hex(&bytes) != descriptor.checksum {
        return Err(Error::Serialization(
            "checkpoint chunk checksum mismatch".into(),
        ));
    }
    let node = decode_checkpoint_chunk(&bytes)?;
    if encode_checkpoint_chunk(&node)? != bytes {
        return Err(Error::Serialization(
            "checkpoint chunk is not canonical".into(),
        ));
    }
    let mut states = Vec::new();
    flatten_node(scope, &node, &descriptor.root_path, true, &mut states)?;
    if states.len() != descriptor.file_state_count as usize {
        return Err(Error::Serialization(
            "checkpoint chunk state count mismatch".into(),
        ));
    }
    if states.windows(2).any(|pair| pair[0].path >= pair[1].path)
        || states
            .iter()
            .any(|state| !is_canonical_account_path(&state.path, &account_root))
    {
        return Err(Error::Serialization(
            "checkpoint chunk paths are invalid or unordered".into(),
        ));
    }
    if !root {
        let (start, end) = checkpoint_descriptor_range(descriptor)?;
        if states
            .iter()
            .any(|state| !checkpoint_range_contains(start, end, &state.path))
        {
            return Err(Error::Serialization(
                "checkpoint chunk range mismatch".into(),
            ));
        }
    }
    Ok(states)
}

/// Merge old chunk states with replacement states that belong to one target range.
fn merge_checkpoint_states(
    states: Vec<FileState>,
    delta: &SegmentDelta,
    remaining: &mut BTreeMap<String, FileState>,
    target: ChunkMergeTarget<'_>,
) -> Vec<FileState> {
    let mut merged = states
        .into_iter()
        .filter(|state| !checkpoint_state_is_dirty(&state.path, delta))
        .map(|state| (state.path.clone(), state))
        .collect::<BTreeMap<_, _>>();
    let selected = remaining
        .keys()
        .filter(|path| checkpoint_target_contains(&target, path))
        .cloned()
        .collect::<Vec<_>>();
    for path in selected {
        if let Some(state) = remaining.remove(&path) {
            merged.insert(path, state);
        }
    }
    merged.into_values().collect()
}

/// Return whether one merge target owns a logical checkpoint path.
fn checkpoint_target_contains(target: &ChunkMergeTarget<'_>, path: &str) -> bool {
    match target {
        ChunkMergeTarget::All => true,
        ChunkMergeTarget::Range(start, end) => checkpoint_range_contains(start, *end, path),
        ChunkMergeTarget::RootOnly(root) => path == *root,
    }
}

/// Return whether a checkpoint state path is replaced or invalidated by a delta.
fn checkpoint_state_is_dirty(path: &str, delta: &SegmentDelta) -> bool {
    delta.exact_paths.contains(path)
        || delta
            .prefixes
            .iter()
            .any(|prefix| is_path_within(path, prefix))
}

/// Return whether a non-root chunk range intersects one segment delta.
fn checkpoint_descriptor_is_dirty(
    descriptor: &ChunkDescriptor,
    delta: &SegmentDelta,
) -> Result<bool> {
    let (start, end) = checkpoint_descriptor_range(descriptor)?;
    Ok(delta
        .exact_paths
        .iter()
        .any(|path| checkpoint_range_contains(start, end, path))
        || delta
            .states
            .keys()
            .any(|path| checkpoint_range_contains(start, end, path))
        || delta
            .prefixes
            .iter()
            .any(|prefix| checkpoint_range_overlaps_prefix(start, end, prefix)))
}

/// Return the validated left-closed and right-open range for a non-root descriptor.
fn checkpoint_descriptor_range(descriptor: &ChunkDescriptor) -> Result<(&str, Option<&str>)> {
    let start = descriptor
        .start_path
        .as_deref()
        .ok_or_else(|| Error::Serialization("checkpoint child chunk lacks start path".into()))?;
    let end = descriptor.end_path.as_deref();
    if let Some(end) = end {
        if start >= end {
            return Err(Error::Serialization(
                "checkpoint chunk range is reversed".into(),
            ));
        }
    }
    Ok((start, end))
}

/// Return whether a left-closed range contains one logical path.
fn checkpoint_range_contains(start: &str, end: Option<&str>, path: &str) -> bool {
    start <= path && end.is_none_or(|end| path < end)
}

/// Return whether one ownership range can contain paths under a dirty prefix.
fn checkpoint_range_overlaps_prefix(start: &str, end: Option<&str>, prefix: &str) -> bool {
    checkpoint_range_contains(start, end, prefix) || is_path_within(start, prefix)
}

/// Sort child descriptors and normalize contiguous ownership ranges.
fn sort_checkpoint_children(children: &mut Vec<ChunkDescriptor>, root_path: &str) -> Result<()> {
    assign_checkpoint_child_ranges(children, root_path, None)
}

/// Retain live states and tombstones still needed by at least one backend.
fn retain_checkpoint_states(states: Vec<FileState>, min_synced: Option<u64>) -> Vec<FileState> {
    states
        .into_iter()
        .filter(|state| {
            state.deleted.is_none()
                || min_synced.is_some_and(|synced| state.latest_seq.seq >= synced)
        })
        .collect()
}

/// Return every chunk descriptor reachable from a root descriptor.
fn checkpoint_chunk_descriptors(root: &ChunkDescriptor) -> Vec<&ChunkDescriptor> {
    let mut descriptors = Vec::new();
    let mut stack = vec![root];
    while let Some(descriptor) = stack.pop() {
        descriptors.push(descriptor);
        stack.extend(descriptor.chunks.iter());
    }
    descriptors
}

/// Return every non-empty chunk filename reachable from one descriptor tree.
fn checkpoint_chunk_files(root: &ChunkDescriptor) -> Vec<String> {
    checkpoint_chunk_descriptors(root)
        .into_iter()
        .filter_map(|descriptor| (!descriptor.file.is_empty()).then(|| descriptor.file.clone()))
        .collect()
}

/// Count chunk files already referenced by one descriptor tree.
fn checkpoint_chunk_count(root: &ChunkDescriptor) -> usize {
    checkpoint_chunk_files(root).len()
}

/// Best-effort delete candidate chunk files no longer referenced after a rewrite.
async fn remove_replaced_checkpoint_files(
    store: &MetadataStore,
    scope: &ScopeKey,
    candidate: &str,
    old_files: Vec<String>,
    new_root: &ChunkDescriptor,
) {
    let Ok(directory) = checkpoint_directory(store, scope, candidate) else {
        return;
    };
    let retained = checkpoint_chunk_files(new_root)
        .into_iter()
        .collect::<BTreeSet<_>>();
    for file in old_files {
        if retained.contains(&file) {
            continue;
        }
        if let Err(error) = store.remove_file(&format!("{directory}/{file}")).await {
            warn!(file = %file, error = %error, "checkpoint chunk cleanup failed");
        }
    }
}

/// Stream-validate a candidate checkpoint without materializing all file states.
async fn verify_checkpoint(
    store: &MetadataStore,
    scope: &ScopeKey,
    manifest: &CheckpointManifest,
    candidate: &str,
    tail: u64,
) -> Result<()> {
    if tree_checksum(&manifest.root)? != manifest.checksum {
        return Err(Error::Serialization(
            "checkpoint tree checksum mismatch".into(),
        ));
    }
    let directory = checkpoint_directory(store, scope, candidate)?;
    let mut descriptors = vec![(&manifest.root, true)];
    let mut previous_path: Option<String> = None;
    while let Some((descriptor, root)) = descriptors.pop() {
        let states =
            read_checkpoint_chunk_states(store, scope, &directory, descriptor, root).await?;
        for state in states {
            if state.latest_seq.seq > tail
                || previous_path
                    .as_ref()
                    .is_some_and(|previous| previous >= &state.path)
            {
                return Err(Error::Serialization(
                    "checkpoint candidate verification failed".into(),
                ));
            }
            previous_path = Some(state.path);
        }
        descriptors.extend(descriptor.chunks.iter().rev().map(|child| (child, false)));
    }
    Ok(())
}

/// Read every referenced chunk and reconstruct sorted file states.
async fn read_chunks(
    store: &MetadataStore,
    scope: &ScopeKey,
    manifest: &CheckpointManifest,
    candidate: &str,
) -> Result<Vec<FileState>> {
    let directory = checkpoint_directory(store, scope, candidate)?;
    let mut descriptors = vec![(&manifest.root, true)];
    let mut states = Vec::new();
    while let Some((descriptor, root)) = descriptors.pop() {
        states.extend(
            read_checkpoint_chunk_states(store, scope, &directory, descriptor, root).await?,
        );
        descriptors.extend(descriptor.chunks.iter().rev().map(|child| (child, false)));
    }
    if states.windows(2).any(|pair| pair[0].path >= pair[1].path) {
        return Err(Error::Serialization(
            "checkpoint paths are not strictly ordered".into(),
        ));
    }
    Ok(states)
}

/// Flatten one decoded checkpoint node into validated file states.
fn flatten_node(
    scope: &ScopeKey,
    node: &CheckpointNode,
    parent: &str,
    root: bool,
    states: &mut Vec<FileState>,
) -> Result<()> {
    if (!root || !node.path_fragment.is_empty()) && !valid_path_fragment(&node.path_fragment) {
        return Err(Error::Serialization(
            "checkpoint path fragment is not relative".into(),
        ));
    }
    let path = format!("{parent}{}", node.path_fragment);
    if let Some(seq) = node.latest_seq {
        let state = FileState {
            path: path.clone(),
            latest_seq: ScopedSeq {
                scope: scope.clone(),
                seq,
            },
            deleted: node.deleted,
        };
        state.validate(scope)?;
        states.push(state);
    }
    for child in &node.children {
        flatten_node(scope, child, &path, false, states)?;
    }
    Ok(())
}

/// Resolve one isolated checkpoint directory from its relative latest pointer.
fn checkpoint_directory(
    store: &MetadataStore,
    scope: &ScopeKey,
    candidate: &str,
) -> Result<String> {
    let name = candidate.strip_suffix('/').ok_or_else(|| {
        Error::Serialization("checkpoint pointer must reference a directory".into())
    })?;
    if !is_checkpoint_directory_name(name) {
        return Err(Error::Serialization(
            "checkpoint pointer directory is invalid".into(),
        ));
    }
    let root = store
        .paths()
        .checkpoints_dir(&scope.account_id, scope.partition_id)?
        .0;
    Ok(format!("{root}/{name}"))
}

/// Return whether one radix edge is a non-empty relative fragment.
fn valid_path_fragment(fragment: &str) -> bool {
    !fragment.is_empty() && !fragment.contains('\0')
}

/// Return whether a path is canonical and belongs to one account root.
fn is_canonical_account_path(path: &str, root: &str) -> bool {
    is_path_within(path, root)
        && !path.ends_with('/')
        && path
            .split('/')
            .skip(1)
            .all(|part| !part.is_empty() && part != "." && part != "..")
}

/// Hash the canonical serialized descriptor tree.
fn tree_checksum(root: &ChunkDescriptor) -> Result<String> {
    Ok(sha256_hex(&serde_json::to_vec(root)?))
}

/// Return whether a path equals a prefix or is one of its descendants.
fn is_path_within(path: &str, prefix: &str) -> bool {
    path == prefix
        || path
            .strip_prefix(prefix)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

/// Return the current Unix timestamp in nanoseconds.
fn now_ns() -> Result<u64> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| Error::internal(error.to_string()))?
        .as_nanos();
    u64::try_from(nanos).map_err(|_| Error::internal("checkpoint timestamp overflow"))
}

/// Preserve an operation result while requiring lease release to succeed.
fn finish_with_release<T>(operation: Result<T>, release: Result<()>) -> Result<T> {
    match (operation, release) {
        (Ok(value), Ok(())) => Ok(value),
        (Err(error), _) => Err(error),
        (Ok(_), Err(error)) => Err(error),
    }
}
