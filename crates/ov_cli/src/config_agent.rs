use std::{
    env, fs,
    io::{self, Read},
    path::PathBuf,
};

use colored::Colorize;
use serde::Serialize;
use serde_json::json;
use uuid::Uuid;

use crate::{
    CliContext, ConfigAddCustomArgs, ConfigAddOvServiceArgs, ConfigAddTarget, ConfigDeleteArgs,
    ConfigEditArgs,
    config::{Config, DEFAULT_CUSTOM_URL},
    config_wizard::{
        ApiKeyRole, ConfigKind, ConfigStore, OPENVIKING_SERVICE_URL, configs_equivalent,
        custom_allows_empty_api_key, custom_requires_api_key, normalize_custom_url,
        validate_account_id_value, validate_candidate_config_with_role, validate_config_name,
        validate_user_id_value,
    },
    error::Error,
    output::OutputFormat,
    theme,
};

const EXIT_BAD_INPUT: i32 = 2;
const EXIT_EXISTS_DIFFERENT: i32 = 3;
const EXIT_VALIDATION: i32 = 4;
const EXIT_AUTH: i32 = 5;
const EXIT_REFUSED: i32 = 6;

#[derive(Debug, Clone)]
pub(crate) struct AgentError {
    code: &'static str,
    message: String,
    exit_code: i32,
}

impl AgentError {
    fn bad_input(message: impl Into<String>) -> Self {
        Self {
            code: "bad_input",
            message: message.into(),
            exit_code: EXIT_BAD_INPUT,
        }
    }

    fn exists_different(message: impl Into<String>) -> Self {
        Self {
            code: "config_exists",
            message: message.into(),
            exit_code: EXIT_EXISTS_DIFFERENT,
        }
    }

    fn validation(message: impl Into<String>) -> Self {
        Self {
            code: "validation_failed",
            message: message.into(),
            exit_code: EXIT_VALIDATION,
        }
    }

    fn auth(message: impl Into<String>) -> Self {
        Self {
            code: "auth_mismatch",
            message: message.into(),
            exit_code: EXIT_AUTH,
        }
    }

    fn refused(message: impl Into<String>) -> Self {
        Self {
            code: "refused",
            message: message.into(),
            exit_code: EXIT_REFUSED,
        }
    }

    pub(crate) fn exit_code(&self) -> i32 {
        self.exit_code
    }
}

type AgentResult<T> = std::result::Result<T, AgentError>;

#[derive(Debug, Clone, Copy)]
enum SecretInput<'a> {
    None,
    Stdin,
    Env(&'a str),
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ValidationSummary {
    status: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct AddEditResult {
    action: &'static str,
    name: String,
    kind: &'static str,
    url: String,
    saved_path: String,
    active_path: Option<String>,
    activated: bool,
    validation: ValidationSummary,
    #[serde(skip)]
    root_key_only: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct SwitchResult {
    action: &'static str,
    name: String,
    kind: &'static str,
    url: String,
    active_path: String,
    activated: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct DeleteResult {
    action: &'static str,
    name: String,
    deleted: bool,
    already_absent: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ListEntry {
    name: String,
    kind: &'static str,
    url: String,
    active: bool,
}

#[derive(Debug)]
pub(crate) enum AgentOutput {
    AddEdit(AddEditResult),
    Switch(SwitchResult),
    Delete(DeleteResult),
    List(Vec<ListEntry>),
}

pub(crate) async fn add(target: ConfigAddTarget, _ctx: &CliContext) -> AgentResult<AgentOutput> {
    let store = ConfigStore::new().map_err(config_error)?;
    let result = match target {
        ConfigAddTarget::OvService(args) => add_ov_service(&store, args).await?,
        ConfigAddTarget::Custom(args) => add_custom(&store, args).await?,
    };
    Ok(AgentOutput::AddEdit(result))
}

pub(crate) async fn edit(args: ConfigEditArgs, _ctx: &CliContext) -> AgentResult<AgentOutput> {
    let store = ConfigStore::new().map_err(config_error)?;
    Ok(AgentOutput::AddEdit(edit_saved_config(&store, args).await?))
}

pub(crate) fn switch(name: String, _ctx: &CliContext) -> AgentResult<AgentOutput> {
    let store = ConfigStore::new().map_err(config_error)?;
    validate_config_name(&name).map_err(config_error)?;
    let config = store.load_saved_config(&name).map_err(|error| {
        AgentError::bad_input(format!("Could not load saved config '{name}': {error}"))
    })?;
    store.activate_config(&name).map_err(config_error)?;
    Ok(AgentOutput::Switch(SwitchResult {
        action: "switch",
        name,
        kind: ConfigKind::from_config(&config).compact_label(),
        url: config.url,
        active_path: path_string(store.active_path()),
        activated: true,
    }))
}

pub(crate) fn list(_ctx: &CliContext) -> AgentResult<AgentOutput> {
    let store = ConfigStore::new().map_err(config_error)?;
    let entries = store
        .list_configs()
        .map_err(config_error)?
        .into_iter()
        .map(|entry| ListEntry {
            name: entry.name,
            kind: entry.kind.compact_label(),
            url: entry.config.url,
            active: entry.is_active,
        })
        .collect();
    Ok(AgentOutput::List(entries))
}

pub(crate) fn delete(args: ConfigDeleteArgs, _ctx: &CliContext) -> AgentResult<AgentOutput> {
    let store = ConfigStore::new().map_err(config_error)?;
    delete_saved_config(&store, args)
}

fn delete_saved_config(store: &ConfigStore, args: ConfigDeleteArgs) -> AgentResult<AgentOutput> {
    validate_config_name(&args.name).map_err(config_error)?;
    let path = store.saved_config_path(&args.name).map_err(config_error)?;
    if !path.exists() {
        return Ok(AgentOutput::Delete(DeleteResult {
            action: "delete",
            name: args.name,
            deleted: false,
            already_absent: true,
        }));
    }

    match store.load_saved_config(&args.name) {
        Ok(_) => {
            if store
                .is_config_name_active(&args.name)
                .map_err(config_error)?
            {
                return Err(AgentError::refused(
                    "Cannot delete the active config. Run 'ov config switch <name>' first.",
                ));
            }
        }
        Err(error) if !args.force => {
            return Err(AgentError::bad_input(format!(
                "Saved config '{}' cannot be read: {error}. Pass --force to delete the file.",
                args.name
            )));
        }
        Err(_) => {}
    }

    fs::remove_file(&path).map_err(|error| {
        AgentError::bad_input(format!("Failed to delete config '{}': {error}", args.name))
    })?;
    Ok(AgentOutput::Delete(DeleteResult {
        action: "delete",
        name: args.name,
        deleted: true,
        already_absent: false,
    }))
}

pub(crate) fn print_success(output: AgentOutput, ctx: &CliContext) {
    match ctx.output_format {
        OutputFormat::Json => {
            let result = match output {
                AgentOutput::AddEdit(result) => json!(result),
                AgentOutput::Switch(result) => json!(result),
                AgentOutput::Delete(result) => json!(result),
                AgentOutput::List(result) => json!(result),
            };
            println!("{}", json!({ "status": "ok", "result": result }));
        }
        OutputFormat::Table => match output {
            AgentOutput::AddEdit(result) => print_add_edit_result(&result),
            AgentOutput::Switch(result) => {
                println!(
                    "{} {}",
                    theme::success("✓").bold(),
                    theme::success(format!("Switched active config to '{}'.", result.name)).bold()
                );
                println!(
                    "{} {}",
                    theme::muted("Active config:"),
                    theme::sky_value(result.active_path)
                );
            }
            AgentOutput::Delete(result) => {
                if result.already_absent {
                    println!(
                        "{} {}",
                        theme::success("✓").bold(),
                        theme::muted(format!("Config '{}' was already absent.", result.name))
                    );
                } else {
                    println!(
                        "{} {}",
                        theme::success("✓").bold(),
                        theme::success(format!("Deleted config '{}'.", result.name)).bold()
                    );
                }
            }
            AgentOutput::List(entries) => print_list_result(&entries),
        },
    }
}

pub(crate) fn print_error(error: &AgentError, ctx: &CliContext) {
    match ctx.output_format {
        OutputFormat::Json => eprintln!(
            "{}",
            json!({
                "status": "error",
                "error": {
                    "code": error.code,
                    "message": error.message,
                }
            })
        ),
        OutputFormat::Table => {
            eprintln!(
                "{} [{}] {}",
                theme::error("Error").bold(),
                theme::error(error.code),
                theme::body(&error.message)
            );
        }
    }
}

async fn add_ov_service(
    store: &ConfigStore,
    args: ConfigAddOvServiceArgs,
) -> AgentResult<AddEditResult> {
    let name = config_name_or_generate(store, args.name, ConfigKind::OpenVikingService)?;
    let api_key = read_required_secret(
        secret_input(args.api_key_stdin, args.api_key_env.as_deref()),
        "OpenViking Service API key",
    )?;
    validate_optional_identity(args.account.as_deref(), args.user.as_deref())?;

    let config = Config {
        url: OPENVIKING_SERVICE_URL.to_string(),
        api_key: Some(api_key),
        root_api_key: None,
        account: args.account,
        user: args.user,
        ..Config::default()
    };

    if let Some(result) = existing_add_preflight(
        store,
        &name,
        ConfigKind::OpenVikingService,
        &config,
        args.activate,
        args.force,
    )? {
        return Ok(result);
    }

    validate_config_for_write(&config, ConfigKind::OpenVikingService, true).await?;
    save_new_config(
        store,
        &name,
        ConfigKind::OpenVikingService,
        &config,
        args.activate,
        args.force,
        "add",
    )
}

async fn add_custom(store: &ConfigStore, args: ConfigAddCustomArgs) -> AgentResult<AddEditResult> {
    let name = config_name_or_generate(store, args.name, ConfigKind::Custom)?;
    validate_optional_identity(args.account.as_deref(), args.user.as_deref())?;
    let api_key = read_optional_secret(
        secret_input(args.api_key_stdin, args.api_key_env.as_deref()),
        "API key",
    )?;
    let root_api_key = read_optional_secret(
        secret_input(args.root_api_key_stdin, args.root_api_key_env.as_deref()),
        "root API key",
    )?;
    let url = normalize_custom_url(args.url.as_deref().unwrap_or(DEFAULT_CUSTOM_URL));

    let config = build_custom_config(url, api_key, root_api_key, args.account, args.user)?;

    if let Some(result) = existing_add_preflight(
        store,
        &name,
        ConfigKind::Custom,
        &config,
        args.activate,
        args.force,
    )? {
        return Ok(result);
    }

    validate_config_for_write(&config, ConfigKind::Custom, false).await?;

    save_new_config(
        store,
        &name,
        ConfigKind::Custom,
        &config,
        args.activate,
        args.force,
        "add",
    )
}

async fn edit_saved_config(
    store: &ConfigStore,
    args: ConfigEditArgs,
) -> AgentResult<AddEditResult> {
    validate_config_name(&args.name).map_err(config_error)?;
    let existing = store.load_saved_config(&args.name).map_err(|error| {
        AgentError::bad_input(format!(
            "Could not load saved config '{}': {error}",
            args.name
        ))
    })?;
    let old_kind = ConfigKind::from_config(&existing);
    let existing_root_as_normal = root_as_normal(&existing);
    let api_key_touched = args.api_key_stdin || args.api_key_env.is_some() || args.clear_api_key;
    let root_key_touched =
        args.root_api_key_stdin || args.root_api_key_env.is_some() || args.clear_root_api_key;
    let new_name = args.new_name.clone().unwrap_or_else(|| args.name.clone());
    validate_config_name(&new_name).map_err(config_error)?;
    validate_optional_identity(args.account.as_deref(), args.user.as_deref())?;

    if old_kind == ConfigKind::OpenVikingService && args.url.is_some() {
        return Err(AgentError::bad_input(
            "OpenViking Service configs use a fixed server URL.",
        ));
    }

    let api_secret = read_optional_secret(
        secret_input(args.api_key_stdin, args.api_key_env.as_deref()),
        "API key",
    )?;
    let root_secret = read_optional_secret(
        secret_input(args.root_api_key_stdin, args.root_api_key_env.as_deref()),
        "root API key",
    )?;

    let mut edited = existing.clone();
    if let Some(url) = args.url.as_deref() {
        edited.url = normalize_custom_url(url);
    }
    if args.clear_api_key {
        edited.api_key = None;
    }
    if let Some(api_key) = api_secret {
        edited.api_key = Some(api_key);
    }
    if args.clear_root_api_key {
        edited.root_api_key = None;
        if existing_root_as_normal && !api_key_touched {
            edited.api_key = None;
        }
    }
    if let Some(root_api_key) = root_secret {
        if !api_key_touched && (existing_root_as_normal || existing.api_key.is_none()) {
            edited.api_key = Some(root_api_key.clone());
        }
        edited.root_api_key = Some(root_api_key);
    }
    if args.account.is_some() {
        edited.account = args.account;
    }
    if args.user.is_some() {
        edited.user = args.user;
    }
    if !api_key_touched
        && !root_key_touched
        && existing_root_as_normal
        && edited.api_key.as_deref() != edited.root_api_key.as_deref()
    {
        edited.api_key = edited.root_api_key.clone();
    }

    let new_kind = ConfigKind::from_config(&edited);
    if new_kind == ConfigKind::Custom {
        finalize_custom_identity(&mut edited)?;
    }
    validate_config_for_write(&edited, new_kind, new_kind == ConfigKind::OpenVikingService).await?;

    save_edited_config(
        store,
        &args.name,
        &new_name,
        &edited,
        args.activate,
        args.force,
    )
}

fn build_custom_config(
    url: String,
    api_key: Option<String>,
    root_api_key: Option<String>,
    mut account: Option<String>,
    mut user: Option<String>,
) -> AgentResult<Config> {
    if api_key.is_none() && root_api_key.is_none() && custom_requires_api_key(&url) {
        return Err(AgentError::bad_input(
            "Remote custom servers require --api-key-stdin, --api-key-env, --root-api-key-stdin, or --root-api-key-env.",
        ));
    }

    if api_key.is_none() && root_api_key.is_none() && custom_allows_empty_api_key(&url) {
        account.get_or_insert_with(|| "default".to_string());
        user.get_or_insert_with(|| "default".to_string());
    }

    let mut config = Config {
        url: url.trim_end_matches('/').to_string(),
        api_key,
        root_api_key,
        account,
        user,
        ..Config::default()
    };

    finalize_custom_identity(&mut config)?;

    Ok(config)
}

fn finalize_custom_identity(config: &mut Config) -> AgentResult<()> {
    if config.api_key.is_none() {
        config.api_key = config.root_api_key.clone();
    }

    if root_as_normal(config) && (config.account.is_none() || config.user.is_none()) {
        return Err(AgentError::auth(
            "Root API keys require explicit --account and --user.",
        ));
    }

    Ok(())
}

async fn validate_config_for_write(
    config: &Config,
    kind: ConfigKind,
    require_api_key: bool,
) -> AgentResult<()> {
    if let Some(root_key) = config.root_api_key.as_deref() {
        let mut root_probe = config.clone();
        root_probe.api_key = Some(root_key.to_string());
        match validate_candidate_config_with_role(&root_probe, true).await {
            Ok(Some(ApiKeyRole::Root)) => {}
            Ok(Some(ApiKeyRole::Regular)) | Ok(None) => {
                return Err(AgentError::auth(
                    "The supplied root API key was not accepted as a root key.",
                ));
            }
            Err(error) => return Err(validation_error(kind, error)),
        }
    }

    let api_key_role = match validate_candidate_config_with_role(config, require_api_key).await {
        Ok(role) => role,
        Err(error) => return Err(validation_error(kind, error)),
    };

    if kind == ConfigKind::Custom
        && api_key_role == Some(ApiKeyRole::Root)
        && !root_as_normal(config)
    {
        return Err(AgentError::auth(
            "The supplied API key is a root key. Use --root-api-key-stdin/--root-api-key-env with --account and --user.",
        ));
    }

    Ok(())
}

fn root_as_normal(config: &Config) -> bool {
    config
        .root_api_key
        .as_ref()
        .is_some_and(|root_key| config.api_key.as_deref() == Some(root_key.as_str()))
}

fn save_new_config(
    store: &ConfigStore,
    name: &str,
    kind: ConfigKind,
    config: &Config,
    activate: bool,
    force: bool,
    action: &'static str,
) -> AgentResult<AddEditResult> {
    let saved_path = store.saved_config_path(name).map_err(config_error)?;
    if saved_path.exists() {
        let existing = store.load_saved_config(name).map_err(config_error)?;
        if configs_equivalent(&existing, config).map_err(config_error)? {
            if activate {
                store.activate_config(name).map_err(config_error)?;
            }
            return Ok(add_edit_result(store, action, name, kind, config, activate));
        }
        if !force {
            return Err(AgentError::exists_different(format!(
                "Config '{name}' already exists with different values. Pass --force to replace it."
            )));
        }
        if store.is_config_name_active(name).map_err(config_error)? && !activate {
            return Err(AgentError::refused(
                "Replacing the active saved config requires --activate.",
            ));
        }
    }

    if activate {
        // Saved and active config files are separate writes: validate first,
        // write the saved config, then write active ovcli.conf.
        store
            .save_and_activate(name, config)
            .map_err(config_error)?;
    } else {
        store
            .save_named_config(name, config)
            .map_err(config_error)?;
    }
    Ok(add_edit_result(store, action, name, kind, config, activate))
}

fn existing_add_preflight(
    store: &ConfigStore,
    name: &str,
    kind: ConfigKind,
    config: &Config,
    activate: bool,
    force: bool,
) -> AgentResult<Option<AddEditResult>> {
    let saved_path = store.saved_config_path(name).map_err(config_error)?;
    if !saved_path.exists() {
        return Ok(None);
    }

    let existing = store.load_saved_config(name).map_err(config_error)?;
    if configs_equivalent(&existing, config).map_err(config_error)? {
        if activate {
            store.activate_config(name).map_err(config_error)?;
        }
        return Ok(Some(add_edit_result(
            store, "add", name, kind, config, activate,
        )));
    }
    if !force {
        return Err(AgentError::exists_different(format!(
            "Config '{name}' already exists with different values. Pass --force to replace it."
        )));
    }
    if store.is_config_name_active(name).map_err(config_error)? && !activate {
        return Err(AgentError::refused(
            "Replacing the active saved config requires --activate.",
        ));
    }

    Ok(None)
}

fn save_edited_config(
    store: &ConfigStore,
    old_name: &str,
    new_name: &str,
    config: &Config,
    activate: bool,
    force: bool,
) -> AgentResult<AddEditResult> {
    let old_config = store.load_saved_config(old_name).map_err(config_error)?;
    let renamed = old_name != new_name;
    let new_path = store.saved_config_path(new_name).map_err(config_error)?;
    if renamed && new_path.exists() {
        let existing_new = store.load_saved_config(new_name).map_err(config_error)?;
        if !configs_equivalent(&existing_new, config).map_err(config_error)? && !force {
            return Err(AgentError::exists_different(format!(
                "Config '{new_name}' already exists with different values. Pass --force to replace it."
            )));
        }
        if store
            .is_config_name_active(new_name)
            .map_err(config_error)?
        {
            return Err(AgentError::refused(
                "Cannot overwrite another active saved config.",
            ));
        }
        if force || configs_equivalent(&existing_new, config).map_err(config_error)? {
            fs::remove_file(&new_path).map_err(|error| {
                AgentError::bad_input(format!(
                    "Failed to replace existing config '{new_name}': {error}"
                ))
            })?;
        }
    }

    let content_changed = !configs_equivalent(&old_config, config).map_err(config_error)?;
    if renamed || content_changed {
        store
            .save_edited_config(old_name, new_name, config)
            .map_err(config_error)?;
    }
    if activate {
        // This intentionally activates after the saved file update; there is no
        // cross-file lock between ovcli.conf.<name> and ovcli.conf.
        store.activate_config(new_name).map_err(config_error)?;
    }

    Ok(add_edit_result(
        store,
        "edit",
        new_name,
        ConfigKind::from_config(config),
        config,
        activate,
    ))
}

fn add_edit_result(
    store: &ConfigStore,
    action: &'static str,
    name: &str,
    kind: ConfigKind,
    config: &Config,
    activated: bool,
) -> AddEditResult {
    AddEditResult {
        action,
        name: name.to_string(),
        kind: kind.compact_label(),
        url: config.url.clone(),
        saved_path: store
            .saved_config_path(name)
            .map(path_string)
            .unwrap_or_else(|_| format!("ovcli.conf.{name}")),
        active_path: activated.then(|| path_string(store.active_path())),
        activated,
        validation: ValidationSummary { status: "passed" },
        root_key_only: config.api_key.is_none() && config.root_api_key.is_some(),
    }
}

fn print_add_edit_result(result: &AddEditResult) {
    let verb = if result.action == "add" {
        "Saved"
    } else {
        "Updated"
    };
    let active_copy = if result.activated {
        " and made it active"
    } else {
        ""
    };
    println!(
        "{} {}",
        theme::success("✓").bold(),
        theme::success(format!("{verb} config '{}'{}.", result.name, active_copy)).bold()
    );
    println!(
        "{} {}",
        theme::muted("Saved to:"),
        theme::sky_value(&result.saved_path)
    );
    if let Some(active_path) = &result.active_path {
        println!(
            "{} {}",
            theme::muted("Active config:"),
            theme::sky_value(active_path)
        );
    } else if no_active_config() {
        println!(
            "{} {}",
            theme::warning("Note:"),
            theme::muted(
                "No active config is set. Run 'ov config switch <name>' or add with --activate."
            )
        );
    }
    if result.root_key_only {
        eprintln!(
            "{} {}",
            theme::warning("Note:"),
            theme::muted(
                "This config has only a root API key. Normal commands require --sudo or a regular API key."
            )
        );
    }
}

fn print_list_result(entries: &[ListEntry]) {
    if entries.is_empty() {
        println!("{}", theme::muted("No saved configs found."));
        return;
    }
    println!(
        "{:<24} {:<18} {:<42} {}",
        theme::heading("Name").bold(),
        theme::heading("Type").bold(),
        theme::heading("URL").bold(),
        theme::heading("Active").bold()
    );
    for entry in entries {
        let active = if entry.active {
            theme::error("[Active]").bold().to_string()
        } else {
            String::new()
        };
        println!(
            "{:<24} {:<18} {:<42} {}",
            theme::command(&entry.name).bold(),
            theme::muted(entry.kind),
            theme::sky_value(&entry.url),
            active
        );
    }
}

fn read_required_secret(input: SecretInput<'_>, label: &str) -> AgentResult<String> {
    read_optional_secret(input, label)?.ok_or_else(|| {
        AgentError::bad_input(format!(
            "Missing {label}. Use --api-key-stdin or --api-key-env <ENV>."
        ))
    })
}

fn read_optional_secret(input: SecretInput<'_>, label: &str) -> AgentResult<Option<String>> {
    let value = match input {
        SecretInput::None => return Ok(None),
        SecretInput::Stdin => {
            let mut value = String::new();
            io::stdin().read_to_string(&mut value).map_err(|error| {
                AgentError::bad_input(format!("Failed to read {label} from stdin: {error}"))
            })?;
            value
        }
        SecretInput::Env(name) => env::var(name).map_err(|_| {
            AgentError::bad_input(format!(
                "Environment variable '{name}' for {label} is unset."
            ))
        })?,
    };
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(AgentError::bad_input(format!("{label} cannot be empty.")));
    }
    Ok(Some(trimmed.to_string()))
}

fn secret_input<'a>(stdin: bool, env_name: Option<&'a str>) -> SecretInput<'a> {
    if stdin {
        SecretInput::Stdin
    } else if let Some(env_name) = env_name {
        SecretInput::Env(env_name)
    } else {
        SecretInput::None
    }
}

fn config_name_or_generate(
    store: &ConfigStore,
    name: Option<String>,
    kind: ConfigKind,
) -> AgentResult<String> {
    if let Some(name) = name {
        validate_config_name(&name).map_err(config_error)?;
        return Ok(name);
    }

    let prefix = match kind {
        ConfigKind::OpenVikingService => "ov-service",
        ConfigKind::Custom => "custom",
    };
    for _ in 0..32 {
        let suffix = Uuid::new_v4().simple().to_string();
        let candidate = format!("{prefix}-{}", &suffix[..6]);
        if !store
            .saved_config_path(&candidate)
            .map_err(config_error)?
            .exists()
        {
            return Ok(candidate);
        }
    }
    Err(AgentError::bad_input(
        "Could not generate a unique config name. Pass --name explicitly.",
    ))
}

fn validate_optional_identity(account: Option<&str>, user: Option<&str>) -> AgentResult<()> {
    if let Some(account) = account {
        validate_account_id_value(account).map_err(config_error)?;
    }
    if let Some(user) = user {
        validate_user_id_value(user).map_err(config_error)?;
    }
    Ok(())
}

fn validation_error(kind: ConfigKind, error: Error) -> AgentError {
    match error {
        Error::Api { message, .. } => AgentError::auth(format!(
            "{} {message}",
            match kind {
                ConfigKind::OpenVikingService => "Check the API key.",
                ConfigKind::Custom => "Check the API key, account, and user.",
            }
        )),
        Error::Network(message) => AgentError::validation(format!(
            "{} {message}",
            match kind {
                ConfigKind::OpenVikingService => "Could not reach OpenViking Service.",
                ConfigKind::Custom => "Could not reach the custom server.",
            }
        )),
        Error::Config(message) => AgentError::bad_input(message),
        other => AgentError::validation(format!("Validation failed: {other}")),
    }
}

fn config_error(error: Error) -> AgentError {
    AgentError::bad_input(error.to_string())
}

fn no_active_config() -> bool {
    ConfigStore::new()
        .ok()
        .and_then(|store| store.load_active().ok())
        .flatten()
        .is_none()
}

fn path_string(path: impl Into<PathBuf>) -> String {
    path.into().display().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config_wizard::ConfigStore;
    use std::time::{SystemTime, UNIX_EPOCH};
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
    };

    fn unique_dir(name: &str) -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should be valid")
            .as_nanos();
        std::env::temp_dir().join(format!("openviking-agent-config-{name}-{suffix}"))
    }

    fn sample_config(url: &str, api_key: Option<&str>) -> Config {
        let mut config = Config::default();
        config.url = url.to_string();
        config.api_key = api_key.map(ToString::to_string);
        config
    }

    fn edit_args(name: &str) -> ConfigEditArgs {
        ConfigEditArgs {
            name: name.to_string(),
            new_name: None,
            url: None,
            api_key_stdin: false,
            api_key_env: None,
            clear_api_key: false,
            root_api_key_stdin: false,
            root_api_key_env: None,
            clear_root_api_key: false,
            account: None,
            user: None,
            activate: false,
            force: false,
        }
    }

    fn http_response(status: u16, body: &str) -> String {
        let reason = match status {
            200 => "OK",
            401 => "Unauthorized",
            403 => "Forbidden",
            404 => "Not Found",
            500 => "Internal Server Error",
            _ => "OK",
        };
        format!(
            "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )
    }

    async fn spawn_root_validation_server(
        root_api_key: &'static str,
        account: &'static str,
        user: &'static str,
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
                let account_header = format!("x-openviking-account: {account}");
                let user_header = format!("x-openviking-user: {user}");
                let has_root_identity = lower_request.contains(&root_header)
                    && lower_request.contains(&account_header)
                    && lower_request.contains(&user_header);
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") && has_root_identity {
                    http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                } else if request.starts_with("GET /api/v1/admin/accounts ") && has_root_identity {
                    http_response(200, r#"{"accounts":[]}"#)
                } else {
                    http_response(
                        401,
                        r#"{"error":{"code":"AuthenticationError","message":"invalid auth"}}"#,
                    )
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    #[test]
    fn generated_agent_config_name_has_expected_prefix() {
        let dir = unique_dir("name");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);

        let name = config_name_or_generate(&store, None, ConfigKind::OpenVikingService)
            .expect("name should generate");
        assert!(name.starts_with("ov-service-"));
        validate_config_name(&name).expect("generated name should be valid");

        let name =
            config_name_or_generate(&store, None, ConfigKind::Custom).expect("name should generate");
        assert!(name.starts_with("custom-"));
        validate_config_name(&name).expect("generated name should be valid");
    }

    #[test]
    fn agent_json_kind_uses_compact_provider_label() {
        let dir = unique_dir("json-kind");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        let config = sample_config(OPENVIKING_SERVICE_URL, Some("key"));

        let result = add_edit_result(
            &store,
            "add",
            "serverless",
            ConfigKind::OpenVikingService,
            &config,
            false,
        );

        assert_eq!(result.kind, "OpenViking Service");
    }

    #[test]
    fn identical_existing_add_succeeds_without_force() {
        let dir = unique_dir("identical");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        let config = sample_config("http://127.0.0.1:1933", Some("key"));
        store
            .save_named_config("local", &config)
            .expect("config should be saved");

        let result = save_new_config(
            &store,
            "local",
            ConfigKind::Custom,
            &config,
            false,
            false,
            "add",
        )
        .expect("identical config should be idempotent");

        assert_eq!(result.name, "local");
        assert!(!result.activated);
    }

    #[test]
    fn different_existing_add_requires_force() {
        let dir = unique_dir("different");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        store
            .save_named_config(
                "local",
                &sample_config("http://127.0.0.1:1933", Some("old")),
            )
            .expect("config should be saved");

        let error = save_new_config(
            &store,
            "local",
            ConfigKind::Custom,
            &sample_config("http://127.0.0.1:1933", Some("new")),
            false,
            false,
            "add",
        )
        .expect_err("different config should require --force");

        assert_eq!(error.exit_code(), EXIT_EXISTS_DIFFERENT);
    }

    #[test]
    fn custom_root_only_config_populates_normal_and_root_keys() {
        let config = build_custom_config(
            "http://127.0.0.1:1933".to_string(),
            None,
            Some("root-key".to_string()),
            Some("acme".to_string()),
            Some("alice".to_string()),
        )
        .expect("root-only custom config should build");

        assert_eq!(config.api_key.as_deref(), Some("root-key"));
        assert_eq!(config.root_api_key.as_deref(), Some("root-key"));
        assert_eq!(config.account.as_deref(), Some("acme"));
        assert_eq!(config.user.as_deref(), Some("alice"));
    }

    #[test]
    fn custom_user_and_root_keys_stay_separate() {
        let config = build_custom_config(
            "https://ov.example.com".to_string(),
            Some("user-key".to_string()),
            Some("root-key".to_string()),
            Some("acme".to_string()),
            Some("alice".to_string()),
        )
        .expect("custom config with both keys should build");

        assert_eq!(config.api_key.as_deref(), Some("user-key"));
        assert_eq!(config.root_api_key.as_deref(), Some("root-key"));
    }

    #[test]
    fn custom_root_key_requires_explicit_identity() {
        let error = build_custom_config(
            "http://127.0.0.1:1933".to_string(),
            None,
            Some("root-key".to_string()),
            None,
            Some("alice".to_string()),
        )
        .expect_err("root key without account should fail");

        assert_eq!(error.exit_code(), EXIT_AUTH);
    }

    #[test]
    fn custom_finalization_promotes_root_only_to_root_as_normal() {
        let mut config = sample_config("http://127.0.0.1:1933", None);
        config.root_api_key = Some("root-key".to_string());
        config.account = Some("acme".to_string());
        config.user = Some("alice".to_string());

        finalize_custom_identity(&mut config).expect("root-only config should finalize");

        assert_eq!(config.api_key.as_deref(), Some("root-key"));
        assert_eq!(config.root_api_key.as_deref(), Some("root-key"));
    }

    #[test]
    fn custom_finalization_rejects_root_as_normal_without_identity() {
        let mut config = sample_config("http://127.0.0.1:1933", None);
        config.root_api_key = Some("root-key".to_string());
        config.user = Some("alice".to_string());

        let error = finalize_custom_identity(&mut config)
            .expect_err("root-as-normal requires explicit account and user");

        assert_eq!(error.exit_code(), EXIT_AUTH);
    }

    #[test]
    fn add_and_edit_finalization_converge_on_root_as_normal_shape() {
        let add_config = build_custom_config(
            "http://127.0.0.1:1933".to_string(),
            None,
            Some("root-key".to_string()),
            Some("acme".to_string()),
            Some("alice".to_string()),
        )
        .expect("add path should build root-as-normal config");

        let mut edit_config = sample_config("http://127.0.0.1:1933", None);
        edit_config.root_api_key = Some("root-key".to_string());
        edit_config.account = Some("acme".to_string());
        edit_config.user = Some("alice".to_string());
        finalize_custom_identity(&mut edit_config)
            .expect("edit path should finalize to root-as-normal config");

        assert_eq!(edit_config.api_key, add_config.api_key);
        assert_eq!(edit_config.root_api_key, add_config.root_api_key);
        assert_eq!(edit_config.account, add_config.account);
        assert_eq!(edit_config.user, add_config.user);
    }

    #[tokio::test]
    async fn edit_clear_api_key_with_root_identity_becomes_root_as_normal() {
        let dir = unique_dir("edit-clear-api-root-normal");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        let url = spawn_root_validation_server("root-key", "acme", "alice").await;
        let mut config = sample_config(&url, Some("user-key"));
        config.root_api_key = Some("root-key".to_string());
        config.account = Some("acme".to_string());
        config.user = Some("alice".to_string());
        store
            .save_named_config("local", &config)
            .expect("config should be saved");

        let mut args = edit_args("local");
        args.clear_api_key = true;
        edit_saved_config(&store, args)
            .await
            .expect("clear API key should promote root key for normal commands");

        let saved = store
            .load_saved_config("local")
            .expect("saved config should load");
        assert_eq!(saved.api_key.as_deref(), Some("root-key"));
        assert_eq!(saved.root_api_key.as_deref(), Some("root-key"));
        assert_eq!(saved.account.as_deref(), Some("acme"));
        assert_eq!(saved.user.as_deref(), Some("alice"));
    }

    #[tokio::test]
    async fn edit_clear_api_key_with_root_without_identity_is_refused() {
        let dir = unique_dir("edit-clear-api-root-no-identity");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        let mut config = sample_config("http://127.0.0.1:1933", Some("user-key"));
        config.root_api_key = Some("root-key".to_string());
        store
            .save_named_config("local", &config)
            .expect("config should be saved");

        let mut args = edit_args("local");
        args.clear_api_key = true;
        let error = edit_saved_config(&store, args)
            .await
            .expect_err("root-as-normal edit without identity should fail");

        assert_eq!(error.exit_code(), EXIT_AUTH);
        assert!(error.message.contains("--account and --user"));
    }

    #[test]
    fn replacing_active_saved_config_requires_activate() {
        let dir = unique_dir("active-replace");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        let old_config = sample_config("http://127.0.0.1:1933", Some("old"));
        store
            .save_and_activate("local", &old_config)
            .expect("config should be active");

        let error = save_new_config(
            &store,
            "local",
            ConfigKind::Custom,
            &sample_config("http://127.0.0.1:1933", Some("new")),
            false,
            true,
            "add",
        )
        .expect_err("replacing active saved config without --activate is refused");

        assert_eq!(error.exit_code(), EXIT_REFUSED);
    }

    #[test]
    fn deleting_missing_config_is_successful_noop() {
        let dir = unique_dir("delete-missing");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);

        let output = delete_saved_config(
            &store,
            ConfigDeleteArgs {
                name: "missing".to_string(),
                force: false,
            },
        )
        .expect("missing delete should be idempotent");

        let AgentOutput::Delete(result) = output else {
            panic!("expected delete result");
        };
        assert!(!result.deleted);
        assert!(result.already_absent);
    }

    #[test]
    fn deleting_active_config_is_refused() {
        let dir = unique_dir("delete-active");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);
        store
            .save_and_activate(
                "local",
                &sample_config("http://127.0.0.1:1933", Some("key")),
            )
            .expect("config should be active");

        let error = delete_saved_config(
            &store,
            ConfigDeleteArgs {
                name: "local".to_string(),
                force: true,
            },
        )
        .expect_err("active delete should be refused");

        assert_eq!(error.exit_code(), EXIT_REFUSED);
    }
}
