use super::*;

    #[tokio::test]
    async fn handoff_and_adopt() {
        let mgr = make_manager().await;
        let outer = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let lease = mgr
            .acquire_tree(
                "/data/sub",
                Duration::ZERO,
                Some((&outer.lease.lease_ref, &outer.ownership_ref)),
            )
            .await
            .unwrap();
        let handoff = mgr.to_handoff(&lease);
        mgr.handoff(&lease).await.unwrap();
        let adopted = mgr.adopt(&handoff).await.unwrap();
        assert_eq!(adopted.lease.owner_id, lease.lease.owner_id);
        assert_ne!(adopted.lease.lease_ref, lease.lease.lease_ref);
        mgr.release(&adopted).await.unwrap();
        assert_eq!(mgr.refresh(&outer).await.unwrap(), "refreshed");
        assert!(mgr
            .acquire_exact("/data/sub/file", Duration::ZERO, None)
            .await
            .is_err());
        mgr.release(&outer).await.unwrap();
        let free = mgr
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        mgr.release(&free).await.unwrap();
    }

    #[tokio::test]
    async fn pending_handoff_rejects_stale_capability_but_keeps_refreshing() {
        let mgr = make_manager_with_config(PathLockConfig {
            lock_expire_secs: 0.03,
            ..PathLockConfig::default()
        })
        .await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let lock_path = lease.lease.lock_paths[0].clone();
        mgr.handoff(&lease).await.unwrap();

        // Producer's stale capability can no longer resolve the parked lease.
        // (FFI lifecycle ops all go through capability resolution.)
        assert!(mgr
            .get_owned_lease_by_capability(&lease.lease.lease_ref, &lease.ownership_ref)
            .await
            .is_none());
        assert!(mgr
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());

        // But the entry stays in the registry and keeps refreshing.
        let before = mgr
            .provider
            .read_token(&lock_path)
            .await
            .unwrap()
            .unwrap()
            .time_ns;
        tokio::time::sleep(Duration::from_millis(60)).await;
        let after = mgr
            .provider
            .read_token(&lock_path)
            .await
            .unwrap()
            .unwrap()
            .time_ns;
        assert!(after > before, "parked lease token must keep refreshing");

        let handoff = mgr.to_handoff(&lease);
        let adopted = mgr.adopt(&handoff).await.unwrap();
        assert_eq!(adopted.lease.owner_id, lease.lease.owner_id);
        // After adopt the new owner can operate the lease again.
        assert!(mgr.release(&adopted).await.is_ok());
    }

    #[tokio::test]
    async fn pending_handoff_is_forgotten_after_cross_manager_release() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let config = PathLockConfig {
            lock_expire_secs: 0.3,
            ..PathLockConfig::default()
        };
        let producer = PathLockManager::new(fs.clone(), provider.clone(), config.clone());
        let consumer = PathLockManager::new(fs, provider.clone(), config);
        let lease = producer
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        let lock_path = lease.lease.lock_paths[0].clone();
        let handoff = producer.to_handoff(&lease);
        producer.handoff(&lease).await.unwrap();

        let adopted = consumer.adopt(&handoff).await.unwrap();
        consumer.release(&adopted).await.unwrap();

        // The producer sees the consumer's release on its next renewal and
        // drops only its local pending-handoff registration. The old stale-GC
        // path would retain this entry for two lock-expiry periods.
        tokio::time::timeout(Duration::from_millis(400), async {
            loop {
                if producer.observe().await.active_locks == 0 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("producer must forget a released cross-manager handoff");
        assert_eq!(provider.read_token(&lock_path).await.unwrap(), None);
    }

    #[tokio::test]
    async fn adopt_before_handoff_is_retryable() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let handoff = mgr.to_handoff(&lease);

        // Consumer races ahead of the producer's handoff(): entry is still Owned.
        let err = mgr.adopt(&handoff).await.unwrap_err();
        assert!(matches!(err, PathLockError::HandoffFailed(_)));

        // Once the producer parks it, adopt succeeds with a fresh lease_ref.
        mgr.handoff(&lease).await.unwrap();
        let adopted = mgr.adopt(&handoff).await.unwrap();
        assert_ne!(adopted.lease.lease_ref, lease.lease.lease_ref);
    }

    #[tokio::test]
    async fn double_handoff_is_rejected() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        mgr.handoff(&lease).await.unwrap();
        assert!(mgr.handoff(&lease).await.is_err());
    }

    #[tokio::test]
    async fn adopt_fast_path_rejects_forged_covered_paths() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let mut handoff = mgr.to_handoff(&lease);
        // Tamper the coverage while keeping owner_id/lock_paths intact.
        handoff.covered_paths = vec![PathLockRequest {
            path: "/data/other".to_string(),
            kind: PathLockKind::Tree,
        }];
        mgr.handoff(&lease).await.unwrap();
        assert!(matches!(
            mgr.adopt(&handoff).await,
            Err(PathLockError::HandoffFailed(_))
        ));
    }

    #[tokio::test]
    async fn legacy_handoff_without_lease_ref_uses_token_fallback() {
        // A legacy payload (no lease_ref) only occurs across a restart: the token is
        // still on disk but the original registry entry is gone. Model that with a
        // second manager sharing the same fs + provider (an empty registry).
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let producer =
            PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
        let lease = producer
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        // Historic payload: no lease_ref, no covered_paths.
        let legacy = PathLockHandoffRef {
            holder_id: None,
            lease_ref: None,
            handle_mode: None,
            owner_id: lease.lease.owner_id.clone(),
            lock_paths: lease.lease.lock_paths.clone(),
            covered_paths: Vec::new(),
        };
        // "Restarted" process: fresh manager, empty registry, token persisted on disk.
        let consumer = PathLockManager::new(fs, provider, PathLockConfig::default());
        assert!(matches!(
            consumer.adopt(&legacy).await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::CoverageMissing
            })
        ));
        producer.release(&lease).await.unwrap();
    }

    #[tokio::test]
    async fn remote_adopt_rejects_forged_kind_without_recovery() {
        let (producer, consumer) = make_filesystem_managers().await;
        let lease = producer
            .acquire_exact("/data/a", Duration::ZERO, None)
            .await
            .unwrap();
        let mut handoff = producer.to_handoff(&lease);
        producer.handoff(&lease).await.unwrap();
        handoff.covered_paths[0].kind = PathLockKind::Tree;
        assert!(matches!(
            consumer.adopt(&handoff).await,
            Err(PathLockError::InvalidRequest(_))
        ));
        // Rejection does not change the original Exact token or consume the handoff.
        let adopted = consumer.adopt(&producer.to_handoff(&lease)).await.unwrap();
        assert_eq!(adopted.lease.covered_paths[0].kind, PathLockKind::Exact);
        consumer.release(&adopted).await.unwrap();
    }

    #[tokio::test]
    async fn legacy_tree_path_requires_explicit_recovery_even_with_coverage() {
        let (mgr, _) = make_filesystem_managers().await;
        mgr.resolver.fs.mkdir("/data/sub", 0o755).await.unwrap();
        let owner_id = "legacy-covered-owner".to_string();
        let lock_path = "/data/sub/.path.ovlock".to_string();
        mgr.provider
            .try_create_token(
                &lock_path,
                &LockToken {
                    holders: Vec::new(),
                    owner_id: owner_id.clone(),
                    time_ns: PathLockManager::now_ns(),
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();
        let handoff = PathLockHandoffRef {
            holder_id: None,
            lease_ref: Some("legacy-covered-lease".to_string()),
            handle_mode: None,
            owner_id,
            lock_paths: vec![lock_path],
            covered_paths: vec![PathLockRequest {
                path: "/data/sub".to_string(),
                kind: PathLockKind::Tree,
            }],
        };

        assert!(matches!(
            mgr.adopt(&handoff).await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::TokenUnavailable,
            })
        ));
        let adopted = mgr
            .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
            .await
            .unwrap();
        mgr.release(&adopted).await.unwrap();
    }

    #[tokio::test]
    async fn replay_requires_recovery_with_independently_releasable_holders() {
        let (producer, fs) = make_manager_with_fs().await;
        let lease1 = producer
            .acquire_exact("/data/file.txt", Duration::from_secs(1), None)
            .await
            .unwrap();
        let capability = (&*lease1.lease.lease_ref, &*lease1.ownership_ref);
        let lease2 = producer
            .acquire_exact("/data/file.txt", Duration::from_secs(1), Some(capability))
            .await
            .unwrap();
        let handoff1 = producer.to_handoff(&lease1);
        producer.handoff(&lease1).await.unwrap();
        producer.handoff(&lease2).await.unwrap();
        let consumer =
            PathLockManager::new(fs, producer.provider.clone(), PathLockConfig::default());
        let adopted1 = consumer.adopt(&handoff1).await.unwrap();
        let adopted2 = consumer.adopt(&producer.to_handoff(&lease2)).await.unwrap();
        consumer.handoff(&adopted1).await.unwrap();
        let next = consumer
            .adopt(&consumer.to_handoff(&adopted1))
            .await
            .unwrap();
        assert!(matches!(
            consumer.adopt(&handoff1).await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::TokenUnavailable,
            })
        ));
        let replay = consumer
            .reacquire_batch(&handoff1.covered_paths, &handoff1.owner_id, Duration::ZERO)
            .await
            .unwrap();
        assert_eq!(replay.lease.owner_id, adopted1.lease.owner_id);
        assert_ne!(replay.lease.lease_ref, adopted1.lease.lease_ref);
        consumer.release(&replay).await.unwrap();
        consumer.release(&next).await.unwrap();
        consumer.release(&adopted2).await.unwrap();
        assert!(consumer
            .provider
            .read_token(&adopted1.lease.lock_paths[0])
            .await
            .unwrap()
            .is_none());
    }

#[tokio::test]
async fn invalidated_pending_handoff_is_replaced_by_adoption() {
    let (manager, provider) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let mut registry = owner.registry.lock().await;
    provider
        .failures
        .lock()
        .unwrap()
        .insert(lease.lease.lock_paths[0].clone(), false);
    assert_eq!(
        PathLockManager::refresh_entry_with(
            &manager.provider,
            &mut registry,
            &owner.owner_id,
            &lease.lease.lease_ref,
            PathLockManager::lease_validity(&manager.config),
        )
        .await,
        "lost"
    );
    drop(registry);
    provider.failures.lock().unwrap().clear();
    let adopted = manager.adopt(&handoff).await.unwrap();
    assert!(manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .is_none());
    let token = provider
        .read_token(&lease.lease.lock_paths[0])
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        token
            .holders
            .iter()
            .map(|h| h.id.as_str())
            .collect::<Vec<_>>(),
        [adopted.lease.lease_ref.as_str()]
    );
    manager.release(&adopted).await.unwrap();
    assert!(provider
        .read_token(&lease.lease.lock_paths[0])
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn expired_source_requires_explicit_recovery_with_a_new_holder() {
    let (producer, provider) = fixture().await;
    let lease = producer
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = producer.to_handoff(&lease);
    producer.handoff(&lease).await.unwrap();
    let path = &lease.lease.lock_paths[0];
    let token = provider.read_token(path).await.unwrap().unwrap();
    let mut expired = token.clone();
    expired.holders[0].time_ns = 1;
    expired.recompute();
    assert!(provider
        .compare_and_write_token(path, &token, &expired)
        .await
        .unwrap());
    let consumer = PathLockManager::new(
        producer.resolver.fs.clone(),
        provider.clone(),
        PathLockConfig::default(),
    );
    assert!(matches!(
        consumer.adopt(&handoff).await,
        Err(PathLockError::HandoffRecoveryRequired {
            reason: HandoffRecoveryReason::TokenUnavailable,
        })
    ));
    assert_eq!(provider.read_token(path).await.unwrap(), Some(expired));
    let adopted = consumer
        .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
        .await
        .unwrap();
    let token = provider.read_token(path).await.unwrap().unwrap();
    assert_eq!(token.owner_id, lease.lease.owner_id);
    assert_eq!(
        token
            .holders
            .iter()
            .map(|h| h.id.as_str())
            .collect::<Vec<_>>(),
        [adopted.lease.lease_ref.as_str()]
    );
    consumer.release(&adopted).await.unwrap();
    assert!(provider.read_token(path).await.unwrap().is_none());
}

#[tokio::test]
async fn filesystem_adopt_retries_a_concurrent_same_owner_heartbeat() {
    let (_, provider, manager) = filesystem_fixture().await;
    let outer = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let inner = manager
        .acquire_tree("/data", Duration::ZERO, capability(&outer))
        .await
        .unwrap();
    let handoff = manager.to_handoff(&inner);
    manager.handoff(&inner).await.unwrap();
    provider.race_write.store(true, Ordering::SeqCst);
    let adopted = manager.adopt(&handoff).await.unwrap();
    let path = &outer.lease.lock_paths[0];
    let token = provider.read_token(path).await.unwrap().unwrap();
    let mut expected = vec![
        outer.lease.lease_ref.as_str(),
        adopted.lease.lease_ref.as_str(),
    ];
    expected.sort();
    assert_eq!(holder_ids(&token), expected);
    manager.release(&adopted).await.unwrap();
    manager.release(&outer).await.unwrap();
    assert!(provider.read_token(path).await.unwrap().is_none());
}

#[tokio::test]
async fn failed_filesystem_adoption_restores_the_parked_source() {
    let (_, provider, manager) = filesystem_fixture().await;
    let lease = manager
        .acquire_exact_batch(&["/data/a".into(), "/data/b".into()], Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    *provider.fail_write.lock().unwrap() = Some(lease.lease.lock_paths[1].clone());
    assert!(matches!(
        manager.adopt(&handoff).await,
        Err(PathLockError::Io(_))
    ));
    for path in &lease.lease.lock_paths {
        let token = provider.read_token(path).await.unwrap().unwrap();
        assert_eq!(holder_ids(&token), [lease.lease.lease_ref.as_str()]);
    }
    let adopted = manager.adopt(&handoff).await.unwrap();
    manager.release(&adopted).await.unwrap();
    for path in &lease.lease.lock_paths {
        assert!(provider.read_token(path).await.unwrap().is_none());
    }
}

#[tokio::test]
async fn source_loss_during_cas_rolls_back_the_transferred_batch() {
    let (_, provider, manager) = filesystem_fixture().await;
    let lease = manager
        .acquire_exact_batch(&["/data/a".into(), "/data/b".into()], Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    *provider.lose_source_on_write.lock().unwrap() = Some(lease.lease.lock_paths[1].clone());
    assert!(matches!(
        manager.adopt(&handoff).await,
        Err(PathLockError::HandoffRecoveryRequired {
            reason: HandoffRecoveryReason::TokenUnavailable,
        })
    ));
    let first = provider
        .read_token(&lease.lease.lock_paths[0])
        .await
        .unwrap()
        .unwrap();
    assert_eq!(holder_ids(&first), [lease.lease.lease_ref.as_str()]);
    assert!(provider
        .read_token(&lease.lease.lock_paths[1])
        .await
        .unwrap()
        .is_none());
    assert_eq!(manager.observe().await.active_locks, 1);
}

#[tokio::test]
async fn redelivered_handoff_recovers_under_the_live_outer_owner() {
    let (fs, provider, producer) = filesystem_fixture().await;
    let outer = producer
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let inner = producer
        .acquire_tree("/data", Duration::ZERO, capability(&outer))
        .await
        .unwrap();
    let handoff = producer.to_handoff(&inner);
    producer.handoff(&inner).await.unwrap();
    let crashed = PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
    let lost = crashed.adopt(&handoff).await.unwrap();
    // The consumer crashes; its holder expires while the outer lease stays live.
    let path = &outer.lease.lock_paths[0];
    let token = provider.read_token(path).await.unwrap().unwrap();
    let mut expired = token.clone();
    for holder in &mut expired.holders {
        if holder.id == lost.lease.lease_ref {
            holder.time_ns = 1;
        }
    }
    expired.recompute();
    assert!(provider
        .compare_and_write_token(path, &token, &expired)
        .await
        .unwrap());
    drop(crashed);
    let consumer = PathLockManager::new(fs, provider.clone(), PathLockConfig::default());
    assert!(matches!(
        consumer.adopt(&handoff).await,
        Err(PathLockError::HandoffRecoveryRequired {
            reason: HandoffRecoveryReason::TokenUnavailable,
        })
    ));
    let recovered = consumer
        .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
        .await
        .unwrap();
    assert_eq!(recovered.lease.owner_id, outer.lease.owner_id);
    consumer.release(&recovered).await.unwrap();
    assert_eq!(producer.refresh(&outer).await.unwrap(), "refreshed");
    assert!(consumer
        .acquire_exact("/data/file", Duration::ZERO, None)
        .await
        .is_err());
    producer.release(&outer).await.unwrap();
    assert!(provider.read_token(path).await.unwrap().is_none());
}

#[tokio::test]
async fn duplicate_and_chained_adoptions_each_release_their_own_holder() {
    let (fs, provider, first) = filesystem_fixture().await;
    let second = PathLockManager::new(fs, provider.clone(), PathLockConfig::default());
    let lease = first
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let path = lease.lease.lock_paths[0].clone();
    let handoff = first.to_handoff(&lease);
    first.handoff(&lease).await.unwrap();
    let (a, b) = tokio::join!(first.adopt(&handoff), second.adopt(&handoff));
    let recover = |result: PathLockResult<OwnedPathLockLease>| {
        assert!(matches!(
            result,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::TokenUnavailable,
            })
        ));
    };
    let (a, b) = match (a, b) {
        (Ok(a), failed) => {
            recover(failed);
            let b = second
                .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
                .await
                .unwrap();
            (a, b)
        }
        (failed, Ok(b)) => {
            recover(failed);
            let a = first
                .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
                .await
                .unwrap();
            (a, b)
        }
        (a, b) => panic!("one source must transfer exactly once: {a:?}, {b:?}"),
    };
    assert_ne!(a.lease.lease_ref, b.lease.lease_ref);
    first.release(&a).await.unwrap();
    assert!(provider.read_token(&path).await.unwrap().is_some());
    // Ownership travels B -> A -> B; each adoption retires its source.
    let mut current = (&second, b);
    for next in [&first, &second] {
        let handoff = current.0.to_handoff(&current.1);
        current.0.handoff(&current.1).await.unwrap();
        current = (next, next.adopt(&handoff).await.unwrap());
        let token = provider.read_token(&path).await.unwrap().unwrap();
        assert_eq!(holder_ids(&token), [current.1.lease.lease_ref.as_str()]);
    }
    current.0.release(&current.1).await.unwrap();
    assert!(provider.read_token(&path).await.unwrap().is_none());
}
