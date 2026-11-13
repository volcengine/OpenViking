use super::*;

    /// Verify owner mutexes serialize one owner without blocking another.
    #[tokio::test]
    async fn owner_mutex_is_scoped_to_owner() {
        let owner_a = OwnerState::new("owner-a".to_string());
        let owner_b = OwnerState::new("owner-b".to_string());
        let _guard = owner_a.registry.lock().await;
        assert!(owner_a.registry.try_lock().is_err());
        assert!(owner_b.registry.try_lock().is_ok());
    }

#[tokio::test]
async fn loss_invalidates_only_the_lost_holder_lease() {
    let (manager, provider) = fixture().await;
    let tree = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let shared = manager
        .acquire_tree_batch(
            &["/data".into(), "/other".into()],
            Duration::ZERO,
            capability(&tree),
        )
        .await
        .unwrap();
    let child = manager
        .acquire_exact("/data/child", Duration::ZERO, capability(&tree))
        .await
        .unwrap();
    assert_eq!(shared.lease.lock_paths[0], tree.lease.lock_paths[0]);
    assert_ne!(child.lease.lock_paths[0], tree.lease.lock_paths[0]);
    provider
        .failures
        .lock()
        .unwrap()
        .insert(tree.lease.lock_paths[0].clone(), false);
    assert_eq!(manager.refresh(&tree).await.unwrap(), "lost");
    // Holders sharing a token are renewed and revoked independently.
    for lease in [&tree] {
        assert_eq!(
            lease_invalidation(&manager, lease).await.reason,
            InvalidationReason::TokenLost
        );
        assert!(manager
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());
        assert!(manager
            .require_covered_lease_ref(&lease.lease.lease_ref, &lease.lease.covered_paths)
            .await
            .is_err());
        assert!(manager
            .acquire_exact("/new", Duration::ZERO, capability(lease))
            .await
            .is_err());
        assert!(manager.handoff(lease).await.is_err());
        assert!(manager.refresh(lease).await.is_err());
        assert!(manager
            .get_releasable_lease_by_capability(&lease.lease.lease_ref, &lease.ownership_ref)
            .await
            .is_some());
    }
    for lease in [&shared, &child] {
        assert!(manager
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_some());
    }
    manager.release(&shared).await.unwrap();
    assert!(provider
        .read_token(&child.lease.lock_paths[0])
        .await
        .unwrap()
        .is_some());
    manager.release(&tree).await.unwrap();
    manager.release(&child).await.unwrap();
}

#[tokio::test]
async fn reentrant_and_adopted_holders_have_independent_deadlines() {
    let (manager, _) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let path = lease.lease.lock_paths[0].clone();
    let reentrant = manager
        .acquire_tree("/data", Duration::ZERO, capability(&lease))
        .await
        .unwrap();
    let handoff = manager.to_handoff(&reentrant);
    manager.handoff(&reentrant).await.unwrap();
    let adopted = manager.adopt(&handoff).await.unwrap();
    age_token(&manager, &lease, &path, Duration::from_secs(25)).await;
    assert!(manager
        .get_owned_lease_by_ref(&lease.lease.lease_ref)
        .await
        .is_none());
    assert!(manager
        .get_owned_lease_by_ref(&adopted.lease.lease_ref)
        .await
        .is_some());
    age_token(&manager, &adopted, &path, Duration::from_secs(25)).await;
    assert!(manager
        .get_owned_lease_by_ref(&adopted.lease.lease_ref)
        .await
        .is_none());
    manager.release(&lease).await.unwrap();
    manager.release(&adopted).await.unwrap();
}

#[tokio::test]
async fn delayed_result_for_old_handoff_ref_cannot_update_the_adopted_lease() {
    let (manager, _) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    let adopted = manager.adopt(&handoff).await.unwrap();
    let owner = manager
        .lease_registry
        .resolve(&adopted.lease.lease_ref)
        .unwrap();
    let mut registry = owner.registry.lock().await;
    let adopted_key = health_key(&adopted, &adopted.lease.lock_paths[0]);
    let before = registry.token_health[&adopted_key].last_confirmed_at;
    assert_eq!(
        registry.apply_refresh_results(
            &lease.lease.lease_ref,
            &lease.lease.lock_paths,
            vec![Ok(false)],
            Instant::now(),
            PathLockManager::lease_validity(&manager.config),
        ),
        "lost"
    );
    assert_eq!(
        registry.token_health[&adopted_key].last_confirmed_at,
        before
    );
    assert!(registry.entries[&adopted.lease.lease_ref]
        .invalidation
        .is_none());
    drop(registry);
    assert!(manager.release(&lease).await.is_err());
    manager.release(&adopted).await.unwrap();
}
