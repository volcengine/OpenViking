use bytes::Bytes;
use libloading::{Library, Symbol};
use ragfs::cache_runtime::{
    CacheError, CacheRuntime, DynamicProviderConfig, ScoredMember, ScoreRange,
};
use ragfs::core::{
    build_configured_stack, CacheFsConfig, CacheRuntimeProviderConfig, CacheStackConfig,
    RagfsConfig,
};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

#[tokio::test]
async fn dynamic_provider_loads_shared_library_and_dispatches_get() {
    let runtime = CacheRuntime::dynamic(config(fixture_library()))
        .await
        .unwrap();

    assert_eq!(
        runtime.get("key").await.unwrap(),
        Some(Bytes::from_static(b"fixture-value"))
    );
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn dynamic_provider_dispatches_v1_hash_and_sorted_set_calls() {
    let runtime = CacheRuntime::dynamic(config(fixture_library()))
        .await
        .unwrap();

    assert_eq!(
        runtime.hget("bucket", "query").await.unwrap(),
        Some(Bytes::from_static(b"fixture-hash-value"))
    );
    assert!(runtime
        .expire("bucket", std::time::Duration::from_secs(300))
        .await
        .unwrap());
    assert_eq!(
        runtime.zscan_match("idx", "buck*", 10).await.unwrap(),
        vec!["bucket".to_string()]
    );
    assert_eq!(
        runtime.zrem("idx", &["bucket".to_string()]).await.unwrap(),
        1
    );
    assert_eq!(runtime.unlink(&["bucket".to_string()]).await.unwrap(), 1);
    runtime.close().await.unwrap();
}

/// Verify a V1 dynamic provider cannot claim support for V2-only operations.
#[tokio::test]
async fn v1_dynamic_provider_rejects_v2_operations() {
    let runtime = CacheRuntime::dynamic(config(fixture_library()))
        .await
        .unwrap();
    let range = ScoreRange {
        min: 0.0,
        min_exclusive: false,
        max: 1.0,
        max_exclusive: false,
        offset: 0,
        limit: 1,
    };

    assert_unsupported(runtime.hgetall("hash").await.unwrap_err(), "hgetall");
    assert_unsupported(
        runtime
            .hset(
                "hash",
                HashMap::from([("field".to_string(), Bytes::from_static(b"value"))]),
            )
            .await
            .unwrap_err(),
        "hset",
    );
    assert_unsupported(
        runtime
            .zadd(
                "scores",
                vec![ScoredMember {
                    score: 1.0,
                    member: "member".to_string(),
                }],
            )
            .await
            .unwrap_err(),
        "zadd",
    );
    assert_unsupported(
        runtime
            .zrangebyscore("scores", range)
            .await
            .unwrap_err(),
        "zrangebyscore",
    );
    assert_unsupported(runtime.zcard("scores").await.unwrap_err(), "zcard");
    assert_unsupported(
        runtime
            .server_time_for_key("scores")
            .await
            .unwrap_err(),
        "server_time_for_key",
    );

    runtime.close().await.unwrap();
}

/// Verify a V2 dynamic provider receives batch values and returns scored members.
#[tokio::test]
async fn v2_dynamic_provider_forwards_cache_runtime_operations() {
    let runtime = CacheRuntime::dynamic(config(v2_fixture_library()))
        .await
        .unwrap();

    assert_eq!(
        runtime.hgetall("metadata").await.unwrap(),
        HashMap::from([("state".to_string(), Bytes::from_static(b"ready"))])
    );
    assert_eq!(
        runtime
            .hset(
                "metadata",
                HashMap::from([
                    ("state".to_string(), Bytes::from_static(b"ready")),
                    ("version".to_string(), Bytes::from_static(b"2")),
                ]),
            )
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        runtime
            .zadd(
                "scores",
                vec![
                    ScoredMember {
                        score: 3.5,
                        member: "three".to_string(),
                    },
                    ScoredMember {
                        score: 7.0,
                        member: "seven".to_string(),
                    },
                ],
            )
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        runtime
            .zrangebyscore(
                "scores",
                ScoreRange {
                    min: 1.0,
                    min_exclusive: true,
                    max: 9.0,
                    max_exclusive: false,
                    offset: 1,
                    limit: 2,
                },
            )
            .await
            .unwrap(),
        vec![
            ScoredMember {
                score: 3.5,
                member: "three".to_string(),
            },
            ScoredMember {
                score: 7.0,
                member: "seven".to_string(),
            },
        ]
    );
    assert_eq!(runtime.zcard("scores").await.unwrap(), 2);
    assert_eq!(
        runtime.server_time_for_key("scores").await.unwrap(),
        1_700_000_000_123
    );

    runtime.close().await.unwrap();
}

#[tokio::test]
async fn dynamic_provider_does_not_fallback_to_del_for_unlink() {
    let runtime = CacheRuntime::dynamic(config(no_unlink_fixture_library()))
        .await
        .unwrap();

    let error = runtime.unlink(&["bucket".to_string()]).await.unwrap_err();

    assert!(matches!(
        error,
        CacheError::UnsupportedOperation(operation) if operation == "unlink"
    ));
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn cachefs_rejects_dynamic_provider_without_query_cache_operations() {
    let result = build_configured_stack(
        RagfsConfig::default(),
        Some(CacheStackConfig {
            instances: HashMap::from([(
                "default".to_string(),
                CacheRuntimeProviderConfig::Dynamic(config(no_unlink_fixture_library())),
            )]),
            cachefs: CacheFsConfig {
                enabled: true,
                instance: "default".to_string(),
                service_name: "query-cache-test".to_string(),
                shared_redis_ttl: None,
            },
        }),
    )
    .await;

    let error = match result {
        Ok(_) => panic!("CacheFS unexpectedly started with a provider missing unlink"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("unlink"));
}

#[tokio::test]
async fn cachefs_rejects_dynamic_provider_without_zrangebyscore() {
    let result = build_configured_stack(
        RagfsConfig::default(),
        Some(CacheStackConfig {
            instances: HashMap::from([(
                "default".to_string(),
                CacheRuntimeProviderConfig::Dynamic(config(no_zrangebyscore_fixture_library())),
            )]),
            cachefs: CacheFsConfig {
                enabled: true,
                instance: "default".to_string(),
                service_name: "query-cache-test".to_string(),
                shared_redis_ttl: None,
            },
        }),
    )
    .await;

    let error = match result {
        Ok(_) => panic!("CacheFS unexpectedly started with a provider missing zrangebyscore"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("zrangebyscore"));
}

#[tokio::test]
async fn close_error_consumes_handle_and_is_not_retried_by_drop() {
    let library_path = close_error_fixture_library();
    let probe = unsafe { Library::new(library_path) }.unwrap();
    let close_count: Symbol<unsafe extern "C" fn() -> u32> =
        unsafe { probe.get(b"ov_fixture_close_count\0") }.unwrap();
    let runtime = CacheRuntime::dynamic(config(library_path)).await.unwrap();

    assert!(matches!(
        runtime.close().await,
        Err(CacheError::Internal(_))
    ));
    assert_eq!(unsafe { close_count() }, 1);
    drop(runtime);
    assert_eq!(unsafe { close_count() }, 1);
}

#[tokio::test]
async fn incompatible_abi_is_rejected_before_create() {
    let result = CacheRuntime::dynamic(config(bad_abi_fixture_library())).await;

    assert!(matches!(result, Err(CacheError::AbiMismatch(_))));
}

#[tokio::test]
async fn mismatched_output_length_is_rejected_without_unsafe_read() {
    let runtime = CacheRuntime::dynamic(config(bad_length_fixture_library()))
        .await
        .unwrap();

    let result = runtime.get("key").await;

    assert!(matches!(result, Err(CacheError::InvalidData(_))));
    runtime.close().await.unwrap();
}

fn config(library_path: &Path) -> DynamicProviderConfig {
    DynamicProviderConfig {
        library_path: library_path.to_path_buf(),
        params_json: "{}".to_string(),
    }
}

fn fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| compile_fixture("normal", &[]))
}

fn close_error_fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| compile_fixture("close-error", &["OV_FIXTURE_CLOSE_ERROR"]))
}

fn bad_abi_fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| compile_fixture("bad-abi", &["OV_FIXTURE_BAD_ABI"]))
}

fn bad_length_fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| compile_fixture("bad-length", &["OV_FIXTURE_BAD_LENGTH"]))
}

fn no_unlink_fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| {
        compile_fixture(
            "no-unlink",
            &["OV_FIXTURE_V2", "OV_FIXTURE_NO_UNLINK"],
        )
    })
}

fn no_zrangebyscore_fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| {
        compile_fixture(
            "no-zrangebyscore",
            &["OV_FIXTURE_V2", "OV_FIXTURE_NO_ZRANGEBYSCORE"],
        )
    })
}

fn v2_fixture_library() -> &'static PathBuf {
    static LIBRARY: OnceLock<PathBuf> = OnceLock::new();
    LIBRARY.get_or_init(|| compile_fixture("v2", &["OV_FIXTURE_V2"]))
}

/// Assert one operation is unavailable from a lower ABI provider.
fn assert_unsupported(error: CacheError, expected_operation: &str) {
    assert!(matches!(
        error,
        CacheError::UnsupportedOperation(operation) if operation == expected_operation
    ));
}

fn compile_fixture(name: &str, defines: &[&str]) -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let output_dir = manifest.join("../../target/dynamic-provider-fixture");
    std::fs::create_dir_all(&output_dir).unwrap();
    let extension = if cfg!(target_os = "windows") {
        "dll"
    } else if cfg!(target_os = "macos") {
        "dylib"
    } else {
        "so"
    };
    let library = output_dir.join(format!("libopenviking_cache_{name}.{extension}"));
    let mut command = Command::new("cc");
    if cfg!(target_os = "macos") {
        command.arg("-dynamiclib");
    } else {
        command.arg("-shared");
    }
    if !cfg!(target_os = "windows") {
        command.arg("-fPIC");
    }
    for define in defines {
        command.arg(format!("-D{define}"));
    }
    let status = command
        .arg("-I")
        .arg(manifest.join("include"))
        .arg(manifest.join("tests/fixtures/dynamic_provider_fixture.c"))
        .arg("-o")
        .arg(&library)
        .status()
        .expect("C compiler must be available for the dynamic provider ABI test");
    assert!(
        status.success(),
        "failed to compile dynamic provider fixture"
    );
    library.canonicalize().unwrap()
}
