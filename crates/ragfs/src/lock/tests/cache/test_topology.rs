use super::*;

    /// Verify optional cluster and sentinel topology contracts.
    #[tokio::test]
    async fn topology_cluster_and_sentinel() {
        if let Some(endpoints) = topology_endpoints("REDIS_CLUSTER_TEST_URLS") {
            exercise_topology("cluster", endpoints, None, "cluster").await;
        }
        if let (Some(endpoints), Ok(master)) = (
            topology_endpoints("REDIS_SENTINEL_TEST_URLS"),
            std::env::var("REDIS_SENTINEL_TEST_MASTER"),
        ) {
            exercise_topology("sentinel", endpoints, Some(master), "sentinel").await;
        }
    }
