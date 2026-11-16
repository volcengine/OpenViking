use super::*;

    /// Verify Lua rejects conflicting owners without partially writing a batch.
    #[tokio::test]
    async fn provider_atomic_batch_is_all_or_nothing() {
        let Some((runtime, provider)) = test_provider("atomic").await else {
            return;
        };
        let initial = vec![request("/local/account-a/locked", PathLockKind::Tree)];
        let acquired = provider
            .try_acquire_batch_atomic(&initial, "owner-a", 100, 0)
            .await
            .unwrap()
            .unwrap();
        assert!(matches!(
            acquired.as_slice(),
            [AtomicAcquisition {
                handle,
                change: AcquisitionChange::Created { .. },
            }] if handle == "/local/account-a/locked"
        ));

        let conflicting = vec![
            request("/local/account-a/free", PathLockKind::Exact),
            request("/local/account-a/locked/child", PathLockKind::Exact),
        ];
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(&conflicting, "owner-b", 101, 0)
                .await,
            Err(PathLockError::Conflict { lock_path, .. })
                if lock_path == "/local/account-a/locked"
        ));
        assert_eq!(
            provider.read_token("/local/account-a/free").await.unwrap(),
            None
        );

        provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/a", PathLockKind::Exact)],
                "owner-a",
                102,
                0,
            )
            .await
            .unwrap();
        assert!(provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/a/b", PathLockKind::Exact)],
                "owner-b",
                103,
                0,
            )
            .await
            .is_ok());
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(&[request("/", PathLockKind::Tree)], "owner-c", 104, 0,)
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));

        let race = [request("/local/account-a/race", PathLockKind::Exact)];
        let (first, second) = tokio::join!(
            provider.try_acquire_batch_atomic(&race, "owner-a", 200, 0),
            provider.try_acquire_batch_atomic(&race, "owner-b", 200, 0),
        );
        assert_ne!(first.is_ok(), second.is_ok());
        assert!(
            matches!(first, Err(PathLockError::Conflict { .. }))
                || matches!(second, Err(PathLockError::Conflict { .. }))
        );
        runtime.close().await.unwrap();
    }

    /// Verify rollback removes creates and restores same-owner upgrades.
    #[tokio::test]
    async fn provider_atomic_rollback_restores_previous_state() {
        let Some((runtime, provider)) = test_provider("rollback").await else {
            return;
        };
        let exact = vec![request("/local/account-a/a", PathLockKind::Exact)];
        let created = provider
            .try_acquire_batch_atomic(&exact, "owner-a", 100, 0)
            .await
            .unwrap()
            .unwrap();
        provider
            .rollback_acquisitions_atomic(&created, "owner-a")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            provider.read_token("/local/account-a/a").await.unwrap(),
            None
        );

        provider
            .try_acquire_batch_atomic(&exact, "owner-a", 200, 0)
            .await
            .unwrap();
        let previous = provider.read_token(&exact[0].path).await.unwrap().unwrap();
        let tree = vec![request("/local/account-a/a", PathLockKind::Tree)];
        let upgraded = provider
            .try_acquire_batch_atomic(&tree, "owner-a", 300, 0)
            .await
            .unwrap()
            .unwrap();
        provider
            .rollback_acquisitions_atomic(&upgraded, "owner-a")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            provider.read_token("/local/account-a/a").await.unwrap(),
            Some(previous)
        );

        let changed = provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/changed", PathLockKind::Exact)],
                "owner-a",
                400,
                0,
            )
            .await
            .unwrap()
            .unwrap();
        assert!(provider
            .refresh_token("/local/account-a/changed", "owner-a", 500)
            .await
            .unwrap());
        let refreshed = provider
            .read_token("/local/account-a/changed")
            .await
            .unwrap()
            .unwrap();
        assert_ne!(refreshed.time_ns, 500);
        assert!(matches!(
            provider
                .rollback_acquisitions_atomic(&changed, "owner-a")
                .await,
            Err(PathLockError::Io(_))
        ));
        assert_eq!(
            provider
                .read_token("/local/account-a/changed")
                .await
                .unwrap(),
            Some(refreshed)
        );

        runtime.close().await.unwrap();
    }

    /// Verify stale foreign tokens are replaced while same-owner reentry preserves time.
    #[tokio::test]
    async fn provider_stale_cleanup_preserves_same_owner_reentry() {
        let Some((runtime, provider)) = test_provider("stale").await else {
            return;
        };
        let same_path = "/local/account-a/same";
        seed_token(
            &provider,
            same_path,
            &token("owner-a", 1, PathLockKind::Exact),
        )
        .await;
        let reentrant = provider
            .try_acquire_batch_atomic(
                &[request(same_path, PathLockKind::Exact)],
                "owner-a",
                100,
                50,
            )
            .await
            .unwrap()
            .unwrap();
        assert!(matches!(reentrant[0].change, AcquisitionChange::Reentrant));
        assert_eq!(
            provider.read_token(same_path).await.unwrap(),
            Some(token("owner-a", 1, PathLockKind::Exact))
        );
        assert_eq!(
            provider
                .is_path_locked_atomic(same_path, 100, 50, true)
                .await
                .unwrap(),
            Some(false)
        );
        assert_eq!(
            provider
                .is_path_locked_atomic(same_path, 100, 50, false)
                .await
                .unwrap(),
            Some(true)
        );

        let foreign_path = "/local/account-a/foreign";
        seed_token(
            &provider,
            foreign_path,
            &token("owner-b", 1, PathLockKind::Exact),
        )
        .await;
        provider
            .try_acquire_batch_atomic(
                &[request(foreign_path, PathLockKind::Exact)],
                "owner-a",
                100,
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            provider
                .read_token(foreign_path)
                .await
                .unwrap()
                .unwrap()
                .owner_id,
            "owner-a"
        );

        runtime.close().await.unwrap();
    }

    /// Verify an out-of-range token is rejected before an atomic upgrade writes.
    #[tokio::test]
    async fn provider_invalid_u128_token_does_not_modify_hash() {
        let Some((runtime, provider)) = test_provider("invalid-u128").await else {
            return;
        };
        runtime
            .register_script(ScriptDefinition {
                id: TEST_HASH_ID,
                redis_lua: TEST_HASH_SCRIPT,
            })
            .unwrap();
        let path = "/local/account-a/invalid";
        let key = provider.tokens_key_for_path(path).unwrap();
        let invalid = "owner-a:340282366920938463463374607431768211456:E";
        runtime
            .execute_script(ScriptRequest {
                script_id: TEST_HASH_ID.to_string(),
                keys: vec![key.clone()],
                args: vec![
                    RedisPathLockProvider::bytes("set"),
                    RedisPathLockProvider::bytes(path),
                    RedisPathLockProvider::bytes(invalid),
                ],
            })
            .await
            .unwrap();

        assert!(matches!(
            provider
                .try_acquire_batch_atomic(&[request(path, PathLockKind::Tree)], "owner-a", 100, 0,)
                .await,
            Err(PathLockError::InvalidToken(_))
        ));
        let raw = runtime
            .execute_script(ScriptRequest {
                script_id: TEST_HASH_ID.to_string(),
                keys: vec![key],
                args: vec![
                    RedisPathLockProvider::bytes("get"),
                    RedisPathLockProvider::bytes(path),
                ],
            })
            .await
            .unwrap()
            .decode()
            .unwrap();
        assert_eq!(raw, ScriptValue::Bytes(invalid.as_bytes().to_vec()));

        runtime.close().await.unwrap();
    }
