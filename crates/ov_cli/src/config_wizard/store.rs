use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};

use serde_json::Value;
use url::Url;

use crate::{
    base_client::BaseClient,
    config::{Config, DEFAULT_SELF_MANAGED_PORT, default_config_path},
    error::{Error, Result},
};

pub const VOLCENGINE_CLOUD_URL: &str = "https://api.vikingdb.cn-beijing.volces.com/openviking";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigKind {
    VolcengineCloud,
    SelfManaged,
}

impl ConfigKind {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::VolcengineCloud => "VolcEngine Cloud",
            Self::SelfManaged => "Self-Managed",
        }
    }

    pub(crate) fn from_config(config: &Config) -> Self {
        if config.url.trim_end_matches('/') == VOLCENGINE_CLOUD_URL {
            Self::VolcengineCloud
        } else {
            Self::SelfManaged
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ApiKeyRole {
    Root,
    Regular,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum IdentityField {
    Account,
    User,
}

#[derive(Debug, Clone)]
pub struct ConfigDraft {
    pub name: String,
    pub kind: ConfigKind,
    pub url: String,
    pub api_key: Option<String>,
    pub root_api_key: Option<String>,
    pub account: Option<String>,
    pub user: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ConfigEntry {
    pub name: String,
    pub config: Config,
    pub is_active: bool,
    pub kind: ConfigKind,
}

#[derive(Debug, Clone)]
pub struct InvalidSavedConfig {
    pub name: String,
    pub path: PathBuf,
}

#[derive(Debug, Clone)]
pub struct ConfigListReport {
    pub configs: Vec<ConfigEntry>,
    pub invalid_configs: Vec<InvalidSavedConfig>,
}

#[derive(Debug, Clone)]
pub struct ConfigStore {
    config_dir: PathBuf,
    active_path: PathBuf,
}

impl ConfigStore {
    pub fn new() -> Result<Self> {
        let active_path = default_config_path()?;
        let config_dir = active_path
            .parent()
            .ok_or_else(|| Error::Config("Could not determine config directory".to_string()))?
            .to_path_buf();
        Ok(Self {
            config_dir,
            active_path,
        })
    }

    #[cfg(test)]
    pub fn for_config_dir(config_dir: PathBuf) -> Self {
        Self {
            active_path: config_dir.join("ovcli.conf"),
            config_dir,
        }
    }

    pub fn active_path(&self) -> &Path {
        &self.active_path
    }

    pub fn config_dir(&self) -> &Path {
        &self.config_dir
    }

    pub fn saved_config_path(&self, name: &str) -> Result<PathBuf> {
        validate_config_name(name)?;
        Ok(self.config_dir.join(format!("ovcli.conf.{name}")))
    }

    pub fn load_active(&self) -> Result<Option<Config>> {
        if !self.active_path.exists() {
            return Ok(None);
        }
        Ok(Some(Config::from_file(
            &self.active_path.to_string_lossy(),
        )?))
    }

    pub(crate) fn load_saved_config(&self, name: &str) -> Result<Config> {
        let path = self.saved_config_path(name)?;
        if !path.exists() {
            return Err(Error::Config(format!("Config '{name}' does not exist")));
        }
        Config::from_file(&path.to_string_lossy())
            .map_err(|e| Error::Config(format!("Failed to read config '{name}': {e}")))
    }

    pub fn list_configs(&self) -> Result<Vec<ConfigEntry>> {
        let report = self.list_configs_report()?;
        for invalid in &report.invalid_configs {
            eprintln!(
                "Warning: skipped invalid OpenViking config '{}'",
                invalid.path.display()
            );
        }
        Ok(report.configs)
    }

    pub fn list_configs_report(&self) -> Result<ConfigListReport> {
        let mut configs = Vec::new();
        let mut invalid_configs = Vec::new();

        if !self.config_dir.exists() {
            return Ok(ConfigListReport {
                configs,
                invalid_configs,
            });
        }

        for entry in fs::read_dir(&self.config_dir)? {
            let entry = entry?;
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let Some(filename) = path.file_name().and_then(|value| value.to_str()) else {
                continue;
            };
            let Some(name) = filename.strip_prefix("ovcli.conf.") else {
                continue;
            };
            if name == "bak" || validate_config_name(name).is_err() {
                continue;
            }

            let Ok(config) = Config::from_file(&path.to_string_lossy()) else {
                invalid_configs.push(InvalidSavedConfig {
                    name: name.to_string(),
                    path,
                });
                continue;
            };

            configs.push(ConfigEntry {
                name: name.to_string(),
                is_active: false,
                kind: ConfigKind::from_config(&config),
                config,
            });
        }

        if !configs.is_empty() {
            let active_config = self.load_active()?;
            for entry in &mut configs {
                entry.is_active = match active_config.as_ref() {
                    Some(active) => configs_equivalent(active, &entry.config)?,
                    None => false,
                };
            }
        }

        configs.sort_by(|left, right| left.name.cmp(&right.name));
        invalid_configs.sort_by(|left, right| left.name.cmp(&right.name));
        normalize_active_config(&mut configs);
        Ok(ConfigListReport {
            configs,
            invalid_configs,
        })
    }

    pub fn save_named_config(&self, name: &str, config: &Config) -> Result<()> {
        let path = self.saved_config_path(name)?;
        write_config_file(&path, config)
    }

    pub fn activate_config(&self, name: &str) -> Result<()> {
        let path = self.saved_config_path(name)?;
        if !path.exists() {
            return Err(Error::Config(format!("Config '{name}' does not exist")));
        }
        let content = fs::read(&path)
            .map_err(|e| Error::Config(format!("Failed to read config '{name}': {e}")))?;
        write_file_atomically(&self.active_path, &content)
    }

    pub fn save_and_activate(&self, name: &str, config: &Config) -> Result<()> {
        self.save_named_config(name, config)?;
        write_config_file(&self.active_path, config)
    }

    pub fn save_edited_config(
        &self,
        old_name: &str,
        new_name: &str,
        config: &Config,
    ) -> Result<()> {
        let old_path = self.saved_config_path(old_name)?;
        let new_path = self.saved_config_path(new_name)?;
        let was_active = self.is_config_name_active(old_name)?;

        if old_name != new_name && new_path.exists() {
            return Err(Error::Config(format!("Config '{new_name}' already exists")));
        }

        self.save_named_config(new_name, config)?;
        if was_active {
            // Keep the active config consistent before removing the old saved file. If cleanup
            // fails, the user may see both names, but the active config still points at the edit.
            write_config_file(&self.active_path, config)?;
        }

        if old_name != new_name && old_path.exists() {
            fs::remove_file(old_path).map_err(|e| {
                Error::Config(format!("Failed to remove old config '{old_name}': {e}"))
            })?;
        }

        Ok(())
    }

    pub fn delete_config(&self, name: &str) -> Result<()> {
        if self.is_config_name_active(name)? {
            return Err(Error::Config(
                "Cannot delete the active config. Switch to another config first.".to_string(),
            ));
        }

        let path = self.saved_config_path(name)?;
        fs::remove_file(&path)
            .map_err(|e| Error::Config(format!("Failed to delete config '{name}': {e}")))
    }

    pub fn is_config_name_active(&self, name: &str) -> Result<bool> {
        let Some(active_config) = self.load_active()? else {
            return Ok(false);
        };
        let path = self.saved_config_path(name)?;
        if !path.exists() {
            return Ok(false);
        }
        let Ok(saved_config) = Config::from_file(&path.to_string_lossy()) else {
            return Ok(false);
        };
        if !configs_equivalent(&active_config, &saved_config)? {
            return Ok(false);
        }

        // Saved configs are file copies, so duplicate files can be equivalent to the active
        // config. Keep the UI's single-active invariant by treating the first equivalent saved
        // config as active, matching list_configs().
        Ok(self
            .primary_equivalent_config_name(&active_config)?
            .is_some_and(|active_name| active_name == name))
    }

    fn primary_equivalent_config_name(&self, active_config: &Config) -> Result<Option<String>> {
        if !self.config_dir.exists() {
            return Ok(None);
        }

        let mut names = Vec::new();
        for entry in fs::read_dir(&self.config_dir)? {
            let entry = entry?;
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let Some(filename) = path.file_name().and_then(|value| value.to_str()) else {
                continue;
            };
            let Some(name) = filename.strip_prefix("ovcli.conf.") else {
                continue;
            };
            if name == "bak" || validate_config_name(name).is_err() {
                continue;
            }

            let Ok(config) = Config::from_file(&path.to_string_lossy()) else {
                eprintln!(
                    "Warning: skipped invalid OpenViking config '{}'",
                    path.display()
                );
                continue;
            };

            if configs_equivalent(active_config, &config)? {
                names.push(name.to_string());
            }
        }

        names.sort();
        Ok(names.into_iter().next())
    }
}

fn normalize_active_config(configs: &mut [ConfigEntry]) {
    let mut found_active = false;
    for entry in configs {
        if !entry.is_active {
            continue;
        }
        if found_active {
            entry.is_active = false;
        } else {
            found_active = true;
        }
    }
}

pub fn validate_config_name(name: &str) -> Result<()> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err(Error::Config("Config name cannot be empty".to_string()));
    }
    if trimmed != name {
        return Err(Error::Config(
            "Config name cannot start or end with whitespace".to_string(),
        ));
    }
    if trimmed.starts_with('.') {
        return Err(Error::Config(
            "Config name cannot start with '.'".to_string(),
        ));
    }
    if trimmed.contains('/') || trimmed.contains('\\') {
        return Err(Error::Config(
            "Config name cannot contain path separators".to_string(),
        ));
    }
    if !trimmed
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
    {
        return Err(Error::Config(
            "Config name can only contain letters, numbers, '-' and '_'".to_string(),
        ));
    }
    Ok(())
}

pub(crate) fn validate_account_id_value(value: &str) -> Result<()> {
    validate_identity_value(value, IdentityField::Account)
}

pub(crate) fn validate_user_id_value(value: &str) -> Result<()> {
    validate_identity_value(value, IdentityField::User)
}

pub(crate) fn validate_identity_value(value: &str, field: IdentityField) -> Result<()> {
    let field_name = match field {
        IdentityField::Account => "Account ID",
        IdentityField::User => "User ID",
    };
    let identifier_name = match field {
        IdentityField::Account => "account_id",
        IdentityField::User => "user_id",
    };
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(Error::Config(format!("{field_name} cannot be empty")));
    }
    if trimmed != value {
        return Err(Error::Config(format!(
            "{field_name} cannot start or end with whitespace"
        )));
    }
    if matches!(field, IdentityField::Account) && trimmed.starts_with('_') {
        return Err(Error::Config(
            "Account ID cannot start with '_'".to_string(),
        ));
    }
    if !trimmed
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | '@'))
    {
        return Err(Error::Config(format!(
            "{field_name} can only contain letters, numbers, '_', '-', '.', and '@'"
        )));
    }
    if trimmed.matches('@').count() > 1 {
        return Err(Error::Config(format!(
            "{identifier_name} must have at most one '@'"
        )));
    }
    Ok(())
}

pub fn build_config(draft: &ConfigDraft) -> Result<Config> {
    validate_config_name(&draft.name)?;

    let mut config = Config::default();
    let account = non_empty_option(draft.account.as_deref());
    let user = non_empty_option(draft.user.as_deref());
    match draft.kind {
        ConfigKind::VolcengineCloud => {
            let api_key = non_empty_option(draft.api_key.as_deref()).ok_or_else(|| {
                Error::Config("VolcEngine Cloud configs require an API key".to_string())
            })?;
            config.url = VOLCENGINE_CLOUD_URL.to_string();
            config.api_key = Some(api_key);
            config.root_api_key = None;
            config.account = account;
            config.user = user;
        }
        ConfigKind::SelfManaged => {
            let url = normalize_self_managed_url(&draft.url);
            if url.is_empty() {
                return Err(Error::Config(
                    "Self-managed configs require a server URL".to_string(),
                ));
            }
            let root_api_key = non_empty_option(draft.root_api_key.as_deref());
            let api_key =
                non_empty_option(draft.api_key.as_deref()).or_else(|| root_api_key.clone());
            if api_key.is_none() && self_managed_requires_api_key(&url) {
                return Err(Error::Config(
                    "Remote self-managed configs require an API key".to_string(),
                ));
            }
            config.url = url.trim_end_matches('/').to_string();
            config.api_key = api_key;
            config.root_api_key = root_api_key;
            config.account = account;
            config.user = user;
        }
    }

    Ok(config)
}

pub(crate) fn self_managed_requires_api_key(url: &str) -> bool {
    !self_managed_allows_empty_api_key(url)
}

pub(crate) fn self_managed_allows_empty_api_key(url: &str) -> bool {
    let normalized = normalize_self_managed_url(url);
    let Ok(parsed) = Url::parse(&normalized) else {
        return false;
    };
    matches!(
        parsed.host_str().map(|host| host.to_ascii_lowercase()),
        Some(host) if matches!(host.as_str(), "localhost" | "127.0.0.1" | "::1" | "[::1]")
    )
}

pub(crate) fn normalize_self_managed_url(url: &str) -> String {
    let trimmed = url.trim().trim_end_matches('/');
    if trimmed.eq_ignore_ascii_case("::1") || trimmed.eq_ignore_ascii_case("[::1]") {
        return format!("http://[::1]:{DEFAULT_SELF_MANAGED_PORT}");
    }
    if let Some(port) = trimmed.strip_prefix("[::1]:")
        && !port.trim().is_empty()
    {
        return format!("http://[::1]:{port}");
    }
    if let Some(port) = trimmed.strip_prefix("::1:")
        && !port.trim().is_empty()
    {
        return format!("http://[::1]:{port}");
    }

    if Url::parse(trimmed).is_ok() {
        return trimmed.to_string();
    }

    let (host, port) = trimmed.split_once(':').unwrap_or((trimmed, ""));
    if matches!(
        host.to_ascii_lowercase().as_str(),
        "localhost" | "127.0.0.1"
    ) {
        if port.trim().is_empty() {
            format!("http://{host}:{DEFAULT_SELF_MANAGED_PORT}")
        } else {
            format!("http://{host}:{port}")
        }
    } else {
        trimmed.to_string()
    }
}

pub fn redacted_config_value(config: &Config) -> Result<Value> {
    let mut value = serde_json::to_value(config)?;
    if let Some(object) = value.as_object_mut() {
        for key in ["api_key", "root_api_key"] {
            if object.get(key).is_some_and(|value| !value.is_null()) {
                object.insert(key.to_string(), Value::String("********".to_string()));
            }
        }
    }
    Ok(value)
}

pub async fn validate_config(config: &Config) -> Result<()> {
    validate_candidate_config(config, false).await
}

pub(crate) async fn validate_candidate_config(
    config: &Config,
    require_api_key: bool,
) -> Result<()> {
    validate_candidate_config_with_role(config, require_api_key)
        .await
        .map(|_| ())
}

pub(crate) async fn validate_candidate_config_with_role(
    config: &Config,
    require_api_key: bool,
) -> Result<Option<ApiKeyRole>> {
    let api_key = non_empty_option(config.api_key.as_deref());
    if require_api_key && api_key.is_none() {
        return Err(Error::Config("API key is required".to_string()));
    }

    let timeout = config.timeout.clamp(1.0, 10.0);
    let auth = config.effective_auth(false);
    let client = BaseClient::new(
        &config.url,
        auth.api_key.clone(),
        auth.account,
        auth.user,
        timeout,
        config.profile,
        config.extra_headers.clone(),
    );

    let value: Value = client.get("/health", &[]).await?;
    if value
        .get("healthy")
        .and_then(Value::as_bool)
        .is_some_and(|healthy| !healthy)
    {
        return Err(Error::Network(
            "Server responded but reported unhealthy status".to_string(),
        ));
    }

    if should_run_authenticated_probe(&value, require_api_key, auth.api_key.is_some()) {
        let _: Value = client.get("/api/v1/system/status", &[]).await?;
    }

    if auth.api_key.is_some() {
        return detect_api_key_role(&client).await.map(Some);
    }

    Ok(None)
}

async fn detect_api_key_role(client: &BaseClient) -> Result<ApiKeyRole> {
    match client.get::<Value>("/api/v1/admin/accounts", &[]).await {
        Ok(_) => Ok(ApiKeyRole::Root),
        Err(Error::Api {
            status: Some(status),
            ..
        }) if admin_probe_regular_key_status(status) => Ok(ApiKeyRole::Regular),
        Err(Error::Api { message, status }) => Err(Error::Api { message, status }),
        Err(error) => Err(error),
    }
}

fn admin_probe_regular_key_status(status: u16) -> bool {
    matches!(
        status,
        // 401/403 means the key works but does not have admin access.
        401 | 403
            // Some self-managed deployments may not expose the admin endpoint.
            // Treat explicit absence as regular access rather than blocking a
            // valid non-admin setup.
            | 404
    )
}

#[cfg(test)]
fn admin_probe_ambiguous_status(status: u16) -> bool {
    matches!(status, 408 | 429 | 500..=599)
}

fn should_run_authenticated_probe(
    health: &Value,
    require_api_key: bool,
    has_api_key: bool,
) -> bool {
    if require_api_key || has_api_key {
        return true;
    }

    match health.get("auth_mode").and_then(Value::as_str) {
        Some("dev") => false,
        Some("api_key" | "trusted") | None => true,
        Some(_) => false,
    }
}

pub(crate) fn validation_error_copy(kind: ConfigKind, _error: &Error) -> String {
    match kind {
        ConfigKind::VolcengineCloud => {
            "Validation failed. Check your API key and try again.".to_string()
        }
        ConfigKind::SelfManaged => {
            "Validation failed. Check the server URL and API key if required.".to_string()
        }
    }
}

fn write_config_file(path: &Path, config: &Config) -> Result<()> {
    let content = serde_json::to_string_pretty(config)?;
    write_file_atomically(path, content.as_bytes())
}

fn write_file_atomically(path: &Path, content: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| Error::Config("Could not determine config file directory".to_string()))?;
    fs::create_dir_all(parent)?;

    let mut temp_file = tempfile::Builder::new()
        .prefix(".ovcli.conf.")
        .suffix(".tmp")
        .tempfile_in(parent)?;
    temp_file.write_all(content)?;
    temp_file.flush()?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut permissions = temp_file.as_file().metadata()?.permissions();
        permissions.set_mode(0o600);
        temp_file.as_file().set_permissions(permissions)?;
    }

    temp_file.as_file().sync_all()?;
    temp_file.persist(path).map_err(|error| {
        Error::Config(format!("Failed to replace config file: {}", error.error))
    })?;

    Ok(())
}

pub(crate) fn configs_equivalent(left: &Config, right: &Config) -> Result<bool> {
    Ok(serde_json::to_value(left)? == serde_json::to_value(right)?)
}

fn non_empty_option(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToString::to_string)
}

#[cfg(test)]
mod tests {
    use super::{
        ApiKeyRole, ConfigDraft, ConfigKind, ConfigStore, VOLCENGINE_CLOUD_URL,
        admin_probe_ambiguous_status, admin_probe_regular_key_status, build_config,
        should_run_authenticated_probe, validate_candidate_config,
        validate_candidate_config_with_role, validate_config_name, validation_error_copy,
    };
    use crate::config::Config;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn unique_dir(name: &str) -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should be valid")
            .as_nanos();
        std::env::temp_dir().join(format!("openviking-config-{name}-{suffix}"))
    }

    fn sample_config(url: &str, api_key: Option<&str>) -> Config {
        let mut config = Config::default();
        config.url = url.to_string();
        config.api_key = api_key.map(ToString::to_string);
        config
    }

    #[test]
    fn cloud_provider_label_uses_product_casing() {
        assert_eq!(ConfigKind::VolcengineCloud.label(), "VolcEngine Cloud");
    }

    fn write_config(path: &Path, config: &Config) {
        fs::write(
            path,
            serde_json::to_string_pretty(config).expect("config should serialize"),
        )
        .expect("config should be written");
    }

    #[test]
    fn config_listing_ignores_active_config_and_bak_file() {
        let dir = unique_dir("list");
        fs::create_dir_all(&dir).expect("dir should exist");
        write_config(
            &dir.join("ovcli.conf"),
            &sample_config("http://active", None),
        );
        write_config(
            &dir.join("ovcli.conf.bak"),
            &sample_config("http://backup", None),
        );
        write_config(
            &dir.join("ovcli.conf.local"),
            &sample_config("http://local", None),
        );
        fs::write(dir.join("ovcli.conf.notes.txt"), "{}").expect("notes file should be written");

        let store = ConfigStore::for_config_dir(dir.clone());
        let configs = store.list_configs().expect("configs should load");

        assert_eq!(configs.len(), 1);
        assert_eq!(configs[0].name, "local");
        assert_eq!(configs[0].config.url, "http://local");
        assert!(!configs[0].is_active);
    }

    #[test]
    fn config_listing_skips_corrupted_saved_config() {
        let dir = unique_dir("list-corrupted");
        fs::create_dir_all(&dir).expect("dir should exist");
        write_config(
            &dir.join("ovcli.conf.local"),
            &sample_config("http://local", None),
        );
        fs::write(dir.join("ovcli.conf.broken"), "{not json")
            .expect("corrupted config should be written");

        let store = ConfigStore::for_config_dir(dir);
        let configs = store
            .list_configs()
            .expect("valid configs should still load");

        assert_eq!(configs.len(), 1);
        assert_eq!(configs[0].name, "local");
    }

    #[test]
    fn config_listing_marks_only_one_duplicate_as_active() {
        let dir = unique_dir("duplicate-active");
        let store = ConfigStore::for_config_dir(dir.clone());
        let config = sample_config("http://local", None);
        store
            .save_named_config("local", &config)
            .expect("local config should save");
        store
            .save_named_config("local-copy", &config)
            .expect("copy config should save");
        store
            .activate_config("local")
            .expect("local config should become active");

        let configs = store.list_configs().expect("configs should load");
        let active = configs
            .iter()
            .filter(|entry| entry.is_active)
            .map(|entry| entry.name.as_str())
            .collect::<Vec<_>>();

        assert_eq!(active, vec!["local"]);
        assert!(store.is_config_name_active("local").expect("active check"));
        assert!(
            !store
                .is_config_name_active("local-copy")
                .expect("copy active check")
        );
    }

    #[test]
    fn cloud_config_listing_marks_only_one_duplicate_as_active() {
        let dir = unique_dir("duplicate-cloud-active");
        let store = ConfigStore::for_config_dir(dir.clone());
        let config = sample_config(VOLCENGINE_CLOUD_URL, Some("cloud-key"));
        store
            .save_named_config("cloud", &config)
            .expect("cloud config should save");
        store
            .save_named_config("cloud-copy", &config)
            .expect("cloud copy config should save");
        store
            .activate_config("cloud")
            .expect("cloud config should become active");

        let configs = store.list_configs().expect("configs should load");
        let active = configs
            .iter()
            .filter(|entry| entry.is_active)
            .map(|entry| entry.name.as_str())
            .collect::<Vec<_>>();

        assert_eq!(active, vec!["cloud"]);
        assert!(store.is_config_name_active("cloud").expect("active check"));
        assert!(
            !store
                .is_config_name_active("cloud-copy")
                .expect("copy active check")
        );
    }

    #[test]
    fn config_name_validation_rejects_unsafe_names() {
        for name in ["", "   ", ".hidden", "../prod", "prod/dev", "prod\\dev"] {
            assert!(validate_config_name(name).is_err(), "{name:?} should fail");
        }

        assert!(validate_config_name("prod").is_ok());
        assert!(validate_config_name("prod-cn-beijing_1").is_ok());
    }

    #[test]
    fn volcengine_config_uses_fixed_url_and_requires_api_key() {
        let missing_key = ConfigDraft {
            name: "cloud".to_string(),
            kind: ConfigKind::VolcengineCloud,
            url: String::new(),
            api_key: None,
            root_api_key: None,
            account: None,
            user: None,
        };
        assert!(build_config(&missing_key).is_err());

        let draft = ConfigDraft {
            api_key: Some("viking-key".to_string()),
            ..missing_key
        };
        let config = build_config(&draft).expect("cloud config should build");

        assert_eq!(config.url, VOLCENGINE_CLOUD_URL);
        assert_eq!(config.api_key.as_deref(), Some("viking-key"));
    }

    #[test]
    fn self_managed_config_accepts_custom_url_and_optional_key() {
        let draft = ConfigDraft {
            name: "local".to_string(),
            kind: ConfigKind::SelfManaged,
            url: "http://127.0.0.1:1933".to_string(),
            api_key: None,
            root_api_key: None,
            account: Some("default".to_string()),
            user: Some("default".to_string()),
        };
        let without_key = build_config(&draft).expect("self-managed config should build");
        assert_eq!(without_key.url, "http://127.0.0.1:1933");
        assert!(without_key.api_key.is_none());
        assert_eq!(without_key.account.as_deref(), Some("default"));
        assert_eq!(without_key.user.as_deref(), Some("default"));

        let with_key = build_config(&ConfigDraft {
            api_key: Some("local-key".to_string()),
            root_api_key: None,
            account: None,
            user: None,
            ..draft
        })
        .expect("self-managed config with key should build");
        assert_eq!(with_key.api_key.as_deref(), Some("local-key"));
        assert!(with_key.account.is_none());
        assert!(with_key.user.is_none());
    }

    #[test]
    fn remote_self_managed_config_requires_api_key() {
        let draft = ConfigDraft {
            name: "remote".to_string(),
            kind: ConfigKind::SelfManaged,
            url: "https://openviking.example.com".to_string(),
            api_key: None,
            root_api_key: None,
            account: Some("default".to_string()),
            user: Some("default".to_string()),
        };

        let error = build_config(&draft).expect_err("remote self-managed requires key");

        assert!(error.to_string().contains("API key"));
    }

    #[test]
    fn bare_loopback_self_managed_urls_are_local_and_get_default_port() {
        for (input, expected) in [
            ("127.0.0.1", "http://127.0.0.1:1933"),
            ("localhost", "http://localhost:1933"),
            ("::1", "http://[::1]:1933"),
            ("[::1]", "http://[::1]:1933"),
            ("::1:1944", "http://[::1]:1944"),
            ("[::1]:1944", "http://[::1]:1944"),
        ] {
            let draft = ConfigDraft {
                name: "local".to_string(),
                kind: ConfigKind::SelfManaged,
                url: input.to_string(),
                api_key: None,
                root_api_key: None,
                account: Some("default".to_string()),
                user: Some("default".to_string()),
            };

            let config = build_config(&draft)
                .unwrap_or_else(|error| panic!("{input} should be treated as local: {error}"));

            assert_eq!(config.url, expected);
        }
    }

    #[tokio::test]
    async fn api_key_role_detection_distinguishes_root_from_regular_keys() {
        let url = spawn_role_probe_server("root-key", "regular-key").await;
        let root_config = sample_config(&url, Some("root-key"));
        let regular_config = sample_config(&url, Some("regular-key"));

        let root_role = validate_candidate_config_with_role(&root_config, true)
            .await
            .expect("root key should validate");
        let regular_role = validate_candidate_config_with_role(&regular_config, true)
            .await
            .expect("regular key should validate");

        assert_eq!(root_role, Some(ApiKeyRole::Root));
        assert_eq!(regular_role, Some(ApiKeyRole::Regular));
    }

    #[tokio::test]
    async fn api_key_role_detection_fails_on_ambiguous_admin_probe_errors() {
        let url = spawn_admin_probe_500_server("maybe-root").await;
        let config = sample_config(&url, Some("maybe-root"));

        let error = validate_candidate_config_with_role(&config, true)
            .await
            .expect_err("admin probe 500 should not be classified as regular");

        assert!(matches!(
            error,
            crate::error::Error::Api {
                status: Some(500),
                ..
            }
        ));
    }

    #[tokio::test]
    async fn self_managed_config_with_api_key_validates_authenticated_probe() {
        let url = spawn_auth_probe_server("good-key").await;
        let config = sample_config(&url, Some("bad-key"));

        let error = validate_candidate_config(&config, false)
            .await
            .expect_err("bad self-managed API key should fail validation");

        assert!(matches!(error, crate::error::Error::Api { .. }));
    }

    #[tokio::test]
    async fn local_empty_key_without_health_auth_mode_probes_status() {
        let url = spawn_status_auth_probe_server().await;
        let config = sample_config(&url, None);

        let error = validate_candidate_config(&config, false)
            .await
            .expect_err("auth-required local server should reject empty API key");

        assert!(matches!(error, crate::error::Error::Api { .. }));
    }

    #[test]
    fn authenticated_probe_runs_for_keys_or_auth_required_health_modes() {
        let dev_health = serde_json::json!({ "healthy": true, "auth_mode": "dev" });
        let api_key_health = serde_json::json!({ "healthy": true, "auth_mode": "api_key" });
        let trusted_health = serde_json::json!({ "healthy": true, "auth_mode": "trusted" });

        assert!(!should_run_authenticated_probe(&dev_health, false, false));
        assert!(should_run_authenticated_probe(&dev_health, false, true));
        assert!(should_run_authenticated_probe(&dev_health, true, false));
        assert!(should_run_authenticated_probe(
            &api_key_health,
            false,
            false
        ));
        assert!(should_run_authenticated_probe(
            &trusted_health,
            false,
            false
        ));
    }

    #[test]
    fn admin_probe_status_classification_is_explicit() {
        assert!(admin_probe_regular_key_status(401));
        assert!(admin_probe_regular_key_status(403));
        assert!(admin_probe_regular_key_status(404));
        assert!(admin_probe_ambiguous_status(408));
        assert!(admin_probe_ambiguous_status(429));
        assert!(admin_probe_ambiguous_status(500));
        assert!(admin_probe_ambiguous_status(503));
        assert!(!admin_probe_regular_key_status(500));
    }

    #[test]
    fn validation_error_copy_hides_raw_backend_details() {
        let error = crate::error::Error::api(
            "[AuthenticationError] invalid key. Request ID: 02177930089909800000000000000000ffff"
                .to_string(),
        );

        let cloud = validation_error_copy(ConfigKind::VolcengineCloud, &error);
        let self_managed = validation_error_copy(ConfigKind::SelfManaged, &error);

        assert_eq!(
            cloud,
            "Validation failed. Check your API key and try again."
        );
        assert_eq!(
            self_managed,
            "Validation failed. Check the server URL and API key if required."
        );
        assert!(!cloud.contains("Request ID"));
        assert!(!self_managed.contains("AuthenticationError"));
    }

    #[test]
    fn edit_active_config_updates_active_config_and_saved_config() {
        let dir = unique_dir("edit-active");
        let store = ConfigStore::for_config_dir(dir.clone());
        let original = sample_config("http://old", Some("old-key"));
        store
            .save_named_config("local", &original)
            .expect("config should save");
        store
            .activate_config("local")
            .expect("config should become active");

        let edited = sample_config("http://new", Some("new-key"));
        store
            .save_edited_config("local", "renamed", &edited)
            .expect("active edit should save");

        let saved = Config::from_file(&dir.join("ovcli.conf.renamed").to_string_lossy())
            .expect("renamed config should load");
        let active = Config::from_file(&dir.join("ovcli.conf").to_string_lossy())
            .expect("active config should load");

        assert_eq!(saved.url, "http://new");
        assert_eq!(active.url, "http://new");
        assert!(!dir.join("ovcli.conf.local").exists());
    }

    #[test]
    fn edit_duplicate_non_active_config_does_not_update_active_config() {
        let dir = unique_dir("edit-duplicate");
        let store = ConfigStore::for_config_dir(dir.clone());
        let original = sample_config("http://local", None);
        store
            .save_named_config("local", &original)
            .expect("local config should save");
        store
            .save_named_config("local-copy", &original)
            .expect("copy config should save");
        store
            .activate_config("local")
            .expect("local config should become active");

        let edited = sample_config("http://copy", None);
        store
            .save_edited_config("local-copy", "local-copy", &edited)
            .expect("copy edit should save");

        let active = Config::from_file(&dir.join("ovcli.conf").to_string_lossy())
            .expect("active config should load");
        let copy = Config::from_file(&dir.join("ovcli.conf.local-copy").to_string_lossy())
            .expect("copy config should load");

        assert_eq!(active.url, "http://local");
        assert_eq!(copy.url, "http://copy");
    }

    #[test]
    fn delete_refuses_active_config() {
        let dir = unique_dir("delete-active");
        let store = ConfigStore::for_config_dir(dir.clone());
        store
            .save_named_config("local", &sample_config("http://local", None))
            .expect("config should save");
        store
            .activate_config("local")
            .expect("config should become active");

        assert!(store.delete_config("local").is_err());
        assert!(dir.join("ovcli.conf.local").exists());
    }

    #[test]
    fn delete_duplicate_non_active_config_is_allowed() {
        let dir = unique_dir("delete-duplicate");
        let store = ConfigStore::for_config_dir(dir.clone());
        let config = sample_config("http://local", None);
        store
            .save_named_config("local", &config)
            .expect("local config should save");
        store
            .save_named_config("local-copy", &config)
            .expect("copy config should save");
        store
            .activate_config("local")
            .expect("local config should become active");

        store
            .delete_config("local-copy")
            .expect("non-active duplicate should delete");

        assert!(dir.join("ovcli.conf.local").exists());
        assert!(!dir.join("ovcli.conf.local-copy").exists());
        assert!(dir.join("ovcli.conf").exists());
    }

    async fn spawn_auth_probe_server(valid_api_key: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..2 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    let header = format!("x-api-key: {valid_api_key}");
                    if request.to_ascii_lowercase().contains(&header) {
                        http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                    } else {
                        http_response(
                            401,
                            r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                        )
                    }
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    async fn spawn_status_auth_probe_server() -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..2 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    http_response(
                        401,
                        r#"{"error":{"code":"AuthenticationError","message":"missing key"}}"#,
                    )
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    async fn spawn_role_probe_server(
        root_api_key: &'static str,
        regular_api_key: &'static str,
    ) -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..6 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let lower_request = request.to_ascii_lowercase();
                let root_header = format!("x-api-key: {root_api_key}");
                let regular_header = format!("x-api-key: {regular_api_key}");
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    if lower_request.contains(&root_header)
                        || lower_request.contains(&regular_header)
                    {
                        http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                    } else {
                        http_response(
                            401,
                            r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                        )
                    }
                } else if request.starts_with("GET /api/v1/admin/accounts ") {
                    if lower_request.contains(&root_header) {
                        http_response(200, r#"{"accounts":[]}"#)
                    } else {
                        http_response(
                            403,
                            r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                        )
                    }
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    async fn spawn_admin_probe_500_server(api_key: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..3 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let lower_request = request.to_ascii_lowercase();
                let api_header = format!("x-api-key: {api_key}");
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    if lower_request.contains(&api_header) {
                        http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                    } else {
                        http_response(
                            401,
                            r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                        )
                    }
                } else if request.starts_with("GET /api/v1/admin/accounts ") {
                    http_response(
                        500,
                        r#"{"error":{"code":"INTERNAL","message":"admin probe failed"}}"#,
                    )
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    fn http_response(status: u16, body: &str) -> String {
        let reason = match status {
            200 => "OK",
            401 => "Unauthorized",
            500 => "Internal Server Error",
            404 => "Not Found",
            _ => "OK",
        };
        format!(
            "HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        )
    }
}
