use super::*;

    /// Verify invalid service_name and expiry values are rejected before Redis access.
    #[test]
    fn provider_rejects_invalid_configuration() {
        let runtime = CacheRuntime::memory();

        assert!(matches!(
            RedisPathLockProvider::new(runtime.clone(), "bad{name}", 1.0),
            Err(PathLockError::InvalidRequest(_))
        ));
        assert!(matches!(
            RedisPathLockProvider::new(runtime, "prod-a", f64::NAN),
            Err(PathLockError::InvalidRequest(_))
        ));
    }

    /// Verify global, system, and account paths use separate Redis HASH keys.
    #[tokio::test]
    async fn provider_routes_tokens_to_scope_hashes() {
        let Some((runtime, provider)) = test_provider("scope-keys").await else {
            return;
        };
        runtime
            .register_script(ScriptDefinition {
                id: TEST_HASH_ID,
                redis_lua: TEST_HASH_SCRIPT,
            })
            .unwrap();

        let cases = [
            ("/", "global"),
            ("/local", "global"),
            ("/local/_system/tasks/task.json", "scope:_system"),
            ("/local/account-a/resources/a.md", "scope:account:account-a"),
            ("/local/account-b/resources/b.md", "scope:account:account-b"),
        ];
        let service_name = format!("pathlock-test-{}-scope-keys", std::process::id());
        for (index, (path, scope)) in cases.iter().enumerate() {
            provider
                .try_create_token(
                    path,
                    &token("owner-a", index as u128 + 1, PathLockKind::Exact),
                )
                .await
                .unwrap();
            let raw = runtime
                .execute_script(ScriptRequest {
                    script_id: TEST_HASH_ID.to_string(),
                    keys: vec![format!("{{{service_name}}}:ov:pathlock:{scope}:tokens")],
                    args: vec![
                        RedisPathLockProvider::bytes("get"),
                        RedisPathLockProvider::bytes(path),
                    ],
                })
                .await
                .unwrap()
                .decode()
                .unwrap();
            assert!(matches!(raw, ScriptValue::Bytes(_)), "{path} missing");
        }
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(
                    &[
                        request("/local/account-a/a", PathLockKind::Exact),
                        request("/local/account-b/b", PathLockKind::Exact),
                    ],
                    "owner-a",
                    10,
                    0,
                )
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));
        assert!(matches!(
            provider
                .try_create_token("/queue", &token("owner-a", 10, PathLockKind::Exact))
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));

        runtime.close().await.unwrap();
    }

    /// Real filesystem aliases must not bypass another manager's Redis Exact lock.
    #[tokio::test]
    async fn redis_path_aliases_cannot_write_a_locked_file() {
        let Some((runtime, provider)) = test_provider("path-alias-write").await else {
            return;
        };
        let dir = tempfile::TempDir::new().unwrap();
        let fs = Arc::new(
            crate::plugins::localfs::LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap(),
        );
        let path = "/local/account-a/file";
        std::fs::create_dir_all(dir.path().join("local/account-a")).unwrap();
        fs.write(path, b"original", 0, crate::core::WriteFlag::Create)
            .await
            .unwrap();
        let second_provider =
            RedisPathLockProvider::new(runtime.clone(), &provider.service_name, 1.0).unwrap();
        let config = PathLockConfig {
            lock_expire_secs: 1.0,
            ..PathLockConfig::default()
        };
        let first = PathLockManager::new(fs.clone(), Arc::new(provider), config.clone());
        let second = Arc::new(PathLockManager::new(
            fs.clone(),
            Arc::new(second_provider),
            config,
        ));
        let wrapped = crate::lock::PathLockWrappedFS::new(second, fs.clone());
        let lease = first
            .acquire_exact(path, Duration::ZERO, None)
            .await
            .unwrap();

        assert!(matches!(
            wrapped
                .write(path, b"modified", 0, crate::core::WriteFlag::Truncate)
                .await,
            Err(crate::core::Error::PathLock(PathLockError::Timeout { .. }))
        ));
        for alias in [
            "/local/account-a/./file",
            "/local/account-a//file",
            "/local/./account-a/file",
        ] {
            assert_eq!(fs.read(alias, 0, 100).await.unwrap(), b"original");
            assert!(
                wrapped
                    .write(alias, b"modified", 0, crate::core::WriteFlag::Truncate)
                    .await
                    .is_err(),
                "write through alias {alias} bypassed the lock"
            );
            assert_eq!(fs.read(path, 0, 100).await.unwrap(), b"original");
        }
        first.release(&lease).await.unwrap();
        wrapped
            .write(path, b"modified", 0, crate::core::WriteFlag::Truncate)
            .await
            .unwrap();
        assert_eq!(fs.read(path, 0, 100).await.unwrap(), b"modified");
        runtime.close().await.unwrap();
    }

    /// Lua validates the entire batch before writing any target or cleaning stale tokens.
    #[tokio::test]
    async fn redis_lua_rejects_path_aliases_before_mutation() {
        let Some((runtime, provider)) = test_provider("path-alias-lua").await else {
            return;
        };
        let valid = "/local/account-a/file";
        let stale_path = "/local/account-a/stale";
        let stale = token("old-owner", 1, PathLockKind::Exact);
        seed_token(&provider, stale_path, &stale).await;
        for path in [
            "",
            "relative",
            "//",
            "/local/account-a/file/",
            "/local/account-a//file",
            "/local/account-a/./file",
            "/local/account-a/../file",
            "/local/account-a/file\0",
        ] {
            for kind in ["E", "T"] {
                let values = provider
                    .execute_array(
                        ACQUIRE_BATCH_ID,
                        provider.tokens_key_for_path(valid).unwrap(),
                        ["owner", "1000", valid, "E", path, kind]
                            .iter()
                            .map(RedisPathLockProvider::bytes)
                            .collect(),
                    )
                    .await
                    .unwrap();
                assert_eq!(
                    RedisPathLockProvider::result_status(&values, "test").unwrap(),
                    "invalid_request",
                    "Lua accepted {path:?}"
                );
                assert_eq!(provider.read_token(valid).await.unwrap(), None);
                assert_eq!(
                    provider.read_token(stale_path).await.unwrap(),
                    Some(stale.clone())
                );
            }
        }
        runtime.close().await.unwrap();
    }

    /// Every Redis token entry point rejects noncanonical paths before executing scripts.
    #[tokio::test]
    async fn redis_provider_rejects_path_aliases_before_script_access() {
        let mock = crate::cache_runtime::MemoryMockProvider::new()
            .with_script_handler(|_| panic!("invalid path must not execute a Redis script"));
        let runtime = CacheRuntime::memory_with_provider(Arc::new(mock));
        let provider = RedisPathLockProvider::new(runtime.clone(), "path-validation", 1.0).unwrap();
        let original = token("owner", 100, PathLockKind::Exact);
        for path in [
            "",
            "relative",
            "//",
            "/local/account-a/file/",
            "/local/account-a/file//",
            "/local/account-a//file",
            "/local/account-a/./file",
            "/local/account-a/../file",
            "/local/account-a/file\0",
        ] {
            for kind in [PathLockKind::Exact, PathLockKind::Tree] {
                assert!(matches!(
                    provider
                        .try_acquire_batch_atomic(
                            &[
                                request("/local/account-a/valid", PathLockKind::Exact),
                                request(path, kind)
                            ],
                            "owner",
                            200,
                            100,
                        )
                        .await,
                    Err(PathLockError::InvalidRequest(_))
                ));
            }
            assert!(matches!(
                provider.try_create_token(path, &original).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider
                    .compare_and_write_token(path, &original, &original)
                    .await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider.read_token(path).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider.refresh_token(path, "owner", 200).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                &provider
                    .refresh_tokens(&[path.to_string()], "owner", 200)
                    .await[0],
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider.remove_token(path, "owner", false).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider.scan_descendant_locks(path).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider.is_path_locked_atomic(path, 200, 100, true).await,
                Err(PathLockError::InvalidRequest(_))
            ));
        }
        runtime.close().await.unwrap();
    }

    /// Reject global Tree requests before accessing Redis, including token upgrades.
    #[tokio::test]
    async fn provider_rejects_cross_scope_tree_before_redis_access() {
        let mock = crate::cache_runtime::MemoryMockProvider::new()
            .with_script_handler(|_| panic!("invalid Tree request must not access Redis"));
        let runtime = CacheRuntime::memory_with_provider(Arc::new(mock));
        let provider = RedisPathLockProvider::new(runtime.clone(), "global-tree", 1.0).unwrap();
        for root in ["/", "/local"] {
            let exact = token("owner-a", 100, PathLockKind::Exact);
            let tree = token("owner-a", 200, PathLockKind::Tree);
            for requests in [
                vec![request(root, PathLockKind::Tree)],
                vec![
                    request(root, PathLockKind::Exact),
                    request(root, PathLockKind::Tree),
                ],
            ] {
                assert!(matches!(
                    provider
                        .try_acquire_batch_atomic(&requests, "owner-a", 200, 100)
                        .await,
                    Err(PathLockError::InvalidRequest(message))
                        if message.contains("Tree") && message.contains("scope")
                ));
            }
            assert!(matches!(
                provider.try_create_token(root, &tree).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                provider.compare_and_write_token(root, &exact, &tree).await,
                Err(PathLockError::InvalidRequest(_))
            ));
        }
        runtime.close().await.unwrap();
    }

    /// Neither acquisition order can publish a Tree token spanning Redis scope HASHes.
    #[tokio::test]
    async fn provider_rejects_cross_scope_tree_in_both_acquisition_orders() {
        let Some((runtime, provider)) = test_provider("global-tree-orders").await else {
            return;
        };
        for root in ["/", "/local"] {
            for scope in ["/local/account-a", "/local/_system"] {
                let child = format!("{scope}/file");
                for kind in [PathLockKind::Exact, PathLockKind::Tree] {
                    for child_first in [false, true] {
                        if child_first {
                            provider
                                .try_acquire_batch_atomic(
                                    &[request(&child, kind)],
                                    "owner-b",
                                    100,
                                    0,
                                )
                                .await
                                .unwrap();
                        }
                        assert!(matches!(
                            provider
                                .try_acquire_batch_atomic(
                                    &[request(root, PathLockKind::Tree)],
                                    "owner-a",
                                    100,
                                    0,
                                )
                                .await,
                            Err(PathLockError::InvalidRequest(_))
                        ));
                        assert_eq!(provider.read_token(root).await.unwrap(), None);
                        if !child_first {
                            provider
                                .try_acquire_batch_atomic(
                                    &[request(&child, kind)],
                                    "owner-b",
                                    100,
                                    0,
                                )
                                .await
                                .unwrap();
                        }
                        let current = provider.read_token(&child).await.unwrap().unwrap();
                        assert_eq!(current.owner_id, "owner-b");
                        assert_eq!(current.lock_type, kind);
                        assert_ne!(current.time_ns, 100);
                        provider
                            .remove_token(&child, "owner-b", false)
                            .await
                            .unwrap();
                    }
                }
            }
        }
        runtime.close().await.unwrap();
    }

    /// A rejected mixed batch cannot replace or clean up existing global Exact tokens.
    #[tokio::test]
    async fn provider_rejected_global_tree_batch_preserves_exact_tokens() {
        let Some((runtime, provider)) = test_provider("global-tree-no-mutation").await else {
            return;
        };
        let existing = token("owner-a", 1, PathLockKind::Exact);
        seed_token(&provider, "/", &existing).await;
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(
                    &[
                        request("/", PathLockKind::Exact),
                        request("/local", PathLockKind::Tree),
                    ],
                    "owner-b",
                    200,
                    100,
                )
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));
        assert_eq!(
            provider.read_token("/").await.unwrap(),
            Some(existing.clone())
        );
        assert_eq!(provider.read_token("/local").await.unwrap(), None);
        assert!(matches!(
            provider
                .compare_and_write_token("/", &existing, &token("owner-a", 200, PathLockKind::Tree))
                .await,
            Err(PathLockError::InvalidRequest(_))
        ));
        assert_eq!(provider.read_token("/").await.unwrap(), Some(existing));
        // Global Exact locks protect only their own path, so child scopes remain available.
        provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/file", PathLockKind::Exact)],
                "owner-b",
                200,
                0,
            )
            .await
            .unwrap();
        runtime.close().await.unwrap();
    }

    /// Account and system root Trees still exclude descendants in both directions.
    #[tokio::test]
    async fn provider_scope_root_trees_preserve_mutual_exclusion() {
        let Some((runtime, provider)) = test_provider("scope-root-tree").await else {
            return;
        };
        for root in ["/local/account-a", "/local/_system"] {
            let child = format!("{root}/file");
            for kind in [PathLockKind::Exact, PathLockKind::Tree] {
                for child_first in [false, true] {
                    let (first, second) = if child_first {
                        (request(&child, kind), request(root, PathLockKind::Tree))
                    } else {
                        (request(root, PathLockKind::Tree), request(&child, kind))
                    };
                    provider
                        .try_acquire_batch_atomic(&[first.clone()], "owner-a", 100, 0)
                        .await
                        .unwrap();
                    assert!(matches!(
                        provider
                            .try_acquire_batch_atomic(&[second], "owner-b", 100, 0)
                            .await,
                        Err(PathLockError::Conflict { .. })
                    ));
                    provider
                        .remove_token(&first.path, "owner-a", false)
                        .await
                        .unwrap();
                }
            }
        }
        runtime.close().await.unwrap();
    }
