use super::*;

    /// Classify filesystem migration without authorizing or changing old tokens.
    #[tokio::test]
    async fn manager_redis_classifies_filesystem_handoffs_without_reading_old_tokens() {
        let Some((runtime, redis, _, key)) = test_managers("filesystem-migration").await else {
            return;
        };
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/local", 0o755).await.unwrap();
        fs.mkdir("/local/account-a", 0o755).await.unwrap();
        fs.mkdir("/local/account-a/a", 0o755).await.unwrap();
        let old_provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            30.0,
        ));
        let old = PathLockManager::new(fs, old_provider.clone(), PathLockConfig::default());
        let lease = old
            .acquire_tree("/local/account-a/a", Duration::ZERO, None)
            .await
            .unwrap();
        let mut handoff = old.to_handoff(&lease);
        old.handoff(&lease).await.unwrap();
        assert_eq!(handoff.handle_mode, Some(PathLockHandleMode::LockPath));
        let old_token = old_provider
            .read_token(&handoff.lock_paths[0])
            .await
            .unwrap();
        assert!(matches!(
            redis.adopt(&handoff).await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::ProviderChanged,
            })
        ));
        let fresh = redis
            .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
            .await
            .unwrap();
        assert_eq!(fresh.lease.owner_id, handoff.owner_id);
        assert_ne!(fresh.lease.lease_ref, lease.lease.lease_ref);
        handoff.holder_id = None;
        handoff.handle_mode = None;
        handoff.covered_paths.clear();
        assert!(matches!(
            redis.adopt(&handoff).await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: crate::lock::types::HandoffRecoveryReason::ProviderChanged,
            })
        ));
        assert_eq!(
            old_provider
                .read_token(&handoff.lock_paths[0])
                .await
                .unwrap(),
            old_token
        );
        redis.release(&fresh).await.unwrap();
        runtime.del(&[key]).await.unwrap();
        runtime.close().await.unwrap();
    }

    /// Reject unavailable or forged sources without changing any batch member.
    #[tokio::test]
    async fn redis_holder_transfer_validates_the_entire_source_batch_before_mutation() {
        let Some((runtime, provider)) = test_provider("strict-source").await else {
            return;
        };
        let paths = ["/local/account-a/a", "/local/account-a/b"];
        let requests = paths.map(|path| PathLockRequest {
            path: path.into(),
            kind: PathLockKind::Exact,
        });
        let first = LockToken::held("owner", "source", PathLockKind::Exact, 200);
        provider.try_create_token(paths[0], &first).await.unwrap();
        let first = provider.read_token(paths[0]).await.unwrap().unwrap();
        let key = provider.tokens_key_for_path(paths[0]).unwrap();
        for second in [
            LockToken::held("owner", "sibling", PathLockKind::Exact, 200),
            LockToken::held("owner", "source", PathLockKind::Exact, 1),
            LockToken::held("owner", "source", PathLockKind::Tree, 200),
            LockToken::held("foreign", "source", PathLockKind::Exact, 200),
        ] {
            if second.time_ns == 1 {
                super::super::time_tests::seed(&provider, paths[1], &second).await;
            } else {
                provider.try_create_token(paths[1], &second).await.unwrap();
            }
            let second = provider.read_token(paths[1]).await.unwrap().unwrap();
            let result = provider
                .try_acquire_holders_atomic(&requests, "owner", "new", 300, 100, Some("source"))
                .await;
            if second.lock_type == PathLockKind::Tree {
                assert!(matches!(result, Err(PathLockError::InvalidRequest(_))));
            } else {
                assert!(matches!(
                    result,
                    Err(PathLockError::HandoffRecoveryRequired {
                        reason: HandoffRecoveryReason::TokenUnavailable,
                    })
                ));
            }
            assert_eq!(
                provider.read_token(paths[0]).await.unwrap(),
                Some(first.clone())
            );
            assert_eq!(
                provider.read_token(paths[1]).await.unwrap(),
                Some(second.clone())
            );
            assert!(provider
                .compare_and_remove_token(paths[1], &second)
                .await
                .unwrap());
        }
        assert!(matches!(
            provider
                .try_acquire_holders_atomic(&requests, "owner", "new", 300, 100, Some("source"))
                .await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::TokenUnavailable,
            })
        ));
        assert_eq!(provider.read_token(paths[0]).await.unwrap(), Some(first));
        assert!(provider.read_token(paths[1]).await.unwrap().is_none());
        runtime.del(&[key]).await.unwrap();
        runtime.close().await.unwrap();
    }

    /// Verify Redis handoff can be adopted by another manager using logical handles.
    #[tokio::test]
    async fn manager_redis_adopts_handoff_across_managers() {
        let Some((runtime, first, second, key)) = test_managers("handoff").await else {
            return;
        };
        let outer = first
            .acquire_tree("/local/account-a/a", Duration::ZERO, None)
            .await
            .unwrap();
        let lease = first
            .acquire_tree(
                "/local/account-a/a",
                Duration::ZERO,
                Some((&outer.lease.lease_ref, &outer.ownership_ref)),
            )
            .await
            .unwrap();
        let handoff = first.to_handoff(&lease);
        first.handoff(&lease).await.unwrap();

        let adopted = second.adopt(&handoff).await.unwrap();

        second.release(&adopted).await.unwrap();
        assert_eq!(first.refresh(&outer).await.unwrap(), "refreshed");
        assert!(matches!(
            second.adopt(&handoff).await,
            Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::TokenUnavailable,
            })
        ));
        let replay = second
            .reacquire_batch(&handoff.covered_paths, &handoff.owner_id, Duration::ZERO)
            .await
            .unwrap();
        assert_ne!(replay.lease.lease_ref, adopted.lease.lease_ref);
        assert_eq!(replay.lease.owner_id, outer.lease.owner_id);
        first.release(&outer).await.unwrap();
        assert!(first
            .acquire_tree("/local/account-a/a", Duration::ZERO, None)
            .await
            .is_err());
        second.release(&replay).await.unwrap();
        let free = first
            .acquire_tree("/local/account-a/a", Duration::ZERO, None)
            .await
            .unwrap();
        first.release(&free).await.unwrap();
        let legacy = PathLockHandoffRef {
            holder_id: None,
            lease_ref: None,
            handle_mode: None,
            owner_id: "owner-a".to_string(),
            lock_paths: vec!["/local/account-a/a".to_string()],
            covered_paths: Vec::new(),
        };
        assert!(matches!(
            second.adopt(&legacy).await,
            Err(PathLockError::HandoffRecoveryRequired { .. })
        ));
        runtime.del(&[key]).await.unwrap();
        runtime.close().await.unwrap();
    }

    /// Verify reentrant upgrades, downgrades, refresh, and partial release use logical handles.
    #[tokio::test]
    async fn manager_redis_preserves_lease_lifecycle_semantics() {
        let Some((runtime, first, second, key)) = test_managers("lifecycle").await else {
            return;
        };
        let exact = first
            .acquire_exact("/local/account-a/a/", Duration::ZERO, None)
            .await
            .unwrap();
        assert_eq!(exact.lease.lock_paths, vec!["/local/account-a/a"]);
        assert!(first.is_locked("/local/account-a/a/", true).await.unwrap());
        let tree = first
            .acquire_tree(
                "/local/account-a/a",
                Duration::ZERO,
                Some((&exact.lease.lease_ref, &exact.ownership_ref)),
            )
            .await
            .unwrap();
        assert_eq!(first.refresh(&exact).await.unwrap(), "refreshed");
        first.release(&tree).await.unwrap();

        let child = second
            .acquire_exact("/local/account-a/a/child", Duration::ZERO, None)
            .await
            .unwrap();
        second.release(&child).await.unwrap();
        first.release(&exact).await.unwrap();
        assert!(!first.is_locked("/local/account-a/a", true).await.unwrap());

        let batch = first
            .acquire_exact_batch(
                &[
                    "/local/account-a/x".to_string(),
                    "/local/account-a/y".to_string(),
                ],
                Duration::ZERO,
                None,
            )
            .await
            .unwrap();
        first
            .release_selected(&batch, &["/local/account-a/x".to_string()])
            .await
            .unwrap();
        let released = second
            .acquire_exact("/local/account-a/x", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(second
            .acquire_exact("/local/account-a/y", Duration::from_millis(1), None,)
            .await
            .is_err());
        second.release(&released).await.unwrap();
        first.release(&batch).await.unwrap();

        runtime.del(&[key]).await.unwrap();
        runtime.close().await.unwrap();
    }
