use super::*;

    /// Verify CRUD preserves the token format and refreshes the HASH TTL.
    #[tokio::test]
    async fn provider_crud_preserves_tokens_and_hash_ttl() {
        let Some((runtime, provider)) = test_provider("crud").await else {
            return;
        };
        let path = "/local/account-a/a";
        provider
            .try_create_token(path, &token("owner-a", 100, PathLockKind::Exact))
            .await
            .unwrap();
        let original = provider.read_token(path).await.unwrap().unwrap();
        assert_eq!(original.owner_id, "owner-a");
        assert_eq!(original.lock_type, PathLockKind::Exact);
        assert_ne!(original.time_ns, 100);
        assert!(provider.refresh_token(path, "owner-a", 200).await.unwrap());
        let refreshed = provider.read_token(path).await.unwrap().unwrap();
        assert_eq!(refreshed.owner_id, original.owner_id);
        assert_eq!(refreshed.lock_type, original.lock_type);
        assert!(refreshed.time_ns >= original.time_ns);
        assert_ne!(refreshed.time_ns, 200);

        runtime
            .register_script(ScriptDefinition {
                id: TEST_PTTL_ID,
                redis_lua: TEST_PTTL_SCRIPT,
            })
            .unwrap();
        let ttl = runtime
            .execute_script(ScriptRequest {
                script_id: TEST_PTTL_ID.to_string(),
                keys: vec![provider.tokens_key_for_path(path).unwrap()],
                args: Vec::new(),
            })
            .await
            .unwrap()
            .decode()
            .unwrap();
        assert!(matches!(ttl, ScriptValue::Integer(value) if value > 0 && value <= 2_000));

        assert!(provider.remove_token(path, "owner-a", false).await.unwrap());
        assert_eq!(provider.read_token(path).await.unwrap(), None);
        runtime.close().await.unwrap();
    }
