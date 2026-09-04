use bytes::Bytes;
use libloading::{Library, Symbol};
use ragfs::cache_runtime::{CacheError, CacheRuntime, DynamicProviderConfig};
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
