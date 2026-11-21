#[derive(serde::Deserialize)]
struct BindingConfig {
    #[serde(default)]
    git: Option<ragfs::git::GitConfig>,
}

fn build_git_from_cfg(
    git_cfg: ragfs::git::GitConfig,
    fs: &Arc<MountableFS>,
    rt: &tokio::runtime::Runtime,
) -> PyResult<(Option<Arc<ragfs::git::GitService>>, Option<String>)> {
    let backend = git_cfg.backend.clone();
    let vfs = fs.clone() as Arc<dyn ragfs::core::FileSystem>;
    let _guard = rt.enter();
    let svc = git::build_git_service(&git_cfg, vfs)?;
    Ok((svc, Some(backend)))
}

fn load_git_from_config(
    path: &str,
    fs: &Arc<MountableFS>,
    rt: &tokio::runtime::Runtime,
) -> PyResult<(Option<Arc<ragfs::git::GitService>>, Option<String>)> {
    let body = std::fs::read_to_string(path)
        .map_err(|e| PyRuntimeError::new_err(format!("read config_path {}: {}", path, e)))?;
    let cfg: BindingConfig = toml::from_str(&body)
        .map_err(|e| PyRuntimeError::new_err(format!("parse config_path: {}", e)))?;
    match cfg.git {
        Some(git_cfg) => build_git_from_cfg(git_cfg, fs, rt),
        None => Ok((None, None)),
    }
}

/// RAGFS Python Binding Client.
///
/// Embeds the ragfs filesystem engine directly in the Python process.
/// API-compatible with the Go-based AGFSBindingClient.
#[pyclass]
struct RAGFSBindingClient {
    /// Mount manager: mount/unmount/list/get_*_stats/register_plugin.
    mountable: Arc<MountableFS>,
    /// Data entry point: `Stats(Encryption(Mountable))` when encrypted, else `Stats(Mountable)`.
    top: Arc<dyn FileSystem>,
    rt: tokio::runtime::Runtime,
    git_service: Option<Arc<ragfs::git::GitService>>,
    git_backend: Option<String>,
    /// PathLock manager. OpenViking always builds ragfs with PathLock enabled.
    pathlock_manager: Arc<PathLockManager>,
    /// Named cache runtimes. Closed only after mounted QueueFS instances shut down.
    cache_runtimes: HashMap<String, Arc<CacheRuntime>>,
    /// Service-scoped leases and revisions; backend selection stays inside Rust.
    coordination: Arc<CoordinationRuntime>,
    /// Write-time hooks applied before any filesystem write.
    write_hooks: WriteHookRegistry,
}

impl RAGFSBindingClient {
    /// Run an async filesystem op on the runtime, releasing the GIL, inside the FS_CTX scope.
    ///
    /// Centralizes the GIL-detach + `FS_CTX.scope` + `block_on` pattern so every data method
    /// shares one implementation (no per-method duplication).
    fn run_scoped<T, Fut, F>(&self, py: Python<'_>, ctx: FsContext, f: F) -> T
    where
        T: Send,
        Fut: Future<Output = T>,
        F: Send + FnOnce() -> Fut,
    {
        py_detach_blocking(py, move || {
            self.rt
                .block_on(FS_CTX.scope(ctx, async move { f().await }))
        })
    }

    /// Clone the PathLock manager shared by this binding client.
    fn clone_pathlock_manager(&self) -> Arc<PathLockManager> {
        self.pathlock_manager.clone()
    }
}

include!("client/constructor.rs");
include!("client/runtime.rs");
include!("client/git.rs");
include!("client/filesystem.rs");
include!("client/coordination.rs");
include!("client/observability.rs");
