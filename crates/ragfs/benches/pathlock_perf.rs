//! Manual release-mode benchmarks; see pathlock_perf.md for the measurement contract.
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use ragfs::core::{FileInfo, FileSystem, ListSortBy, Result as FsResult, SortOrder, WriteFlag};
use ragfs::lock::types::PathLockError;
use ragfs::lock::{
    FilesystemPathLockProvider, PathLockConfig, PathLockManager, PathLockProvider,
    SQLitePathLockProvider,
};
use ragfs::plugins::localfs::LocalFileSystem;
use serde::{Deserialize, Serialize};
use tempfile::TempDir;
use tokio::sync::{mpsc, Barrier};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone)]
struct Options {
    mode: String,
    iterations: usize,
    worker_iterations: usize,
    workers: Vec<usize>,
    args: HashMap<String, String>,
}

impl Options {
    fn parse() -> Result<Self> {
        let mut args = HashMap::new();
        let mut input = std::env::args().skip(1);
        while let Some(key) = input.next() {
            match key.as_str() {
                "--bench" => {}
                "--worker" => {
                    args.insert(key, "true".into());
                }
                "--help" | "-h" => {
                    println!(
                        "cargo bench --locked -p ragfs --bench pathlock_perf -- \
                        --mode all|tree|concurrency --iterations 20 \
                        --worker-iterations 100 --workers 1,4,16 --output /tmp/pathlock.csv"
                    );
                    std::process::exit(0);
                }
                _ => {
                    args.insert(key, input.next().ok_or("missing option value")?);
                }
            }
        }
        let options = Self {
            mode: args.get("--mode").cloned().unwrap_or("all".into()),
            iterations: args
                .get("--iterations")
                .map(|s| s.parse())
                .transpose()?
                .unwrap_or(20),
            worker_iterations: args
                .get("--worker-iterations")
                .map(|s| s.parse())
                .transpose()?
                .unwrap_or(100),
            workers: args
                .get("--workers")
                .map(String::as_str)
                .unwrap_or("1,4,16")
                .split(',')
                .map(str::parse)
                .collect::<std::result::Result<_, _>>()?,
            args,
        };
        if options.iterations == 0
            || options.worker_iterations == 0
            || options.workers.iter().any(|n| *n == 0)
            || !matches!(options.mode.as_str(), "all" | "tree" | "concurrency")
        {
            return Err(
                "iterations/workers must be positive and mode must be all, tree, or concurrency"
                    .into(),
            );
        }
        Ok(options)
    }

    fn required(&self, key: &str) -> Result<&str> {
        self.args
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| format!("missing {key}").into())
    }
}

#[derive(Default, Serialize, Deserialize)]
struct Samples {
    latency_ns: Vec<u64>,
    failures: usize,
    acquire_failures: usize,
    release_failures: usize,
    timeouts: usize,
    busy_errors: usize,
}

impl Samples {
    fn merge(&mut self, other: Self) {
        self.latency_ns.extend(other.latency_ns);
        self.failures += other.failures;
        self.acquire_failures += other.acquire_failures;
        self.release_failures += other.release_failures;
        self.timeouts += other.timeouts;
        self.busy_errors += other.busy_errors;
    }

    fn failed(&mut self, path: &str, phase: &str, error: &PathLockError) {
        if self.failures == 0 {
            eprintln!("first {phase} failure at {path}: {error}");
        }
        self.failures += 1;
        if phase == "acquire" {
            self.acquire_failures += 1;
        } else {
            self.release_failures += 1;
        }
        self.timeouts += usize::from(matches!(error, PathLockError::Timeout { .. }));
        self.busy_errors += usize::from(matches!(error, PathLockError::Busy { .. }));
    }
}

struct Record {
    case: String,
    provider: String,
    concurrency: usize,
    files: usize,
    directories: usize,
    scope: &'static str,
    elapsed: Duration,
    samples: Samples,
    directory_reads: Option<u64>,
}

// Count only directory listings. Delegate native CAS operations explicitly so
// instrumentation cannot change the real filesystem locking protocol.
struct CountingFS {
    inner: LocalFileSystem,
    directory_reads: AtomicU64,
}

#[async_trait]
impl FileSystem for CountingFS {
    async fn create(&self, path: &str) -> FsResult<()> {
        self.inner.create(path).await
    }
    async fn mkdir(&self, path: &str, mode: u32) -> FsResult<()> {
        self.inner.mkdir(path, mode).await
    }
    async fn remove(&self, path: &str) -> FsResult<()> {
        self.inner.remove(path).await
    }
    async fn remove_all(&self, path: &str) -> FsResult<()> {
        self.inner.remove_all(path).await
    }
    async fn read(&self, path: &str, offset: u64, size: u64) -> FsResult<Vec<u8>> {
        self.inner.read(path, offset, size).await
    }
    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> FsResult<u64> {
        self.inner.write(path, data, offset, flags).await
    }
    async fn compare_and_write(&self, path: &str, expected: &[u8], data: &[u8]) -> FsResult<bool> {
        self.inner.compare_and_write(path, expected, data).await
    }
    async fn compare_and_remove(&self, path: &str, expected: &[u8]) -> FsResult<bool> {
        self.inner.compare_and_remove(path, expected).await
    }
    async fn read_dir(
        &self,
        path: &str,
        offset: Option<usize>,
        limit: Option<usize>,
        sort_by: Option<ListSortBy>,
        sort_order: Option<SortOrder>,
    ) -> FsResult<Vec<FileInfo>> {
        self.directory_reads.fetch_add(1, Ordering::Relaxed);
        self.inner
            .read_dir(path, offset, limit, sort_by, sort_order)
            .await
    }
    async fn read_internal_dir(&self, path: &str) -> FsResult<Vec<FileInfo>> {
        self.directory_reads.fetch_add(1, Ordering::Relaxed);
        self.inner.read_internal_dir(path).await
    }
    async fn stat(&self, path: &str) -> FsResult<FileInfo> {
        self.inner.stat(path).await
    }
    async fn rename(&self, from: &str, to: &str) -> FsResult<()> {
        self.inner.rename(from, to).await
    }
    async fn chmod(&self, path: &str, mode: u32) -> FsResult<()> {
        self.inner.chmod(path, mode).await
    }
    async fn exists(&self, path: &str) -> bool {
        self.inner.exists(path).await
    }
    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> FsResult<()> {
        self.inner.ensure_parent_dirs(path, mode).await
    }
}

struct Output {
    csv: Option<File>,
}

impl Output {
    fn new(path: Option<&String>) -> Result<Self> {
        let mut output = Self {
            csv: path.map(File::create).transpose()?,
        };
        output.line("case,provider,workers,files,directories,latency_scope,successes,failures,elapsed_s,successful_ops_s,p50_ms,p95_ms,p99_ms,directory_reads,acquire_failures,release_failures,timeouts,busy_errors")?;
        Ok(output)
    }

    fn line(&mut self, line: &str) -> Result<()> {
        println!("{line}");
        if let Some(csv) = &mut self.csv {
            writeln!(csv, "{line}")?;
            csv.flush()?;
        }
        Ok(())
    }

    fn record(&mut self, mut record: Record) -> Result<()> {
        record.samples.latency_ns.sort_unstable();
        let quantile = |q: f64| -> String {
            let values = &record.samples.latency_ns;
            if values.is_empty() {
                return String::new();
            }
            let index = ((q * values.len() as f64).ceil() as usize).saturating_sub(1);
            format!(
                "{:.6}",
                values[index.min(values.len() - 1)] as f64 / 1_000_000.0
            )
        };
        self.line(&format!(
            "{},{},{},{},{},{},{},{},{:.6},{:.3},{},{},{},{},{},{},{},{}",
            record.case,
            record.provider,
            record.concurrency,
            record.files,
            record.directories,
            record.scope,
            record.samples.latency_ns.len(),
            record.samples.failures,
            record.elapsed.as_secs_f64(),
            record.samples.latency_ns.len() as f64 / record.elapsed.as_secs_f64(),
            quantile(0.5),
            quantile(0.95),
            quantile(0.99),
            record
                .directory_reads
                .map(|n| n.to_string())
                .unwrap_or_default(),
            record.samples.acquire_failures,
            record.samples.release_failures,
            record.samples.timeouts,
            record.samples.busy_errors,
        ))
    }
}

fn make_fs(root: &Path) -> Result<Arc<CountingFS>> {
    Ok(Arc::new(CountingFS {
        inner: LocalFileSystem::new(root.to_str().ok_or("non-UTF8 root")?)?,
        directory_reads: AtomicU64::new(0),
    }))
}

fn make_provider(
    name: &str,
    fs: Arc<dyn FileSystem>,
    db: &Path,
) -> Result<Arc<dyn PathLockProvider>> {
    match name {
        "filesystem" => Ok(Arc::new(FilesystemPathLockProvider::new(fs, 30.0))),
        "sqlite" => Ok(Arc::new(SQLitePathLockProvider::new(
            db.to_str().ok_or("non-UTF8 database")?,
            "pathlock-perf",
        )?)),
        _ => Err(format!("unknown provider: {name}").into()),
    }
}

async fn cycle(manager: &PathLockManager, path: &str, samples: &mut Samples) {
    let start = Instant::now();
    match manager.acquire_exact(path, Duration::ZERO, None).await {
        Ok(lease) => match manager.release(&lease).await {
            Ok(()) => samples.latency_ns.push(start.elapsed().as_nanos() as u64),
            Err(error) => samples.failed(path, "release", &error),
        },
        Err(error) => {
            samples.failed(path, "acquire", &error);
        }
    }
}

async fn tree(options: &Options, output: &mut Output) -> Result<()> {
    // Flat trees expose per-entry metadata cost; nested trees expose per-directory I/O.
    for (files, dirs) in [
        (10, 0),
        (200, 0),
        (1_000, 0),
        (5_000, 0),
        (10_000, 0),
        (200, 200),
        (1_000, 1_000),
    ] {
        let temp = TempDir::new()?;
        let root = temp.path().join("data");
        fs::create_dir_all(root.join("tree"))?;
        for id in 0..files {
            let parent = if dirs == 0 {
                root.join("tree")
            } else {
                let parent = root.join(format!("tree/d{id:05}"));
                fs::create_dir_all(&parent)?;
                parent
            };
            fs::write(parent.join(format!("f{id:05}")), b"x")?;
        }
        for name in ["filesystem", "sqlite"] {
            let tracked = make_fs(&root)?;
            let provider = make_provider(name, tracked.clone(), &temp.path().join("locks.db"))?;
            let manager =
                PathLockManager::new(tracked.clone(), provider, PathLockConfig::default());
            for _ in 0..3 {
                let lease = manager.acquire_tree("/tree", Duration::ZERO, None).await?;
                manager.release(&lease).await?;
            }
            let before = tracked.directory_reads.load(Ordering::Relaxed);
            let mut samples = Samples::default();
            let wall = Instant::now();
            for _ in 0..options.iterations {
                let start = Instant::now();
                let lease = manager.acquire_tree("/tree", Duration::ZERO, None).await?;
                samples.latency_ns.push(start.elapsed().as_nanos() as u64);
                manager.release(&lease).await?;
            }
            let elapsed = wall.elapsed();
            let after = tracked.directory_reads.load(Ordering::Relaxed);
            output.record(Record {
                case: if dirs == 0 {
                    "tree_flat"
                } else {
                    "tree_nested"
                }
                .into(),
                provider: name.into(),
                concurrency: 1,
                files,
                directories: dirs,
                scope: "acquire",
                elapsed,
                samples,
                directory_reads: Some(after - before),
            })?;
        }
    }
    Ok(())
}

fn prepare_work(root: &Path, workers: usize, iterations: usize) -> Result<()> {
    fs::create_dir_all(root.join("work"))?;
    for id in 0..workers {
        for iteration in 0..iterations + 3 {
            fs::write(root.join(format!("work/{id}-{iteration}")), b"x")?;
        }
    }
    Ok(())
}

async fn threads(options: &Options, name: &str, workers: usize, shared: bool) -> Result<Record> {
    let temp = TempDir::new()?;
    let root = temp.path().join("data");
    prepare_work(&root, workers, options.worker_iterations)?;
    let tracked = make_fs(&root)?;
    let db = temp.path().join("locks.db");
    let common = make_provider(name, tracked.clone(), &db)?;
    let barrier = Arc::new(Barrier::new(workers + 1));
    let (ready_tx, mut ready_rx) = mpsc::channel(workers);
    let mut tasks = Vec::new();
    for id in 0..workers {
        let provider = if shared {
            common.clone()
        } else {
            make_provider(name, tracked.clone(), &db)?
        };
        let manager = PathLockManager::new(tracked.clone(), provider, PathLockConfig::default());
        let barrier = barrier.clone();
        let ready_tx = ready_tx.clone();
        let iterations = options.worker_iterations;
        tasks.push(tokio::spawn(async move {
            let mut warmup = Samples::default();
            for iteration in 0..3 {
                cycle(&manager, &format!("/work/{id}-{iteration}"), &mut warmup).await;
            }
            ready_tx.send(()).await.unwrap();
            barrier.wait().await;
            let mut samples = Samples::default();
            for iteration in 3..iterations + 3 {
                cycle(&manager, &format!("/work/{id}-{iteration}"), &mut samples).await;
            }
            samples
        }));
    }
    for _ in 0..workers {
        ready_rx
            .recv()
            .await
            .ok_or("worker failed before barrier")?;
    }
    let before = tracked.directory_reads.load(Ordering::Relaxed);
    let wall = Instant::now();
    barrier.wait().await;
    let mut samples = Samples::default();
    for task in tasks {
        samples.merge(task.await?);
    }
    let elapsed = wall.elapsed();
    let after = tracked.directory_reads.load(Ordering::Relaxed);
    Ok(Record {
        case: if shared {
            "threads_shared_provider"
        } else {
            "threads_independent_providers"
        }
        .into(),
        provider: name.into(),
        concurrency: workers,
        files: workers * (options.worker_iterations + 3),
        directories: 0,
        scope: "acquire_release",
        elapsed,
        samples,
        directory_reads: Some(after - before),
    })
}

fn wait_for(path: &Path) -> Result<()> {
    let start = Instant::now();
    while !path.exists() {
        if start.elapsed() > Duration::from_secs(60) {
            return Err(format!("timed out waiting for {path:?}").into());
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    Ok(())
}

struct Worker(Child);
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

async fn process_worker(options: &Options) -> Result<()> {
    let root = PathBuf::from(options.required("--root")?);
    let db = PathBuf::from(options.required("--db")?);
    let sync = PathBuf::from(options.required("--sync")?);
    let id = options.required("--id")?;
    let tracked = make_fs(&root)?;
    let provider = make_provider(options.required("--provider")?, tracked.clone(), &db)?;
    let manager = PathLockManager::new(tracked, provider, PathLockConfig::default());
    let mut warmup = Samples::default();
    for iteration in 0..3 {
        cycle(&manager, &format!("/work/{id}-{iteration}"), &mut warmup).await;
    }
    fs::write(sync.join(format!("{id}.ready")), "")?;
    wait_for(&sync.join("go"))?;
    let mut samples = Samples::default();
    for iteration in 3..options.worker_iterations + 3 {
        cycle(&manager, &format!("/work/{id}-{iteration}"), &mut samples).await;
    }
    let temp_report = sync.join(format!("{id}.json.tmp"));
    fs::write(&temp_report, serde_json::to_vec(&samples)?)?;
    fs::rename(temp_report, sync.join(format!("{id}.json")))?;
    Ok(())
}

fn processes(options: &Options, name: &str, workers: usize) -> Result<Record> {
    let temp = TempDir::new()?;
    let root = temp.path().join("data");
    let db = temp.path().join("locks.db");
    let sync = temp.path().join("sync");
    prepare_work(&root, workers, options.worker_iterations)?;
    fs::create_dir(&sync)?;
    // Exclude concurrent schema initialization from measurements.
    drop(make_provider(name, make_fs(&root)?, &db)?);
    let mut children = Vec::new();
    for id in 0..workers {
        children.push(Worker(
            Command::new(std::env::current_exe()?)
                .arg("--worker")
                .arg("--provider")
                .arg(name)
                .arg("--root")
                .arg(&root)
                .arg("--db")
                .arg(&db)
                .arg("--sync")
                .arg(&sync)
                .arg("--id")
                .arg(id.to_string())
                .arg("--worker-iterations")
                .arg(options.worker_iterations.to_string())
                .spawn()?,
        ));
    }
    let deadline = Instant::now();
    loop {
        if (0..workers).all(|id| sync.join(format!("{id}.ready")).exists()) {
            break;
        }
        for worker in &mut children {
            if let Some(status) = worker.0.try_wait()? {
                return Err(format!("worker exited before start: {status}").into());
            }
        }
        if deadline.elapsed() > Duration::from_secs(60) {
            return Err("worker initialization timed out".into());
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    let wall = Instant::now();
    fs::write(sync.join("go"), "")?;
    let mut samples = Samples::default();
    for id in 0..workers {
        let report = sync.join(format!("{id}.json"));
        wait_for(&report)?;
        samples.merge(serde_json::from_slice(&fs::read(report)?)?);
    }
    let elapsed = wall.elapsed();
    for worker in &mut children {
        if !worker.0.wait()?.success() {
            return Err("worker failed".into());
        }
    }
    Ok(Record {
        case: "processes_independent_providers".into(),
        provider: name.into(),
        concurrency: workers,
        files: workers * (options.worker_iterations + 3),
        directories: 0,
        scope: "acquire_release",
        elapsed,
        samples,
        directory_reads: None,
    })
}

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() -> Result<()> {
    let options = Options::parse()?;
    if options.args.contains_key("--worker") {
        return process_worker(&options).await;
    }
    eprintln!("pathlock_perf os={} arch={} available_parallelism={} runtime_threads=4 tree_samples={} per_worker_samples={} sqlite=WAL/FULL busy_ms=100 busy_attempts=3 backoff_ms=1,4",
        std::env::consts::OS, std::env::consts::ARCH, std::thread::available_parallelism()?,
        options.iterations, options.worker_iterations);
    let mut output = Output::new(options.args.get("--output"))?;
    if matches!(options.mode.as_str(), "all" | "tree") {
        tree(&options, &mut output).await?;
    }
    if matches!(options.mode.as_str(), "all" | "concurrency") {
        for &workers in &options.workers {
            for name in ["filesystem", "sqlite"] {
                output.record(threads(&options, name, workers, true).await?)?;
                output.record(threads(&options, name, workers, false).await?)?;
                output.record(processes(&options, name, workers)?)?;
            }
        }
    }
    Ok(())
}
