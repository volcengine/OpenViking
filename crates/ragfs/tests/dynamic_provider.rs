use ragfs::cache_runtime::{CacheError, CacheRuntime, DynamicProviderConfig};
use std::path::PathBuf;

#[tokio::test]
async fn dynamic_provider_is_explicitly_unsupported_in_this_release() {
    let result = CacheRuntime::dynamic(DynamicProviderConfig {
        library_path: PathBuf::from("libcache_provider.so"),
        params_json: "{}".into(),
    })
    .await;

    assert!(
        matches!(result, Err(CacheError::UnsupportedProvider(message)) if message.contains("DynamicProvider"))
    );
}
