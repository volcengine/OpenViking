//! OpenViking Assets manifest mode for `ov add-resource -m <manifest.yaml>`.
//!
//! Implements the declaration layer of the OpenViking Assets protocol
//! (`openviking-assets/1`). Two keys carry the whole model: `catalog:` always
//! holds asset definitions — inline in the manifest, or in a separate
//! `catalog.yaml` shared by several manifests — and `assets:` always holds the
//! names this build applies, defaulting to all of them.
//! Validation is strict by design — unknown
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
const DEFAULT_CATALOG_FILENAME: &str = "catalog.yaml";
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

#[derive(Debug, Clone)]
pub struct ManifestRunOptions {
    pub dry_run: bool,
    pub skip_failed: bool,
    pub wait: bool,
    /// Overrides per-asset / catalog-default values when set.
    pub watch_interval: Option<f64>,
    pub processing_mode: String,
    /// Route assets through an external Connector service instead of the
    /// standard ingestion pipeline.
    pub external_connector: bool,
}

/// The `add_type` to submit for one asset.
///
/// Declaring it routes the source to an external Connector service, so it is
/// sent only when the caller asked for that. The standard pipeline resolves
/// the source from the URL itself and rejects `add_type` without an exact
/// `to` target, which a first-time create does not have.
fn effective_add_type(external_connector: bool, connector: &str) -> Option<String> {
    external_connector.then(|| connector.to_string())
}

/// Where one asset lands.
///
/// A resource already recorded in State is synced in place. A first create
/// normally lets the server name the resource, but a declared `add_type`
/// requires an exact target, so external-Connector runs derive one from the
/// asset name — unique within a manifest and already URI-safe.
fn target_uri(
    existing: Option<String>,
    external_connector: bool,
    asset_name: &str,
) -> Option<String> {
    existing.or_else(|| external_connector.then(|| format!("viking://resources/{asset_name}")))
}

impl Default for ManifestRunOptions {
    fn default() -> Self {
        Self {
            dry_run: false,
            skip_failed: false,
            wait: false,
            watch_interval: None,
            processing_mode: "semantic_and_vectors".to_string(),
            external_connector: false,
        }
    }
}

/// `--args` keys that are manifest-run options, consumed entirely by the CLI.
/// They never reach the server: the submitted per-asset args come only from
/// the catalog's params and credential resolution. Single-resource mode
/// rejects these keys outright so a forgotten `-m` fails instead of sending
/// them to the server as parser options.
pub const MANIFEST_RUN_ARG_KEYS: &[&str] =
    &["catalog", "dry_run", "skip_failed", EXTERNAL_CONNECTOR_KEY];

/// Internal switch, deliberately absent from help and user docs.
const EXTERNAL_CONNECTOR_KEY: &str = "external_connector";

/// Manifest-run options carried by `--args` in manifest mode.
#[derive(Debug, Default)]
pub struct ManifestArgs {
    pub catalog: Option<String>,
    pub dry_run: bool,
    pub skip_failed: bool,
    pub external_connector: bool,
}

pub fn parse_manifest_run_args(args: Option<&Map<String, Value>>) -> Result<ManifestArgs> {
    let mut run = ManifestArgs::default();
    let Some(args) = args else {
        return Ok(run);
    };
    for (key, value) in args {
        match key.as_str() {
            "catalog" => match value {
                Value::String(path) if !path.trim().is_empty() => {
                    run.catalog = Some(path.clone());
                }
                _ => return Err(client_err("--args catalog expects a file path.")),
            },
            "dry_run" => run.dry_run = manifest_bool_arg(key, value)?,
            "skip_failed" => run.skip_failed = manifest_bool_arg(key, value)?,
            EXTERNAL_CONNECTOR_KEY => run.external_connector = manifest_bool_arg(key, value)?,
            _ => {
                return Err(client_err(format!(
                    "unknown manifest-run option '{key}' in --args; supported: \
                     catalog, dry_run, skip_failed"
                )));
            }
        }
    }
    Ok(run)
}

fn manifest_bool_arg(key: &str, value: &Value) -> Result<bool> {
    value
        .as_bool()
        .ok_or_else(|| client_err(format!("--args {key} expects true or false.")))
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
    async fn preflight(
        &self,
        asset: &ResolvedAsset,
        args: Option<Map<String, Value>>,
    ) -> Result<Value>;

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

fn git_auth_config(args: Option<&Map<String, Value>>) -> Option<Value> {
    let args = args?;
    let mut auth = Map::new();
    for key in ["username", "token"] {
        if let Some(value) = args.get(key) {
            auth.insert(key.to_string(), value.clone());
        }
    }
    if auth.is_empty() {
        None
    } else {
        Some(Value::Object(auth))
    }
}

/// Preflight and then apply (or dry-run) a resolved manifest.
///
/// Every source is checked before the first mutation. A preflight failure
/// therefore aborts immediately even when `skip_failed` is set.
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

    // Source access is part of validation, including dry-run. Complete every
    // preflight before submitting the first asset so permission failures never
    // leave a partially applied manifest.
    for (index, asset) in assets.iter().enumerate() {
        let base = json!({
            "index": index + 1,
            "total": assets.len(),
            "name": asset.name,
            "connector": asset.connector,
            "locator": asset.locator,
            "ref": asset.git_ref,
        });
        emit({
            let mut event = base.as_object().cloned().unwrap_or_default();
            event.insert("event".to_string(), json!("asset_preflight_start"));
            Value::Object(event)
        });
        let args = build_args(asset, &credential_args[&asset.name]);
        match submitter.preflight(asset, args).await {
            Ok(_) => emit({
                let mut event = base.as_object().cloned().unwrap_or_default();
                event.insert("event".to_string(), json!("asset_preflight_ok"));
                Value::Object(event)
            }),
            Err(err) => {
                emit({
                    let mut event = base.as_object().cloned().unwrap_or_default();
                    event.insert("event".to_string(), json!("asset_preflight_failed"));
                    event.insert("error".to_string(), json!(err.to_string()));
                    Value::Object(event)
                });
                return Err(err);
            }
        }
    }

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
            .submit(
                asset,
                target_uri(
                    entry.resource_uri.clone(),
                    options.external_connector,
                    &asset.name,
                ),
                watch_interval,
                args,
            )
            .await
        {
            Ok(response) => {
                entry.status = if options.wait { "ok" } else { "submitted" }.to_string();
                entry.error = None;
                if let Some(uri) =
                    extract_str(&response, &["root_uri", "uri", "resource_uri", "to"])
                {
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
    processing_mode: String,
    external_connector: bool,
}

impl Submitter for HttpSubmitter {
    async fn preflight(
        &self,
        asset: &ResolvedAsset,
        args: Option<Map<String, Value>>,
    ) -> Result<Value> {
        self.client
            .post(
                "/api/v1/openviking-assets/preflight",
                &json!({
                    "name": asset.name,
                    "connector": asset.connector,
                    "repo_url": asset.repo_url,
                    "branch": asset.branch,
                    "auth_config": git_auth_config(args.as_ref()),
                }),
            )
            .await
    }

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
                effective_add_type(self.external_connector, &asset.connector),
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
                self.processing_mode.clone(),
                args,
                Vec::new(),
                "replace".to_string(),
                false,
                false,
            )
            .await
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
        "asset_preflight_start" => {
            println!(
                "[{}/{}] checking access: {} (git:{}{ref_suffix}) ...",
                text("index"),
                text("total"),
                text("name"),
                text("locator")
            );
        }
        "asset_preflight_ok" => println!("    -> access ok"),
        "asset_preflight_failed" => eprintln!("    -> ACCESS FAILED: {}", text("error")),
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
                println!(
                    "Plan complete: {} asset(s) validated, including source access.",
                    text("total")
                );
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

/// True when the manifest defines its assets directly under a top-level
/// `catalog:` list, so no separate catalog file is needed. The server owns
/// full validation; this only decides whether to attach a catalog file.
fn manifest_defines_assets(manifest_yaml: &str) -> bool {
    serde_yaml::from_str::<serde_yaml::Value>(manifest_yaml)
        .map(|doc| {
            doc.get("catalog")
                .is_some_and(serde_yaml::Value::is_sequence)
        })
        .unwrap_or(false)
}

/// Entry point for `ov add-resource -m <manifest>`.
pub async fn handle_manifest_apply(
    manifest: String,
    catalog_file: Option<String>,
    options: ManifestRunOptions,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let manifest_path = PathBuf::from(&manifest);
    let manifest_yaml = std::fs::read_to_string(&manifest_path).map_err(|e| {
        client_err(format!(
            "cannot read manifest '{}': {e}",
            manifest_path.display()
        ))
    })?;
    let catalog_path = match catalog_file {
        Some(path) => Some(PathBuf::from(path)),
        None if manifest_defines_assets(&manifest_yaml) => None,
        None => Some(
            manifest_path
                .parent()
                .unwrap_or(Path::new("."))
                .join(DEFAULT_CATALOG_FILENAME),
        ),
    };
    let catalog_yaml = catalog_path
        .as_ref()
        .map(|path| {
            std::fs::read_to_string(path).map_err(|e| {
                client_err(format!(
                    "cannot read asset definitions '{}': {e}; define assets under 'catalog' in the manifest or pass --catalog <file>",
                    path.display()
                ))
            })
        })
        .transpose()?;

    let effective_timeout = if options.wait {
        timeout.unwrap_or(60.0).max(ctx.config.timeout)
    } else {
        ctx.config.timeout.max(20.0)
    };
    let client = ctx.get_client_with_timeout(Some(effective_timeout));
    let mut request = json!({
        "manifest_yaml": manifest_yaml,
        "manifest_label": manifest_path.display().to_string(),
    });
    if let (Some(path), Some(yaml)) = (&catalog_path, &catalog_yaml) {
        request["catalog_yaml"] = json!(yaml);
        request["catalog_label"] = json!(path.display().to_string());
    }
    let resolved: ResolveResponse = client
        .post("/api/v1/openviking-assets/resolve", &request)
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
    let submitter = HttpSubmitter {
        client,
        wait: options.wait,
        timeout,
        processing_mode: options.processing_mode.clone(),
        external_connector: options.external_connector,
    };
    let summary = apply_manifest_core(
        &manifest_path,
        // Single-file manifests are their own catalog in plan output.
        catalog_path.as_deref().unwrap_or(&manifest_path),
        &resolved.assets,
        &credentials_file,
        &options,
        &submitter,
        &mut emit,
    )
    .await?;

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

    #[test]
    fn add_type_is_declared_only_for_external_connector_runs() {
        // The standard pipeline resolves the source from the URL and rejects
        // add_type without an exact `to`, which a first create does not have.
        assert_eq!(effective_add_type(false, "git"), None);
        assert_eq!(effective_add_type(true, "git"), Some("git".to_string()));
        assert_eq!(effective_add_type(true, "tos"), Some("tos".to_string()));
    }

    #[test]
    fn manifest_run_args_parse_known_keys() {
        let args = serde_json::json!({
            "catalog": "shared/catalog.yaml",
            "dry_run": true,
            "skip_failed": true,
            "external_connector": true,
        });
        let run = parse_manifest_run_args(args.as_object()).unwrap();
        assert_eq!(run.catalog.as_deref(), Some("shared/catalog.yaml"));
        assert!(run.dry_run && run.skip_failed && run.external_connector);

        let run = parse_manifest_run_args(None).unwrap();
        assert_eq!(run.catalog, None);
        assert!(!run.dry_run && !run.skip_failed && !run.external_connector);
    }

    #[test]
    fn manifest_run_args_reject_unknown_keys_and_bad_types() {
        // Catalog params (e.g. git branch) belong in the catalog file, not
        // here; an unknown key must not silently do nothing.
        let unknown = serde_json::json!({"branch": "main"});
        let err = parse_manifest_run_args(unknown.as_object()).unwrap_err();
        assert!(err.to_string().contains("branch"), "{err}");
        assert!(
            err.to_string().contains("skip_failed"),
            "should list keys: {err}"
        );
        // The internal switch stays out of the supported-keys hint.
        assert!(!err.to_string().contains("external_connector"), "{err}");

        let bad_bool = serde_json::json!({"dry_run": "yes"});
        assert!(parse_manifest_run_args(bad_bool.as_object()).is_err());
        let bad_catalog = serde_json::json!({"catalog": true});
        assert!(parse_manifest_run_args(bad_catalog.as_object()).is_err());
    }

    #[test]
    fn creates_get_an_exact_target_only_for_external_connector_runs() {
        // Standard runs let the server name a new resource.
        assert_eq!(target_uri(None, false, "alpha"), None);
        // A declared add_type requires an exact target, derived from the name.
        assert_eq!(
            target_uri(None, true, "alpha"),
            Some("viking://resources/alpha".to_string())
        );
        // An existing resource is synced in place under either mode.
        let existing = Some("viking://resources/renamed".to_string());
        assert_eq!(target_uri(existing.clone(), false, "alpha"), existing);
        assert_eq!(target_uri(existing.clone(), true, "alpha"), existing);
    }

    #[test]
    fn manifest_defines_assets_detects_catalog_lists_only() {
        assert!(manifest_defines_assets(
            "protocol: openviking-assets/1\ncatalog:\n  - name: alpha\n    connector: git\n"
        ));
        assert!(!manifest_defines_assets("assets:\n  - alpha\n"));
        assert!(!manifest_defines_assets(
            "catalog: ../catalog.yaml\nassets:\n  - alpha\n"
        ));
        assert!(!manifest_defines_assets(": not yaml ["));
    }

    type PreflightCall = (String, Option<Map<String, Value>>);
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
        let manifest = dir.path().join("manifest.yaml");
        let catalog = dir.path().join("catalog.yaml");
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
        let manifest = dir.path().join("manifest.yaml");
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
        preflight_fail_names: Vec<&'static str>,
        fail_names: Vec<&'static str>,
        preflight_calls: Mutex<Vec<PreflightCall>>,
        calls: Mutex<Vec<SubmitCall>>,
    }

    impl FakeSubmitter {
        fn new(fail_names: Vec<&'static str>) -> Self {
            Self {
                preflight_fail_names: Vec::new(),
                fail_names,
                preflight_calls: Mutex::new(Vec::new()),
                calls: Mutex::new(Vec::new()),
            }
        }

        fn with_preflight_fail(preflight_fail_names: Vec<&'static str>) -> Self {
            Self {
                preflight_fail_names,
                fail_names: Vec::new(),
                preflight_calls: Mutex::new(Vec::new()),
                calls: Mutex::new(Vec::new()),
            }
        }
    }

    impl Submitter for FakeSubmitter {
        async fn preflight(
            &self,
            asset: &ResolvedAsset,
            args: Option<Map<String, Value>>,
        ) -> Result<Value> {
            self.preflight_calls
                .lock()
                .unwrap()
                .push((asset.name.clone(), args));
            if self.preflight_fail_names.contains(&asset.name.as_str()) {
                return Err(client_err(format!("access denied: {}", asset.name)));
            }
            Ok(json!({"accessible": true}))
        }

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
                "root_uri": format!("viking://resources/{}", asset.name),
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
        assert_eq!(submitter.preflight_calls.lock().unwrap().len(), 3);
        assert!(submitter.calls.lock().unwrap().is_empty());
        assert!(!state_path_for(&manifest).exists());
        let planned = events
            .iter()
            .filter(|e| e["event"] == "asset_planned")
            .count();
        assert_eq!(planned, 3);
    }

    #[tokio::test]
    async fn preflight_failure_aborts_before_any_submission_or_state_write() {
        let (dir, manifest, catalog, assets) = workspace();
        let creds = dir.path().join("no-creds.yaml");
        let submitter = FakeSubmitter::with_preflight_fail(vec!["beta"]);
        let mut events = Vec::new();
        let err = {
            let mut emit = |event: Value| events.push(event);
            apply_manifest_core(
                &manifest,
                &catalog,
                &assets,
                &creds,
                &ManifestRunOptions {
                    skip_failed: true,
                    ..run_opts()
                },
                &submitter,
                &mut emit,
            )
            .await
            .unwrap_err()
        };

        assert!(err.to_string().contains("access denied: beta"));
        assert_eq!(submitter.preflight_calls.lock().unwrap().len(), 2);
        assert!(submitter.calls.lock().unwrap().is_empty());
        assert!(!state_path_for(&manifest).exists());
        assert!(
            events
                .iter()
                .any(|event| event["event"] == "asset_preflight_failed")
        );
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
        let preflight_calls = submitter.preflight_calls.lock().unwrap();
        let alpha_preflight_args = preflight_calls[0].1.as_ref().unwrap();
        assert_eq!(alpha_preflight_args["token"], json!("sekrit"));
        drop(preflight_calls);
        let calls = submitter.calls.lock().unwrap();
        let alpha_args = calls[0].3.as_ref().unwrap();
        assert_eq!(alpha_args["token"], json!("sekrit"));
        assert_eq!(alpha_args["branch"], json!("main"));
    }
}
