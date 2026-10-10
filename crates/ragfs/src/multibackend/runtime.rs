//! Foreground event queue and metadata flush worker for V2 multi-write.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;

use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;
use tracing::{debug, error};

use crate::core::context::{FsContextInner, FS_CTX};
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::multibackend::catch_up::{CatchUpTarget, CatchUpWorker};
use crate::multibackend::checkpoint::{CheckpointReader, CheckpointWorker};
use crate::multibackend::codec::{decode_segment, sha256_hex};
use crate::multibackend::constants::SYSTEM_DIR;
use crate::multibackend::meta::{MetadataStore, MultiWriteWorker};
use crate::multibackend::model::{
    DirectoryEvent, DirectoryOperation, MarkerPosition, PartitionContext, PartitionState,
    PartitionsManifest, PendingEvent, PendingEventKind, ScopeKey, SegmentEventType,
};
use crate::multibackend::provider::{bootstrap_account, MultiWriteProvider};
use crate::multibackend::router::AccountRouter;

/// Read active account ids from the encrypted or plain primary registry.
pub(crate) async fn read_active_accounts(primary: &dyn FileSystem) -> Result<HashSet<String>> {
    let context = Arc::new(
        FsContextInner::new(SYSTEM_DIR)
            .with_bypass_cache(true)
            .with_auto_pathlock_disabled(),
    );
    let bytes = match FS_CTX
        .scope(context, primary.read("/_system/accounts.json", 0, 0))
        .await
    {
        Ok(bytes) => bytes,
        Err(Error::NotFound(_)) => return Ok(HashSet::new()),
        Err(error) => return Err(error),
    };
    let value: serde_json::Value = serde_json::from_slice(&bytes)?;
    let accounts = value
        .get("accounts")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| Error::Serialization("accounts.json lacks an accounts object".into()))?;
    Ok(accounts
        .iter()
        .filter(|(_, account)| {
            account
                .get("deletion")
                .is_none_or(serde_json::Value::is_null)
        })
        .map(|(account_id, _)| account_id.clone())
        .collect())
}

/// Owns the non-blocking foreground sender and its background flush worker.
pub struct MultiWriteRuntime {
    sender: StdMutex<Option<mpsc::UnboundedSender<PendingEvent>>>,
    activation: watch::Sender<Option<bool>>,
    import_worker: StdMutex<Option<JoinHandle<Result<()>>>>,
    store: Arc<MetadataStore>,
    catch_up_cancelled: watch::Sender<bool>,
    catch_up_wake: Arc<tokio::sync::Notify>,
    running: Arc<AtomicBool>,
    pending_events: Arc<AtomicUsize>,
}

impl MultiWriteRuntime {
    /// Assemble runtime tasks without allowing background work to start.
    pub(crate) async fn prepare(
        store: Arc<MetadataStore>,
        provider: Arc<dyn MultiWriteProvider>,
        initial_partitions: u32,
        backup_names: Vec<String>,
        primary: Option<Arc<dyn FileSystem>>,
        targets: Vec<CatchUpTarget>,
        checkpoint_interval: Duration,
    ) -> Self {
        let (sender, receiver) = mpsc::unbounded_channel();
        let running = Arc::new(AtomicBool::new(true));
        let worker_running = running.clone();
        let pending_events = Arc::new(AtomicUsize::new(0));
        let worker_pending_events = pending_events.clone();
        let (activation, _) = watch::channel(None);
        let (catch_up_cancelled, _) = watch::channel(false);
        let catch_up_wake = Arc::new(tokio::sync::Notify::new());
        let flush_primary = primary.clone();
        let checkpoint_state = primary.as_ref().map(|primary| {
            Arc::new(CheckpointWorker::new(
                primary.clone(),
                store.clone(),
                provider.clone(),
            ))
        });
        let checkpoint_reader = Arc::new(CheckpointReader::new(store.clone()));
        if let Some(worker) = checkpoint_state {
            let cancellation = catch_up_cancelled.subscribe();
            let mut activated = activation.subscribe();
            drop(tokio::spawn(async move {
                if wait_for_start(&mut activated).await {
                    worker.run(checkpoint_interval, cancellation).await;
                }
            }));
        }
        let catch_up_workers = primary.map_or_else(Vec::new, |primary| {
            targets
                .into_iter()
                .map(|target| {
                    Arc::new(
                        CatchUpWorker::new(
                            target.backend_id,
                            primary.clone(),
                            target.backend,
                            store.clone(),
                            provider.clone(),
                        )
                        .with_checkpoint_consumer(checkpoint_reader.clone())
                        .with_cancellation(catch_up_cancelled.subscribe()),
                    )
                })
                .collect::<Vec<_>>()
        });
        let worker = FlushWorker::new(
            store.clone(),
            provider,
            initial_partitions,
            backup_names,
            flush_primary,
        );
        let mut activated = activation.subscribe();
        drop(tokio::spawn(async move {
            if wait_for_start(&mut activated).await {
                worker.recover_directory_events().await;
                if *activated.borrow() == Some(true) {
                    worker.run(receiver, worker_pending_events).await;
                }
            }
            worker_running.store(false, Ordering::SeqCst);
        }));
        for worker in catch_up_workers {
            let mut cancelled = catch_up_cancelled.subscribe();
            let wake = catch_up_wake.clone();
            let mut activated = activation.subscribe();
            drop(tokio::spawn(async move {
                if !wait_for_start(&mut activated).await {
                    return;
                }
                while !*cancelled.borrow() {
                    if let Err(error) = worker.run_once().await {
                        debug!(error = %error, "multi-write catch-up round failed");
                    }
                    tokio::select! {
                        _ = tokio::time::sleep(Duration::from_secs(300)) => {}
                        _ = wake.notified() => {}
                        changed = cancelled.changed() => {
                            if changed.is_err() || *cancelled.borrow() {
                                break;
                            }
                        }
                    }
                }
            }));
        }
        Self {
            sender: StdMutex::new(Some(sender)),
            activation,
            import_worker: StdMutex::new(None),
            store,
            catch_up_cancelled,
            catch_up_wake,
            running,
            pending_events,
        }
    }

    /// Attach the full-data import task to this runtime's lifecycle.
    pub(crate) fn set_import_worker(
        &self,
        worker: impl std::future::Future<Output = Result<()>> + Send + 'static,
    ) {
        let mut activated = self.activation.subscribe();
        let worker = tokio::spawn(async move {
            if wait_for_start(&mut activated).await {
                worker.await
            } else {
                Ok(())
            }
        });
        *self
            .import_worker
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = Some(worker);
    }

    /// Release prepared runtime work after the mount becomes visible.
    pub(crate) fn activate(&self) {
        self.activation.send_replace(Some(true));
    }

    /// Submit one event without waiting for metadata work or taking business locks.
    pub fn submit(&self, event: PendingEvent) {
        let failed = {
            let sender = self
                .sender
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            match sender.as_ref() {
                Some(sender) => {
                    self.pending_events.fetch_add(1, Ordering::Relaxed);
                    sender.send(event).err().map(|error| {
                        self.pending_events.fetch_sub(1, Ordering::Relaxed);
                        error.0
                    })
                }
                None => Some(event),
            }
        };
        if let Some(event) = failed {
            self.store.record_worker_error(MultiWriteWorker::Flush);
            error!(
                operation_id = %event.operation_id,
                account = %event.account_id,
                event_kind = ?event.kind,
                "multi-write event queue is closed"
            );
        }
        self.catch_up_wake.notify_waiters();
    }

    /// Return events still waiting in the foreground channel.
    pub(crate) fn pending_event_count(&self) -> usize {
        self.pending_events.load(Ordering::Relaxed)
    }

    /// Return whether the flush worker has not exited yet.
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }
}

impl Drop for MultiWriteRuntime {
    /// Signal background tasks when the owning wrapper is dropped.
    fn drop(&mut self) {
        self.sender
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .take();
        self.activation.send_replace(Some(false));
        if let Some(worker) = self
            .import_worker
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .take()
        {
            worker.abort();
        }
        self.catch_up_cancelled.send_replace(true);
        self.catch_up_wake.notify_waiters();
    }
}

/// Wait until a prepared runtime gate is started or stopped.
async fn wait_for_start(gate: &mut watch::Receiver<Option<bool>>) -> bool {
    gate.wait_for(|state| state.is_some())
        .await
        .is_ok_and(|state| *state == Some(true))
}

#[cfg(test)]
#[path = "tests/test_runtime.rs"]
mod tests;

/// Drains queued events and persists normal file events by account and scope.
pub struct FlushWorker {
    store: Arc<MetadataStore>,
    provider: Arc<dyn MultiWriteProvider>,
    primary: Option<Arc<dyn FileSystem>>,
    router: AccountRouter,
    initial_partitions: u32,
    backup_names: Vec<String>,
}

impl FlushWorker {
    /// Create a worker using one metadata store and persistence provider.
    pub fn new(
        store: Arc<MetadataStore>,
        provider: Arc<dyn MultiWriteProvider>,
        initial_partitions: u32,
        backup_names: Vec<String>,
        primary: Option<Arc<dyn FileSystem>>,
    ) -> Self {
        Self {
            router: AccountRouter::new(store.clone()),
            store,
            provider,
            primary,
            initial_partitions,
            backup_names,
        }
    }

    /// Drain the receiver until every sender is closed and queued event is consumed.
    pub async fn run(
        mut self,
        mut receiver: mpsc::UnboundedReceiver<PendingEvent>,
        pending_events: Arc<AtomicUsize>,
    ) {
        while let Some(first) = receiver.recv().await {
            let mut batch = vec![first];
            while let Ok(event) = receiver.try_recv() {
                batch.push(event);
            }
            let attempted = batch.len();
            self.flush_batch(batch).await;
            pending_events.fetch_sub(attempted, Ordering::Relaxed);
        }
    }

    /// Flush one batch while recording and dropping failed events.
    async fn flush_batch(&mut self, batch: Vec<PendingEvent>) {
        let mut failed = false;
        let deleted = batch
            .iter()
            .filter(|event| matches!(event.kind, PendingEventKind::DeleteAccount))
            .map(|event| event.account_id.clone())
            .collect::<HashSet<_>>();
        let mut accounts = HashMap::<String, Vec<PendingEvent>>::new();
        for event in batch {
            if matches!(event.kind, PendingEventKind::DeleteAccount) {
                continue;
            }
            if deleted.contains(&event.account_id) {
                continue;
            }
            accounts
                .entry(event.account_id.clone())
                .or_default()
                .push(event);
        }

        for (account, events) in accounts {
            match self.account_exists(&account).await {
                Ok(false) => continue,
                Err(error) => {
                    failed = true;
                    error!(account = %account, error = %error,
                        "multi-write primary account check failed");
                    continue;
                }
                Ok(true) => {}
            }
            match self
                .store
                .initialize_account(&account, self.initial_partitions, &self.backup_names)
                .await
            {
                Ok(partitions) => {
                    if let Err(error) = bootstrap_account(
                        self.provider.as_ref(),
                        self.store.as_ref(),
                        &account,
                        &partitions,
                    )
                    .await
                    {
                        failed = true;
                        error!(account = %account, error = %error,
                            "multi-write provider bootstrap failed");
                        continue;
                    }
                }
                Err(error) => {
                    failed = true;
                    error!(
                        account = %account,
                        error = %error,
                        "multi-write account initialization failed"
                    );
                    continue;
                }
            }

            let mut groups = HashMap::<ScopeKey, (PartitionContext, Vec<PendingEvent>)>::new();
            for mut event in events {
                match event.kind {
                    PendingEventKind::Data(SegmentEventType::Write | SegmentEventType::Remove) => {
                        match self.router.route(&account, &event.path).await {
                            Ok(route) => {
                                event.route_hint = Some(route.clone());
                                groups
                                    .entry(route.scope.clone())
                                    .or_insert_with(|| (route, Vec::new()))
                                    .1
                                    .push(event);
                            }
                            Err(error) => {
                                failed = true;
                                error!(
                                    operation_id = %event.operation_id,
                                    account = %account,
                                    event_kind = ?event.kind,
                                    error = %error,
                                    "multi-write event routing failed"
                                );
                            }
                        }
                    }
                    PendingEventKind::Data(
                        SegmentEventType::RemoveTree | SegmentEventType::MoveTree,
                    ) => {
                        if let Err(error) = self.flush_directory_event(&account, &event).await {
                            failed = true;
                            error!(
                                operation_id = %event.operation_id,
                                account = %account,
                                event_kind = ?event.kind,
                                error = %error,
                                "multi-write directory event persistence failed"
                            );
                        }
                    }
                    PendingEventKind::DeleteAccount => unreachable!(),
                }
            }

            for (scope, (route, events)) in groups {
                if let Err(error) = self.provider.flush(Some(&route), events).await {
                    failed = true;
                    error!(
                        account = %account,
                        partition = scope.partition_id,
                        epoch = scope.epoch,
                        error = %error,
                        "multi-write metadata flush failed"
                    );
                }
            }
        }
        if failed {
            self.store.record_worker_error(MultiWriteWorker::Flush);
        }
    }

    /// Check account membership through the user registry when the primary view is available.
    async fn account_exists(&self, account_id: &str) -> Result<bool> {
        let Some(primary) = &self.primary else {
            return self.store.account_exists(account_id).await;
        };
        Ok(read_active_accounts(primary.as_ref())
            .await?
            .contains(account_id))
    }

    /// Register and persist one directory marker to every captured stable partition.
    async fn flush_directory_event(&self, account: &str, event: &PendingEvent) -> Result<()> {
        let scopes = self.register_directory_event(account, event).await?;
        let mut failed = false;
        for scope in scopes {
            let result = match self.find_or_append_marker(&scope, event).await {
                Ok(position) => {
                    self.backfill_position(account, event.operation_id, position)
                        .await
                }
                Err(error) => Err(error),
            };
            if let Err(error) = result {
                failed = true;
                error!(
                    operation_id = %event.operation_id, account = %account,
                    partition = scope.partition_id, epoch = scope.epoch, error = %error,
                    "multi-write directory marker scope remains incomplete"
                );
            }
        }
        if failed {
            Err(Error::internal("directory marker scopes remain incomplete"))
        } else {
            Ok(())
        }
    }

    /// Register one operation idempotently and return its missing stable scopes.
    async fn register_directory_event(
        &self,
        account: &str,
        event: &PendingEvent,
    ) -> Result<Vec<ScopeKey>> {
        let persisted = Self::directory_event(event)?;
        self.store
            .update_partitions_manifest(account, move |manifest| {
                let index = manifest
                    .directory_events
                    .iter()
                    .position(|candidate| candidate.op_id == persisted.op_id);
                if let Some(index) = index {
                    let existing = &manifest.directory_events[index];
                    if existing.operation != persisted.operation
                        || existing.source_path != persisted.source_path
                        || existing.destination_path != persisted.destination_path
                    {
                        return Err(Error::invalid_operation(
                            "directory op_id was reused with different content",
                        ));
                    }
                } else {
                    manifest.directory_events.push(persisted);
                }
                let event = manifest
                    .directory_events
                    .iter()
                    .find(|candidate| candidate.op_id == event.operation_id)
                    .unwrap();
                Ok(Self::missing_scopes(account, manifest, &event.positions))
            })
            .await
    }

    /// Return Stable scopes that do not yet have a marker position.
    fn missing_scopes(
        account: &str,
        manifest: &PartitionsManifest,
        positions: &[MarkerPosition],
    ) -> Vec<ScopeKey> {
        let completed = positions
            .iter()
            .map(|position| position.partition_id)
            .collect::<HashSet<_>>();
        manifest
            .partitions
            .iter()
            .filter(|(id, partition)| {
                partition.state == PartitionState::Stable && !completed.contains(id)
            })
            .map(|(&partition_id, _)| ScopeKey {
                account_id: account.to_string(),
                partition_id,
                epoch: manifest.epoch,
            })
            .collect()
    }

    /// Convert one queued directory marker into its persisted notification.
    fn directory_event(event: &PendingEvent) -> Result<DirectoryEvent> {
        let operation = match event.kind {
            PendingEventKind::Data(SegmentEventType::RemoveTree) => DirectoryOperation::RemoveTree,
            PendingEventKind::Data(SegmentEventType::MoveTree) => DirectoryOperation::MoveTree,
            _ => {
                return Err(Error::invalid_operation(
                    "directory notification requires a tree event",
                ))
            }
        };
        Ok(DirectoryEvent {
            op_id: event.operation_id,
            operation,
            source_path: event.path.clone(),
            destination_path: event.destination_path.clone(),
            positions: Vec::new(),
        })
    }

    /// Idempotently persist one marker position under the manifest Exact lock.
    async fn backfill_position(
        &self,
        account: &str,
        operation_id: uuid::Uuid,
        position: MarkerPosition,
    ) -> Result<()> {
        self.store
            .update_partitions_manifest(account, move |manifest| {
                let event = manifest
                    .directory_events
                    .iter_mut()
                    .find(|event| event.op_id == operation_id)
                    .ok_or_else(|| Error::not_found(operation_id.to_string()))?;
                if let Some(existing) = event
                    .positions
                    .iter()
                    .find(|existing| existing.partition_id == position.partition_id)
                {
                    if existing != &position {
                        return Err(Error::invalid_operation(
                            "conflicting directory marker positions",
                        ));
                    }
                    return Ok(());
                }
                event.positions.push(position);
                event
                    .positions
                    .sort_by_key(|position| position.partition_id);
                Ok(())
            })
            .await
    }

    /// Recover every incomplete notification discoverable before queue processing starts.
    async fn recover_directory_events(&self) {
        let accounts = match self.store.initialized_accounts().await {
            Ok(accounts) => accounts,
            Err(error) => {
                self.store.record_worker_error(MultiWriteWorker::Flush);
                error!(error = %error, "multi-write directory recovery discovery failed");
                return;
            }
        };
        for account in accounts {
            match self.recover_account(&account).await {
                Ok(()) | Err(Error::NotFound(_)) => {}
                Err(error) => {
                    self.store.record_worker_error(MultiWriteWorker::Flush);
                    error!(account = %account, error = %error,
                        "multi-write directory recovery failed");
                }
            }
        }
    }

    /// Recover all currently missing marker positions for one account.
    async fn recover_account(&self, account: &str) -> Result<()> {
        let manifest_path = self.store.paths().account_manifest(account)?.0;
        let manifest: PartitionsManifest = self.store.read_json(&manifest_path).await?;
        manifest.validate()?;
        let mut failed = false;
        for persisted in &manifest.directory_events {
            let event = PendingEvent {
                operation_id: persisted.op_id,
                account_id: account.to_string(),
                kind: PendingEventKind::Data(match persisted.operation {
                    DirectoryOperation::RemoveTree => SegmentEventType::RemoveTree,
                    DirectoryOperation::MoveTree => SegmentEventType::MoveTree,
                }),
                path: persisted.source_path.clone(),
                destination_path: persisted.destination_path.clone(),
                route_hint: None,
            };
            for scope in Self::missing_scopes(account, &manifest, &persisted.positions) {
                let result = self.find_or_append_marker(&scope, &event).await;
                match result {
                    Ok(position) => {
                        if let Err(error) = self
                            .backfill_position(account, event.operation_id, position)
                            .await
                        {
                            failed = true;
                            error!(operation_id = %event.operation_id, account = %account,
                                partition = scope.partition_id, error = %error,
                                "multi-write recovered marker position update failed");
                        }
                    }
                    Err(error) => {
                        failed = true;
                        error!(operation_id = %event.operation_id, account = %account,
                            partition = scope.partition_id, error = %error,
                            "multi-write directory marker recovery scope failed");
                    }
                }
            }
        }
        if failed {
            Err(Error::internal(
                "directory marker recovery remains incomplete",
            ))
        } else {
            Ok(())
        }
    }

    /// Reuse a retained sealed marker or append it when none exists.
    async fn find_or_append_marker(
        &self,
        scope: &ScopeKey,
        event: &PendingEvent,
    ) -> Result<MarkerPosition> {
        match self.find_marker(scope, event).await? {
            Some(position) => Ok(position),
            None => self.provider.append_marker(scope, event).await,
        }
    }

    /// Find one operation marker in currently retained sealed segments.
    async fn find_marker(
        &self,
        scope: &ScopeKey,
        event: &PendingEvent,
    ) -> Result<Option<MarkerPosition>> {
        let manifest = self.provider.read_manifest(scope).await?;
        let directory = self
            .store
            .paths()
            .segments_dir(&scope.account_id, scope.partition_id)?
            .0;
        for descriptor in manifest
            .segments
            .iter()
            .filter(|segment| segment.segment_to_seq.is_some())
        {
            let bytes = self
                .store
                .read_bytes(&format!("{directory}/{}", descriptor.path))
                .await?;
            if bytes.len() as u64 != descriptor.byte_len
                || descriptor.checksum.as_deref() != Some(&sha256_hex(&bytes))
            {
                return Err(Error::Serialization(
                    "sealed marker segment mismatch".to_string(),
                ));
            }
            let decoded = decode_segment(&bytes)?;
            if decoded.truncated_tail || decoded.valid_bytes != descriptor.byte_len {
                return Err(Error::Serialization(
                    "sealed marker segment is truncated".to_string(),
                ));
            }
            if let Some(record) = decoded
                .records
                .iter()
                .find(|record| record.op_id == Some(event.operation_id))
            {
                let PendingEventKind::Data(kind) = event.kind else {
                    unreachable!()
                };
                if record.event_type != kind
                    || record.path != event.path
                    || record.destination_path != event.destination_path
                {
                    return Err(Error::Serialization(
                        "directory op_id marker mismatch".to_string(),
                    ));
                }
                return Ok(Some(MarkerPosition {
                    partition_id: scope.partition_id,
                    epoch: scope.epoch,
                    seq: record.seq,
                }));
            }
        }
        Ok(None)
    }
}
