//! OpenViking Assets manifest mode for `ov add-resource -m <manifest.yaml>`.
//!
//! Implements the declaration layer of the OpenViking Assets protocol
//! (`openviking-assets/1`):
//! a team-wide asset catalog (`assets.yaml`) plus flat build manifests selecting
//! catalog assets by name. Validation is strict by design — unknown
//! fields are errors, not warnings — so a mistyped manifest fails at parse
//! time instead of silently degrading. Fetching, parsing, vectorization and
//! watch-based refresh stay in the server and its connector plugins.

use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::CliContext;
use crate::error::{Error, Result};
use crate::output::OutputFormat;

pub const STATE_PROTOCOL: &str = "openviking-assets-state/1";
const DEFAULT_CATALOG_FILENAME: &str = "assets.yaml";
const CREDENTIALS_ENV: &str = "OPENVIKING_ASSETS_CREDENTIALS_FILE";

fn client_err(msg: impl Into<String>) -> Error {
    Error::Client(msg.into())
}

// The server owns OpenViking Assets syntax parsing and semantic validation.
// The CLI receives only the resolved execution plan.
#[derive(Debug, Clone, Deserialize)]
pub struct ResolvedAsset {
    pub name: String,
    pub connector: String,
    pub repo_url: String,
    pub branch: Option<String>,
    pub auth_ref: Option<String>,
    pub watch_interval: f64,
    pub locator: String,
    pub git_ref: String,
    pub asset_id: String,
}

#[derive(Deserialize)]
struct ResolveResponse {
    assets: Vec<ResolvedAsset>,
}

fn read_yaml<T: serde::de::DeserializeOwned>(path: &Path, what: &str) -> Result<T> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| client_err(format!("cannot read {what} '{}': {e}", path.display())))?;
    serde_yaml::from_str(&text).map_err(|e| client_err(format!("{what} '{}': {e}", path.display())))
}

// ============================ Credentials layer ===========================

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawCredentialsFile {
    #[serde(default)]
    credentials: Option<BTreeMap<String, Option<BTreeMap<String, serde_yaml::Value>>>>,
}

pub fn credentials_path() -> PathBuf {
    if let Ok(path) = std::env::var(CREDENTIALS_ENV)
        && !path.is_empty()
    {
        return PathBuf::from(path);
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openviking")
        .join("openviking_assets_credentials.yaml")
}

/// Load the alias -> args mapping. A missing file is an empty mapping.
pub fn load_credentials(path: &Path) -> Result<BTreeMap<String, Map<String, Value>>> {
    if !path.is_file() {
        return Ok(BTreeMap::new());
    }
    let raw: RawCredentialsFile = read_yaml(path, "credentials file")?;
    let mut parsed = BTreeMap::new();
    for (alias, args) in raw.credentials.unwrap_or_default() {
        let mut map = Map::new();
        for (key, value) in args.unwrap_or_default() {
            let value = serde_json::to_value(&value).map_err(|e| {
                client_err(format!(
                    "credentials file '{}': alias '{alias}' arg '{key}': {e}",
                    path.display()
                ))
            })?;
            map.insert(key, value);
        }
        parsed.insert(alias, map);
    }
    Ok(parsed)
}

/// Return the args behind an alias; a declared but unresolvable alias is an error.
pub fn resolve_auth_ref(
    auth_ref: &str,
    credentials: &BTreeMap<String, Map<String, Value>>,
    path: &Path,
) -> Result<Map<String, Value>> {
    credentials.get(auth_ref).cloned().ok_or_else(|| {
        client_err(format!(
            "auth_ref '{auth_ref}' is not defined in the local credentials file '{}'; \
             add it under 'credentials:', or remove 'auth_ref' from the catalog entry if \
             the server's own auth (e.g. SSH keys) is sufficient",
            path.display()
        ))
    })
}

// =============================== State layer ==============================

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct AssetStateEntry {
    pub name: String,
    pub connector: String,
    pub locator: String,
    #[serde(rename = "ref")]
    pub git_ref: String,
    #[serde(default)]
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resource_uri: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_applied_at: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct RawStateFile {
    protocol: String,
    #[serde(default)]
    updated_at: Option<String>,
    #[serde(default)]
    assets: BTreeMap<String, AssetStateEntry>,
}

/// Manifest-level apply state (`<manifest>.state.json`).
///
/// Records only manifest-level facts: which asset became which resource and
/// how the last apply ended. Content-level sync watermarks stay with the
/// server-side connector/watch machinery. Assets that disappear from the
/// manifest are reported as orphans and kept — v1 never deletes resources.
#[derive(Debug)]
pub struct ManifestState {
    pub path: PathBuf,
    pub assets: BTreeMap<String, AssetStateEntry>,
}

impl ManifestState {
    pub fn orphans(&self, current_ids: &HashSet<String>) -> Vec<(&String, &AssetStateEntry)> {
        self.assets
            .iter()
            .filter(|(id, _)| !current_ids.contains(*id))
            .collect()
    }

    pub fn record(&mut self, asset_id: &str, mut entry: AssetStateEntry) {
        entry.last_applied_at = Some(now_iso());
        self.assets.insert(asset_id.to_string(), entry);
    }

    pub fn save(&self) -> Result<()> {
        let payload = RawStateFile {
            protocol: STATE_PROTOCOL.to_string(),
            updated_at: Some(now_iso()),
            assets: self.assets.clone(),
        };
        let text = serde_json::to_string_pretty(&payload)
            .map_err(|e| client_err(format!("cannot serialize state: {e}")))?;
        let tmp_path = self.path.with_extension("json.tmp");
        std::fs::write(&tmp_path, text + "\n")
            .map_err(|e| client_err(format!("cannot write '{}': {e}", tmp_path.display())))?;
        std::fs::rename(&tmp_path, &self.path)
            .map_err(|e| client_err(format!("cannot write '{}': {e}", self.path.display())))?;
        Ok(())
    }
}

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, false)
}

pub fn state_path_for(manifest_path: &Path) -> PathBuf {
    let name = manifest_path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    manifest_path.with_file_name(format!("{name}.state.json"))
}

pub fn load_state(manifest_path: &Path) -> Result<ManifestState> {
    let path = state_path_for(manifest_path);
    if !path.is_file() {
        return Ok(ManifestState {
            path,
            assets: BTreeMap::new(),
        });
    }
    let text = std::fs::read_to_string(&path).map_err(|e| {
        client_err(format!(
            "state file '{}' is unreadable: {e}",
            path.display()
        ))
    })?;
    let raw: RawStateFile = serde_json::from_str(&text).map_err(|e| {
        client_err(format!(
            "state file '{}' is unreadable ({e}); fix or remove it and re-run",
            path.display()
        ))
    })?;
    if raw.protocol != STATE_PROTOCOL {
        return Err(client_err(format!(
            "state file '{}' has an unsupported protocol (expected '{STATE_PROTOCOL}'); \
             remove it to start fresh",
            path.display()
        )));
    }
    Ok(ManifestState {
        path,
        assets: raw.assets,
    })
}

// =============================== Apply layer ==============================

#[derive(Debug, Clone, Default)]
pub struct ManifestRunOptions {
    pub dry_run: bool,
    pub skip_failed: bool,
    pub wait: bool,
    /// Overrides per-asset / catalog-default values when set.
    pub watch_interval: Option<f64>,
}

#[derive(Debug, Default)]
pub struct ApplySummary {
    pub total: usize,
    pub succeeded: Vec<String>,
    pub failed: BTreeMap<String, String>,
    pub not_attempted: Vec<String>,
}

impl ApplySummary {
    pub fn all_failed(&self) -> bool {
        self.total > 0 && self.failed.len() == self.total
    }
}

/// The one call the orchestrator needs; tests provide a fake implementation.
pub trait Submitter {
    async fn submit(
        &self,
        asset: &ResolvedAsset,
        to: Option<String>,
        watch_interval: f64,
        args: Option<Map<String, Value>>,
    ) -> Result<Value>;
}

fn extract_str(result: &Value, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| {
        result
            .get(key)
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    })
}

fn build_args(
    asset: &ResolvedAsset,
    credential_args: &Map<String, Value>,
) -> Option<Map<String, Value>> {
    let mut args = Map::new();
    if let Some(branch) = &asset.branch {
        args.insert("branch".to_string(), json!(branch));
    }
    for (key, value) in credential_args {
        args.insert(key.clone(), value.clone());
    }
    if args.is_empty() { None } else { Some(args) }
}

/// Apply (or dry-run) a resolved manifest with per-asset failure isolation.
pub async fn apply_manifest_core<S: Submitter>(
    manifest_path: &Path,
    catalog_path: &Path,
    assets: &[ResolvedAsset],
    credentials_file: &Path,
    options: &ManifestRunOptions,
    submitter: &S,
    emit: &mut dyn FnMut(Value),
) -> Result<ApplySummary> {
    let mut state = load_state(manifest_path)?;

    // Pre-flight: every declared auth_ref must resolve before anything is submitted.
    let credentials = load_credentials(credentials_file)?;
    let mut credential_args: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
    for asset in assets {
        let args = match &asset.auth_ref {
            Some(auth_ref) => resolve_auth_ref(auth_ref, &credentials, credentials_file)?,
            None => Map::new(),
        };
        credential_args.insert(asset.name.clone(), args);
    }

    let current_ids: HashSet<String> = assets.iter().map(|a| a.asset_id.clone()).collect();
    for (asset_id, orphan) in state.orphans(&current_ids) {
        emit(json!({
            "event": "orphan",
            "asset_id": asset_id,
            "name": orphan.name,
            "resource_uri": orphan.resource_uri,
            "note": "no longer in manifest; OpenViking Assets never deletes resources automatically",
        }));
    }

    emit(json!({
        "event": "plan",
        "manifest": manifest_path.display().to_string(),
        "catalog": catalog_path.display().to_string(),
        "total": assets.len(),
        "dry_run": options.dry_run,
    }));

    let mut summary = ApplySummary {
        total: assets.len(),
        ..Default::default()
    };
    let mut stop = false;
    for (index, asset) in assets.iter().enumerate() {
        let existing = state.assets.get(&asset.asset_id).cloned();
        let action = match &existing {
            Some(entry) if entry.resource_uri.is_some() => "sync",
            _ => "create",
        };
        let watch_interval = options.watch_interval.unwrap_or(asset.watch_interval);
        let base = json!({
            "index": index + 1,
            "total": assets.len(),
            "name": asset.name,
            "asset_id": asset.asset_id,
            "connector": asset.connector,
            "locator": asset.locator,
            "ref": asset.git_ref,
            "action": action,
            "watch_interval": watch_interval,
        });
        let with = |base: &Value, extra: Value| {
            let mut merged = base.as_object().cloned().unwrap_or_default();
            if let Value::Object(extra) = extra {
                merged.extend(extra);
            }
            Value::Object(merged)
        };

        if stop {
            summary.not_attempted.push(asset.name.clone());
            emit(with(
                &base,
                json!({"event": "asset_skipped", "reason": "previous asset failed"}),
            ));
            continue;
        }
        if options.dry_run {
            emit(with(&base, json!({"event": "asset_planned"})));
            continue;
        }

        emit(with(&base, json!({"event": "asset_start"})));
        let mut entry = AssetStateEntry {
            name: asset.name.clone(),
            connector: asset.connector.clone(),
            locator: asset.locator.clone(),
            git_ref: asset.git_ref.clone(),
            resource_uri: existing.as_ref().and_then(|e| e.resource_uri.clone()),
            task_id: existing.as_ref().and_then(|e| e.task_id.clone()),
            ..Default::default()
        };
        let args = build_args(asset, &credential_args[&asset.name]);
        match submitter
            .submit(asset, entry.resource_uri.clone(), watch_interval, args)
            .await
        {
            Ok(response) => {
                entry.status = if options.wait { "ok" } else { "submitted" }.to_string();
                entry.error = None;
                if let Some(uri) = extract_str(&response, &["uri", "resource_uri", "to"]) {
                    entry.resource_uri = Some(uri);
                }
                if let Some(task_id) = extract_str(&response, &["task_id"]) {
                    entry.task_id = Some(task_id);
                }
                let done = with(
                    &base,
                    json!({
                        "event": "asset_done",
                        "status": entry.status,
                        "resource_uri": entry.resource_uri,
                        "task_id": entry.task_id,
                    }),
                );
                state.record(&asset.asset_id, entry);
                summary.succeeded.push(asset.name.clone());
                emit(done);
            }
            Err(err) => {
                let message = err.to_string();
                entry.status = "failed".to_string();
                entry.error = Some(message.clone());
                state.record(&asset.asset_id, entry);
                summary.failed.insert(asset.name.clone(), message.clone());
                emit(with(
                    &base,
                    json!({"event": "asset_failed", "error": message}),
                ));
                if !options.skip_failed {
                    stop = true;
                }
            }
        }
    }

    if !options.dry_run {
        state.save()?;
    }

    emit(json!({
        "event": "summary",
        "total": summary.total,
        "succeeded": summary.succeeded.len(),
        "failed": summary.failed.len(),
        "not_attempted": summary.not_attempted.len(),
        "all_failed": summary.all_failed(),
        "dry_run": options.dry_run,
        "state": if options.dry_run { Value::Null } else { json!(state.path.display().to_string()) },
    }));
    Ok(summary)
}

// ================================ CLI entry ===============================

struct HttpSubmitter {
    client: crate::client::HttpClient,
    wait: bool,
    timeout: Option<f64>,
}

impl Submitter for HttpSubmitter {
    async fn submit(
        &self,
        asset: &ResolvedAsset,
        to: Option<String>,
        watch_interval: f64,
        args: Option<Map<String, Value>>,
    ) -> Result<Value> {
        self.client
            .add_resource(
                &asset.repo_url,
                to,
                None,
                None,
                "",
                "",
                self.wait,
                self.timeout,
                false,
                None,
                None,
                None,
                true,
                watch_interval,
                args,
                false,
                false,
            )
            .await
    }
}

struct NeverSubmitter;

impl Submitter for NeverSubmitter {
    async fn submit(
        &self,
        _asset: &ResolvedAsset,
        _to: Option<String>,
        _watch_interval: f64,
        _args: Option<Map<String, Value>>,
    ) -> Result<Value> {
        Err(client_err("dry-run never submits"))
    }
}

fn render_event(event: &Value) {
    let kind = event.get("event").and_then(Value::as_str).unwrap_or("");
    let text = |key: &str| {
        event
            .get(key)
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_default()
    };
    let git_ref = text("ref");
    let ref_suffix = if git_ref.is_empty() {
        String::new()
    } else {
        format!("@{git_ref}")
    };
    match kind {
        "plan" => {
            let mode = if event["dry_run"].as_bool().unwrap_or(false) {
                "Dry run"
            } else {
                "Applying"
            };
            println!(
                "{mode}: {} ({} assets, catalog {})",
                text("manifest"),
                text("total"),
                text("catalog")
            );
        }
        "orphan" => {
            let uri = extract_str(event, &["resource_uri"])
                .unwrap_or_else(|| "unknown resource".to_string());
            eprintln!(
                "! orphan: '{}' left the manifest but its resource remains ({uri}); \
                 OpenViking Assets never deletes automatically",
                text("name")
            );
        }
        "asset_planned" => {
            let watch = event["watch_interval"].as_f64().unwrap_or(0.0);
            let watch_suffix = if watch > 0.0 {
                format!(", watch={watch}min")
            } else {
                String::new()
            };
            println!(
                "[{}/{}] {}: {} (git:{}{ref_suffix}{watch_suffix})",
                text("index"),
                text("total"),
                text("action"),
                text("name"),
                text("locator")
            );
        }
        "asset_start" => {
            println!(
                "[{}/{}] {}: {} (git:{}{ref_suffix}) ...",
                text("index"),
                text("total"),
                text("action"),
                text("name"),
                text("locator")
            );
        }
        "asset_done" => {
            let uri = extract_str(event, &["resource_uri"])
                .unwrap_or_else(|| "(uri pending)".to_string());
            let task = extract_str(event, &["task_id"])
                .map(|t| format!(" task={t}"))
                .unwrap_or_default();
            println!("    -> {}: {uri}{task}", text("status"));
        }
        "asset_failed" => eprintln!("    -> FAILED: {}", text("error")),
        "asset_skipped" => println!(
            "[{}/{}] skipped: {} ({})",
            text("index"),
            text("total"),
            text("name"),
            text("reason")
        ),
        "summary" => {
            if event["dry_run"].as_bool().unwrap_or(false) {
                println!("Plan complete: {} asset(s) validated.", text("total"));
            } else {
                println!(
                    "Done: {} ok, {} failed, {} not attempted (state: {})",
                    text("succeeded"),
                    text("failed"),
                    text("not_attempted"),
                    text("state")
                );
            }
        }
        _ => {}
    }
}

/// Entry point for `ov add-resource -m <manifest>`.
pub async fn handle_manifest_apply(
    manifest: String,
    catalog: Option<String>,
    options: ManifestRunOptions,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let manifest_path = PathBuf::from(&manifest);
    let catalog_path = catalog.map(PathBuf::from).unwrap_or_else(|| {
        manifest_path
            .parent()
            .unwrap_or(Path::new("."))
            .join(DEFAULT_CATALOG_FILENAME)
    });
    let manifest_yaml = std::fs::read_to_string(&manifest_path).map_err(|e| {
        client_err(format!(
            "cannot read manifest '{}': {e}",
            manifest_path.display()
        ))
    })?;
    let catalog_yaml = std::fs::read_to_string(&catalog_path).map_err(|e| {
        client_err(format!(
            "cannot read catalog '{}': {e}; pass --catalog <file> when it is not next to the manifest",
            catalog_path.display()
        ))
    })?;

    let effective_timeout = if options.wait {
        timeout.unwrap_or(60.0).max(ctx.config.timeout)
    } else {
        ctx.config.timeout
    };
    let client = ctx.get_client_with_timeout(Some(effective_timeout));
    let resolved: ResolveResponse = client
        .post(
            "/api/v1/openviking-assets/resolve",
            &json!({
                "manifest_yaml": manifest_yaml,
                "catalog_yaml": catalog_yaml,
                "manifest_label": manifest_path.display().to_string(),
                "catalog_label": catalog_path.display().to_string(),
            }),
        )
        .await?;

    let json_mode = matches!(ctx.output_format, OutputFormat::Json);
    let mut emit = |event: Value| {
        if json_mode {
            println!("{event}");
        } else {
            render_event(&event);
        }
    };

    let credentials_file = credentials_path();
    let summary = if options.dry_run {
        apply_manifest_core(
            &manifest_path,
            &catalog_path,
            &resolved.assets,
            &credentials_file,
            &options,
            &NeverSubmitter,
            &mut emit,
        )
        .await?
    } else {
        let submitter = HttpSubmitter {
            client,
            wait: options.wait,
            timeout,
        };
        apply_manifest_core(
            &manifest_path,
            &catalog_path,
            &resolved.assets,
            &credentials_file,
            &options,
            &submitter,
            &mut emit,
        )
        .await?
    };

    if summary.all_failed() {
        return Err(client_err(
            "every asset failed; nothing was applied successfully",
        ));
    }
    if !summary.failed.is_empty() {
        let names = summary
            .failed
            .keys()
            .cloned()
            .collect::<Vec<_>>()
            .join(", ");
        return Err(client_err(format!(
            "{} asset(s) failed: {names}",
            summary.failed.len()
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    type SubmitCall = (String, Option<String>, f64, Option<Map<String, Value>>);

    fn write(dir: &Path, name: &str, content: &str) -> PathBuf {
        let path = dir.join(name);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(&path, content).unwrap();
        path
    }

    fn workspace() -> (tempfile::TempDir, PathBuf, PathBuf, Vec<ResolvedAsset>) {
        let dir = tempfile::tempdir().unwrap();
        let manifest = dir.path().join("kb.yaml");
        let catalog = dir.path().join("assets.yaml");
        let assets = vec![
            ResolvedAsset {
                name: "alpha".into(),
                connector: "git".into(),
                repo_url: "https://github.com/org/alpha".into(),
                branch: Some("main".into()),
                auth_ref: None,
                watch_interval: 30.0,
                locator: "github.com/org/alpha".into(),
                git_ref: "main".into(),
                asset_id: "alpha-id".into(),
            },
            ResolvedAsset {
                name: "beta".into(),
                connector: "git".into(),
                repo_url: "https://github.com/org/beta".into(),
                branch: None,
                auth_ref: None,
                watch_interval: 30.0,
                locator: "github.com/org/beta".into(),
                git_ref: String::new(),
                asset_id: "beta-id".into(),
            },
            ResolvedAsset {
                name: "gamma".into(),
                connector: "git".into(),
                repo_url: "git@github.com:org/gamma.git".into(),
                branch: Some("dev".into()),
                auth_ref: None,
                watch_interval: 0.0,
                locator: "github.com/org/gamma".into(),
                git_ref: "dev".into(),
                asset_id: "gamma-id".into(),
            },
        ];
        (dir, manifest, catalog, assets)
    }

    #[test]
    fn state_roundtrip_and_orphans() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = dir.path().join("kb.yaml");
        let mut state = load_state(&manifest).unwrap();
        state.record(
            "abc",
            AssetStateEntry {
                name: "repo".into(),
                connector: "git".into(),
                locator: "github.com/org/repo".into(),
                git_ref: "main".into(),
                status: "submitted".into(),
                resource_uri: Some("viking://resources/repo".into()),
                task_id: Some("task-1".into()),
                ..Default::default()
            },
        );
        state.save().unwrap();

        let loaded = load_state(&manifest).unwrap();
        assert_eq!(
            loaded.assets["abc"].resource_uri.as_deref(),
            Some("viking://resources/repo")
        );
        assert!(loaded.assets["abc"].last_applied_at.is_some());
        let ids: HashSet<String> = HashSet::new();
        assert_eq!(loaded.orphans(&ids).len(), 1);

        std::fs::write(state_path_for(&manifest), "{not json").unwrap();
        assert!(load_state(&manifest).is_err());
        std::fs::write(
            state_path_for(&manifest),
            r#"{"protocol": "openviking-assets-state/99", "assets": {}}"#,
        )
        .unwrap();
        let err = load_state(&manifest).unwrap_err().to_string();
        assert!(err.contains("unsupported protocol"), "{err}");
    }

    struct FakeSubmitter {
        fail_names: Vec<&'static str>,
        calls: Mutex<Vec<SubmitCall>>,
    }

    impl FakeSubmitter {
        fn new(fail_names: Vec<&'static str>) -> Self {
            Self {
                fail_names,
                calls: Mutex::new(Vec::new()),
            }
        }
    }

    impl Submitter for FakeSubmitter {
        async fn submit(
            &self,
            asset: &ResolvedAsset,
            to: Option<String>,
            watch_interval: f64,
            args: Option<Map<String, Value>>,
        ) -> Result<Value> {
            self.calls
                .lock()
                .unwrap()
                .push((asset.name.clone(), to, watch_interval, args));
            if self.fail_names.contains(&asset.name.as_str()) {
                return Err(client_err(format!("boom: {}", asset.name)));
            }
            Ok(json!({
                "uri": format!("viking://resources/{}", asset.name),
                "task_id": format!("task-{}", asset.name),
            }))
        }
    }

    fn run_opts() -> ManifestRunOptions {
        ManifestRunOptions::default()
    }

    async fn run(
        manifest: &Path,
        catalog: &Path,
        assets: &[ResolvedAsset],
        creds: &Path,
        opts: &ManifestRunOptions,
        submitter: &FakeSubmitter,
    ) -> (ApplySummary, Vec<Value>) {
        let mut events = Vec::new();
        let summary = {
            let mut emit = |event: Value| events.push(event);
            apply_manifest_core(manifest, catalog, assets, creds, opts, submitter, &mut emit)
                .await
                .unwrap()
        };
        (summary, events)
    }

    #[tokio::test]
    async fn fresh_apply_then_sync() {
        let (dir, manifest, catalog, assets) = workspace();
        let creds = dir.path().join("no-creds.yaml");
        let submitter = FakeSubmitter::new(vec![]);
        let (summary, _) = run(
            &manifest,
            &catalog,
            &assets,
            &creds,
            &run_opts(),
            &submitter,
        )
        .await;
        assert_eq!(summary.succeeded, ["alpha", "beta", "gamma"]);
        {
            let calls = submitter.calls.lock().unwrap();
            assert!(calls.iter().all(|(_, to, _, _)| to.is_none()));
            assert_eq!(calls[0].2, 30.0);
            assert_eq!(calls[2].2, 0.0);
            let alpha_args = calls[0].3.as_ref().unwrap();
            assert_eq!(alpha_args["branch"], json!("main"));
            assert!(calls[1].3.is_none());
        }

        // Second run syncs with to=<uri> from state.
        let submitter2 = FakeSubmitter::new(vec![]);
        let (summary2, events) = run(
            &manifest,
            &catalog,
            &assets,
            &creds,
            &run_opts(),
            &submitter2,
        )
        .await;
        assert_eq!(summary2.succeeded.len(), 3);
        let calls = submitter2.calls.lock().unwrap();
        assert_eq!(calls[0].1.as_deref(), Some("viking://resources/alpha"));
        let actions: Vec<&str> = events
            .iter()
            .filter(|e| e["event"] == "asset_done")
            .map(|e| e["action"].as_str().unwrap())
            .collect();
        assert_eq!(actions, ["sync", "sync", "sync"]);
    }

    #[tokio::test]
    async fn fail_fast_and_skip_failed() {
        let (dir, manifest, catalog, assets) = workspace();
        let creds = dir.path().join("no-creds.yaml");

        let submitter = FakeSubmitter::new(vec!["beta"]);
        let (summary, _) = run(
            &manifest,
            &catalog,
            &assets,
            &creds,
            &run_opts(),
            &submitter,
        )
        .await;
        assert_eq!(summary.succeeded, ["alpha"]);
        assert!(summary.failed.contains_key("beta"));
        assert_eq!(summary.not_attempted, ["gamma"]);
        assert_eq!(submitter.calls.lock().unwrap().len(), 2);

        let submitter = FakeSubmitter::new(vec!["beta"]);
        let opts = ManifestRunOptions {
            skip_failed: true,
            ..run_opts()
        };
        let (summary, _) = run(&manifest, &catalog, &assets, &creds, &opts, &submitter).await;
        assert_eq!(summary.succeeded, ["alpha", "gamma"]);
        assert!(!summary.all_failed());

        let submitter = FakeSubmitter::new(vec!["alpha", "beta", "gamma"]);
        let (summary, _) = run(&manifest, &catalog, &assets, &creds, &opts, &submitter).await;
        assert!(summary.all_failed());
    }

    #[tokio::test]
    async fn dry_run_submits_nothing_and_writes_no_state() {
        let (dir, manifest, catalog, assets) = workspace();
        let creds = dir.path().join("no-creds.yaml");
        let submitter = FakeSubmitter::new(vec![]);
        let opts = ManifestRunOptions {
            dry_run: true,
            ..run_opts()
        };
        let (summary, events) = run(&manifest, &catalog, &assets, &creds, &opts, &submitter).await;
        assert_eq!(summary.total, 3);
        assert!(submitter.calls.lock().unwrap().is_empty());
        assert!(!state_path_for(&manifest).exists());
        let planned = events
            .iter()
            .filter(|e| e["event"] == "asset_planned")
            .count();
        assert_eq!(planned, 3);
    }

    #[tokio::test]
    async fn orphan_reported_but_kept() {
        let (dir, manifest, catalog, assets) = workspace();
        let creds = dir.path().join("no-creds.yaml");
        let submitter = FakeSubmitter::new(vec![]);
        run(
            &manifest,
            &catalog,
            &assets,
            &creds,
            &run_opts(),
            &submitter,
        )
        .await;

        let submitter = FakeSubmitter::new(vec![]);
        let (_, events) = run(
            &manifest,
            &catalog,
            &assets[..2],
            &creds,
            &run_opts(),
            &submitter,
        )
        .await;
        let orphans: Vec<&Value> = events.iter().filter(|e| e["event"] == "orphan").collect();
        assert_eq!(orphans.len(), 1);
        assert_eq!(orphans[0]["name"], json!("gamma"));
        assert_eq!(load_state(&manifest).unwrap().assets.len(), 3);
    }

    #[tokio::test]
    async fn auth_ref_precheck_and_merge() {
        let (dir, manifest, catalog, mut assets) = workspace();
        assets[0].auth_ref = Some("team-git".into());

        // Missing alias fails before anything is submitted.
        let submitter = FakeSubmitter::new(vec![]);
        let creds = dir.path().join("no-creds.yaml");
        let mut emit = |_event: Value| {};
        let err = apply_manifest_core(
            &manifest,
            &catalog,
            &assets,
            &creds,
            &run_opts(),
            &submitter,
            &mut emit,
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(err.contains("team-git"), "{err}");
        assert!(submitter.calls.lock().unwrap().is_empty());

        // Resolvable alias merges its args into the submit args.
        let creds = write(
            dir.path(),
            "creds.yaml",
            "credentials:\n  team-git:\n    token: sekrit\n",
        );
        let submitter = FakeSubmitter::new(vec![]);
        run(
            &manifest,
            &catalog,
            &assets,
            &creds,
            &run_opts(),
            &submitter,
        )
        .await;
        let calls = submitter.calls.lock().unwrap();
        let alpha_args = calls[0].3.as_ref().unwrap();
        assert_eq!(alpha_args["token"], json!("sekrit"));
        assert_eq!(alpha_args["branch"], json!("main"));
    }
}
