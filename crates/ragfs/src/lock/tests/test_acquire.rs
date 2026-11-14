use super::*;

    struct ConcurrentParentCreationFs {
        inner: Arc<MemFileSystem>,
        shared_parent: String,
        initial_missing_stats: AtomicUsize,
        initial_stat_barrier: Barrier,
        shared_parent_mkdir_attempts: AtomicUsize,
    }

    impl ConcurrentParentCreationFs {
        fn new(inner: Arc<MemFileSystem>, shared_parent: &str) -> Self {
            Self {
                inner,
                shared_parent: shared_parent.to_string(),
                initial_missing_stats: AtomicUsize::new(0),
                initial_stat_barrier: Barrier::new(2),
                shared_parent_mkdir_attempts: AtomicUsize::new(0),
            }
        }
    }

    #[async_trait]
    impl FileSystem for ConcurrentParentCreationFs {
        async fn create(&self, path: &str) -> FsResult<()> {
            self.inner.create(path).await
        }

        async fn mkdir(&self, path: &str, mode: u32) -> FsResult<()> {
            if path == self.shared_parent {
                self.shared_parent_mkdir_attempts
                    .fetch_add(1, Ordering::SeqCst);
            }
            self.inner.mkdir(path, mode).await
        }

        async fn remove(&self, path: &str) -> FsResult<()> {
            self.inner.remove(path).await
        }

        async fn remove_all(&self, path: &str) -> FsResult<()> {
            self.inner.remove_all(path).await
        }

        async fn read(&self, path: &str, offset: u64, size: u64) -> FsResult<Vec<u8>> {
            self.inner.read(path, offset, size).await
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            offset: u64,
            flags: WriteFlag,
        ) -> FsResult<u64> {
            self.inner.write(path, data, offset, flags).await
        }

        async fn read_dir(
            &self,
            path: &str,
            offset: Option<usize>,
            limit: Option<usize>,
            sort_by: Option<crate::core::ListSortBy>,
            sort_order: Option<crate::core::SortOrder>,
        ) -> FsResult<Vec<FileInfo>> {
            self.inner
                .read_dir(path, offset, limit, sort_by, sort_order)
                .await
        }

        async fn stat(&self, path: &str) -> FsResult<FileInfo> {
            if path == self.shared_parent
                && self.initial_missing_stats.fetch_add(1, Ordering::SeqCst) < 2
            {
                self.initial_stat_barrier.wait().await;
                return Err(Error::not_found(path));
            }
            self.inner.stat(path).await
        }

        async fn rename(&self, old_path: &str, new_path: &str) -> FsResult<()> {
            self.inner.rename(old_path, new_path).await
        }

        async fn chmod(&self, path: &str, mode: u32) -> FsResult<()> {
            self.inner.chmod(path, mode).await
        }
    }

    /// Alternate conflicts before acquisition and during post-verification.
    ///
    /// Every call to `try_create_token` is one real acquire-loop probe. Odd
    /// probes fail there with Busy; even probes create a token, then the next
    /// read exposes a synthetic ancestor Tree token so post-verification fails.
    struct RetryLoopCountingProvider {
        inner: crate::lock::provider::MemoryPathLockProvider,
        create_probes: AtomicUsize,
        pre_acquire_conflicts: AtomicUsize,
        post_verify_conflicts: AtomicUsize,
        inject_post_verify_conflict: AtomicBool,
    }

    impl RetryLoopCountingProvider {
        fn new() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                create_probes: AtomicUsize::new(0),
                pre_acquire_conflicts: AtomicUsize::new(0),
                post_verify_conflicts: AtomicUsize::new(0),
                inject_post_verify_conflict: AtomicBool::new(false),
            }
        }
    }

    #[async_trait]
    impl PathLockProvider for RetryLoopCountingProvider {
        async fn compare_and_remove_token(
            &self,
            path: &str,
            expected: &LockToken,
        ) -> PathLockResult<bool> {
            self.inner.compare_and_remove_token(path, expected).await
        }
        fn name(&self) -> &'static str {
            "retry-loop-counting"
        }

        async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
            if self
                .inject_post_verify_conflict
                .swap(false, Ordering::SeqCst)
            {
                self.post_verify_conflicts.fetch_add(1, Ordering::SeqCst);
                return Ok(Some(LockToken {
                    holders: Vec::new(),
                    owner_id: "post-verify-blocker".to_string(),
                    time_ns: PathLockManager::now_ns(),
                    lock_type: PathLockKind::Tree,
                }));
            }
            self.inner.read_token(lock_path).await
        }

        async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
            let probe = self.create_probes.fetch_add(1, Ordering::SeqCst);
            if probe.is_multiple_of(2) {
                self.pre_acquire_conflicts.fetch_add(1, Ordering::SeqCst);
                return Err(PathLockError::Busy {
                    lock_path: lock_path.to_string(),
                    operation: "injected pre-acquire conflict".to_string(),
                });
            }

            self.inner.try_create_token(lock_path, token).await?;
            self.inject_post_verify_conflict
                .store(true, Ordering::SeqCst);
            Ok(())
        }

        async fn compare_and_write_token(
            &self,
            lock_path: &str,
            expected: &LockToken,
            replacement: &LockToken,
        ) -> PathLockResult<bool> {
            self.inner
                .compare_and_write_token(lock_path, expected, replacement)
                .await
        }

        async fn refresh_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            time_ns: u128,
        ) -> PathLockResult<bool> {
            self.inner.refresh_token(lock_path, owner_id, time_ns).await
        }

        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            force: bool,
        ) -> PathLockResult<bool> {
            self.inner.remove_token(lock_path, owner_id, force).await
        }

        async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
            self.inner.scan_descendant_locks(root).await
        }
    }

    #[tokio::test]
    async fn exact_locks_tolerate_concurrent_parent_directory_creation() {
        let inner = Arc::new(MemFileSystem::new());
        inner.mkdir("/local", 0o755).await.unwrap();
        inner.mkdir("/local/default", 0o755).await.unwrap();
        inner
            .mkdir("/local/default/resources", 0o755)
            .await
            .unwrap();
        let shared_parent = "/local/default/resources/shared";
        let fs = Arc::new(ConcurrentParentCreationFs::new(
            inner.clone(),
            shared_parent,
        ));
        let provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            30.0,
        ));
        let first_manager =
            PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
        let second_manager = PathLockManager::new(fs.clone(), provider, PathLockConfig::default());
        let first_path = format!("{shared_parent}/a.md");
        let second_path = format!("{shared_parent}/b.md");

        let (first, second) = tokio::join!(
            first_manager.acquire_exact(&first_path, Duration::ZERO, None),
            second_manager.acquire_exact(&second_path, Duration::ZERO, None),
        );

        assert!(first.is_ok(), "first lock failed: {first:?}");
        assert!(second.is_ok(), "second lock failed: {second:?}");
        assert_eq!(fs.shared_parent_mkdir_attempts.load(Ordering::SeqCst), 2);
        assert!(inner.stat(shared_parent).await.unwrap().is_dir);
    }

    #[tokio::test]
    async fn acquire_exact_reentrant_requires_local_owned_capability() {
        let mgr = make_manager().await;
        let lease1 = mgr
            .acquire_exact("/data/file.txt", Duration::from_secs(1), None)
            .await
            .unwrap();
        let capability = (
            lease1.lease.lease_ref.as_str(),
            lease1.ownership_ref.as_str(),
        );
        let lease2 = mgr
            .acquire_exact("/data/file.txt", Duration::from_secs(1), Some(capability))
            .await
            .unwrap();
        assert_eq!(lease1.lease.lock_paths, lease2.lease.lock_paths);

        mgr.release(&lease2).await.unwrap();
        assert!(matches!(
            mgr.acquire_exact("/data/file.txt", Duration::ZERO, None)
                .await,
            Err(PathLockError::Timeout { .. })
        ));

        mgr.release(&lease1).await.unwrap();
        assert!(mgr
            .acquire_exact("/data/file.txt", Duration::ZERO, None)
            .await
            .is_ok());
    }

    #[tokio::test]
    async fn failed_batch_rolls_back_same_owner_tree_upgrade() {
        let mgr = make_manager().await;
        let exact = mgr
            .acquire_exact("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        let blocker = mgr
            .acquire_exact("/data/z.txt", Duration::ZERO, None)
            .await
            .unwrap();
        let capability = (exact.lease.lease_ref.as_str(), exact.ownership_ref.as_str());

        let result = mgr
            .acquire_batch(
                &[
                    PathLockRequest {
                        path: "/data/sub".to_string(),
                        kind: PathLockKind::Tree,
                    },
                    PathLockRequest {
                        path: "/data/z.txt".to_string(),
                        kind: PathLockKind::Exact,
                    },
                ],
                Duration::ZERO,
                Some(capability),
            )
            .await;

        assert!(matches!(result, Err(PathLockError::Timeout { .. })));
        let token = mgr
            .provider
            .read_token(&exact.lease.lock_paths[0])
            .await
            .unwrap()
            .unwrap();
        assert_eq!(token.lock_type, PathLockKind::Exact);

        mgr.release(&blocker).await.unwrap();
        mgr.release(&exact).await.unwrap();
    }

    #[tokio::test]
    async fn missing_exact_and_same_path_tree_conflict_in_both_orders() {
        let (mgr, _) = make_manager_with_fs().await;
        let exact = mgr
            .acquire_exact("/data/new", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(matches!(
            mgr.acquire_tree("/data/new", Duration::ZERO, None).await,
            Err(PathLockError::Timeout { .. })
        ));
        mgr.release(&exact).await.unwrap();

        let tree = mgr
            .acquire_tree("/data/other", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(matches!(
            mgr.acquire_exact("/data/other", Duration::ZERO, None).await,
            Err(PathLockError::Timeout { .. })
        ));
        mgr.release(&tree).await.unwrap();
    }

    #[tokio::test]
    async fn filesystem_lock_relationship_matrix() {
        struct Case {
            name: &'static str,
            held_path: &'static str,
            held_kind: PathLockKind,
            requested_path: &'static str,
            requested_kind: PathLockKind,
            conflicts: bool,
        }

        let cases = [
            Case {
                name: "ancestor tree blocks descendant exact",
                held_path: "/data/a",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/b",
                requested_kind: PathLockKind::Exact,
                conflicts: true,
            },
            Case {
                name: "descendant exact blocks ancestor tree",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Exact,
                requested_path: "/data/a",
                requested_kind: PathLockKind::Tree,
                conflicts: true,
            },
            Case {
                name: "ancestor tree blocks descendant tree",
                held_path: "/data/a",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/b",
                requested_kind: PathLockKind::Tree,
                conflicts: true,
            },
            Case {
                name: "descendant tree blocks ancestor tree",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a",
                requested_kind: PathLockKind::Tree,
                conflicts: true,
            },
            Case {
                name: "ancestor exact allows descendant exact",
                held_path: "/data/a",
                held_kind: PathLockKind::Exact,
                requested_path: "/data/a/b",
                requested_kind: PathLockKind::Exact,
                conflicts: false,
            },
            Case {
                name: "descendant exact allows ancestor exact",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Exact,
                requested_path: "/data/a",
                requested_kind: PathLockKind::Exact,
                conflicts: false,
            },
            Case {
                name: "sibling tree allows sibling exact",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/c",
                requested_kind: PathLockKind::Exact,
                conflicts: false,
            },
            Case {
                name: "sibling tree allows sibling tree",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/c",
                requested_kind: PathLockKind::Tree,
                conflicts: false,
            },
        ];

        for case in cases {
            let (holder, contender) = make_filesystem_managers().await;
            let held = acquire_kind(&holder, case.held_path, case.held_kind)
                .await
                .unwrap_or_else(|error| {
                    panic!("{}: failed to acquire held lock: {error}", case.name)
                });
            let requested =
                acquire_kind(&contender, case.requested_path, case.requested_kind).await;

            if case.conflicts {
                assert!(
                    matches!(requested, Err(PathLockError::Timeout { .. })),
                    "{}: expected conflict, got {requested:?}",
                    case.name
                );
            } else {
                let requested = requested
                    .unwrap_or_else(|error| panic!("{}: unexpected conflict: {error}", case.name));
                contender.release(&requested).await.unwrap();
            }
            holder.release(&held).await.unwrap();
        }
    }

    #[tokio::test]
    async fn legacy_tree_token_still_blocks_descendant_acquisition() {
        let (mgr, _) = make_filesystem_managers().await;
        mgr.resolver.fs.mkdir("/data/sub", 0o755).await.unwrap();
        mgr.provider
            .try_create_token(
                "/data/sub/.path.ovlock",
                &LockToken {
                    holders: Vec::new(),
                    owner_id: "legacy-owner".to_string(),
                    time_ns: PathLockManager::now_ns(),
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();

        assert!(matches!(
            mgr.acquire_exact("/data/sub/file.txt", Duration::ZERO, None)
                .await,
            Err(PathLockError::Timeout { .. })
        ));
    }

    #[tokio::test]
    async fn provider_io_error_is_not_retried() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_read_failure());
        let mgr = PathLockManager::new(fs, provider, PathLockConfig::default());

        assert!(matches!(
            mgr.acquire_exact("/data/file.txt", Duration::ZERO, None)
                .await,
            Err(PathLockError::Io(message)) if message == "injected read failure"
        ));
    }

    #[tokio::test]
    async fn busy_stale_takeover_is_retried() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_busy_takeover());
        let mgr = PathLockManager::new(
            fs,
            provider.clone(),
            PathLockConfig {
                lock_expire_secs: 1.0,
                ..PathLockConfig::default()
            },
        );
        let lock_path = mgr
            .resolver
            .resolve_tree_lock_path("/data/file.txt")
            .await
            .unwrap();
        provider
            .inner
            .try_create_token(
                &lock_path,
                &LockToken {
                    holders: Vec::new(),
                    owner_id: "stale-owner".to_string(),
                    time_ns: 1,
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();

        let lease = mgr
            .acquire_tree("/data/file.txt", Duration::from_millis(200), None)
            .await
            .unwrap();

        assert_eq!(provider.busy_takeover_count.load(Ordering::SeqCst), 1);
        mgr.release(&lease).await.unwrap();
        let error = PathLockManager::rollback_error(
            PathLockError::Io("commit".into()),
            PathLockError::Io("rollback".into()),
        );
        assert!(!PathLockManager::is_retryable_error(&error));
    }

    #[tokio::test]
    async fn stale_takeover_preserves_a_concurrent_refresh() {
        for (refresh, timeout) in [
            (false, Duration::ZERO),
            (true, Duration::ZERO),
            (true, Duration::from_millis(200)),
        ] {
            let fs = Arc::new(MemFileSystem::new());
            fs.mkdir("/data", 0o755).await.unwrap();
            let provider = Arc::new(FailNextRemoveProvider {
                reject_compare: false,
                refresh_on_takeover: refresh,
                ..FailNextRemoveProvider::with_compare_miss()
            });
            let mgr = PathLockManager::new(fs, provider.clone(), PathLockConfig::default());
            let lock_path = mgr.resolver.resolve_tree_lock_path("/data").await.unwrap();
            provider
                .inner
                .try_create_token(
                    &lock_path,
                    &LockToken {
                        holders: Vec::new(),
                        owner_id: "original-owner".to_string(),
                        time_ns: 1,
                        lock_type: PathLockKind::Tree,
                    },
                )
                .await
                .unwrap();

            let result = mgr.acquire_tree("/data", timeout, None).await;
            let token = provider
                .inner
                .read_token(&lock_path)
                .await
                .unwrap()
                .unwrap();
            assert!(token.time_ns > 1);
            if refresh {
                // The heartbeat wins after the stale read but before takeover.
                // Both the renewed token and its ownership must survive.
                assert!(matches!(result, Err(PathLockError::Timeout { .. })));
                assert_eq!(token.owner_id, "original-owner");
            } else {
                // A genuinely stale token remains reclaimable without waiting.
                let lease = result.unwrap();
                assert_eq!(token.owner_id, lease.lease.owner_id);
                mgr.release(&lease).await.unwrap();
            }
        }
    }

    #[tokio::test]
    async fn contending_waiters_eventually_acquire_with_backoff() {
        let mgr = Arc::new(make_manager().await);
        let holder = mgr
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();

        let mut waiters = Vec::new();
        for _ in 0..4 {
            let waiter_mgr = mgr.clone();
            waiters.push(tokio::spawn(async move {
                let lease = waiter_mgr
                    .acquire_tree("/data/sub", Duration::from_secs(5), None)
                    .await?;
                waiter_mgr.release(&lease).await
            }));
        }

        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if mgr.metrics_snapshot().await.waiting_lock_count == 4 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("all waiters should enter the conflict/backoff path");
        mgr.release(&holder).await.unwrap();

        for waiter in waiters {
            tokio::time::timeout(Duration::from_secs(5), waiter)
                .await
                .expect("waiter should complete after holder release")
                .unwrap()
                .unwrap();
        }
        let mut stale_metrics = mgr.metrics.write().await;
        stale_metrics.waiting_lock_count = usize::MAX;
        drop(stale_metrics);
        let metrics = mgr.metrics_snapshot().await;
        assert_eq!(metrics.waiting_lock_count, 0);
    }

    #[test]
    fn poll_interval_bounds_back_off_and_saturate() {
        assert_eq!(PathLockManager::poll_interval_bounds(0), (50, 60));
        assert_eq!(PathLockManager::poll_interval_bounds(1), (60, 90));
        assert_eq!(PathLockManager::poll_interval_bounds(2), (91, 135));
        assert_eq!(PathLockManager::poll_interval_bounds(3), (136, 204));
        assert_eq!(PathLockManager::poll_interval_bounds(4), (204, 306));
        assert_eq!(PathLockManager::poll_interval_bounds(5), (307, 459));
        assert_eq!(PathLockManager::poll_interval_bounds(6), (400, 500));
        assert_eq!(PathLockManager::poll_interval_bounds(u32::MAX), (400, 500));
    }

    #[test]
    fn poll_interval_samples_stay_within_deterministic_bounds() {
        for attempt in [0, 1, 2, 3, 4, 5, 6, 50, u32::MAX] {
            let (lower_ms, upper_ms) = PathLockManager::poll_interval_bounds(attempt);
            for _ in 0..100 {
                let ms = PathLockManager::poll_interval_for_attempt(attempt).as_millis() as u64;
                assert!(
                    (lower_ms..=upper_ms).contains(&ms),
                    "attempt {attempt} produced {ms}ms outside [{lower_ms}, {upper_ms}]"
                );
            }
        }
    }

    #[test]
    fn retry_delay_does_not_overshoot_remaining_timeout() {
        let remaining = Duration::from_millis(7);
        assert_eq!(PathLockManager::retry_delay(0, remaining), remaining);
        assert_eq!(PathLockManager::retry_delay(u32::MAX, remaining), remaining);
    }

    #[tokio::test]
    async fn real_retry_loop_stays_within_ten_second_probe_budget() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(RetryLoopCountingProvider::new());
        let mgr = PathLockManager::new(
            fs,
            provider.clone(),
            PathLockConfig {
                lock_expire_secs: 60.0,
                ..PathLockConfig::default()
            },
        );
        let requests = [PathLockRequest {
            path: "/data/file.txt".to_string(),
            kind: PathLockKind::Exact,
        }];

        let result = tokio::time::timeout(
            Duration::from_secs(12),
            mgr.acquire_batch(&requests, Duration::from_secs(10), None),
        )
        .await
        .expect("acquire loop should honor its ten-second timeout");

        assert!(matches!(result, Err(PathLockError::Timeout { .. })));
        let total_probes = provider.create_probes.load(Ordering::SeqCst);
        let retry_probes = total_probes.saturating_sub(1);
        let pre_acquire_conflicts = provider.pre_acquire_conflicts.load(Ordering::SeqCst);
        let post_verify_conflicts = provider.post_verify_conflicts.load(Ordering::SeqCst);
        assert!(
            retry_probes <= 29,
            "ten-second acquire exceeded retry probe budget: total={total_probes}, retries={retry_probes}"
        );
        assert!(
            pre_acquire_conflicts > 0,
            "test must exercise the acquisition conflict branch"
        );
        assert!(
            post_verify_conflicts > 0,
            "test must exercise the post-verification conflict branch"
        );
        assert_eq!(
            pre_acquire_conflicts + post_verify_conflicts,
            total_probes,
            "every acquire-loop probe must terminate in one of the two conflict branches"
        );
    }
