use crate::CliContext;
use crate::PrivacyCommands;
use crate::client;
use crate::commands;
use crate::config::merge_csv_options;
use crate::config_agent;
use crate::error::{Error, Result};
use crate::terminal_ui::{
    RenderedRegion as RenderedSelectRegion, clear_rendered_lines, live_select_block,
};
use crate::theme;
use crate::tui;
use colored::Colorize;
use serde_json::{Map, Value};

pub async fn handle_add_resource(
    mut path: String,
    to: Option<String>,
    parent: Option<String>,
    parent_auto_create: Option<String>,
    reason: String,
    instruction: String,
    wait: bool,
    timeout: Option<f64>,
    strict_mode: bool,
    ignore_dirs: Option<String>,
    include: Option<String>,
    exclude: Option<String>,
    no_directly_upload_media: bool,
    watch_interval: f64,
    resource_args: Option<String>,
    ctx: CliContext,
) -> Result<()> {
    let is_url =
        path.starts_with("http://") || path.starts_with("https://") || path.starts_with("git@");

    if !is_url {
        use std::path::Path;

        // Unescape path: replace backslash followed by space with just space
        let unescaped_path = path.replace("\\ ", " ");
        let path_obj = Path::new(&unescaped_path);
        if !path_obj.exists() {
            return Err(Error::Client(format!(
                "Local path does not exist: {path}. If the path contains spaces, wrap it in quotes."
            )));
        }
        path = unescaped_path;
    }

    // Check that only one of --to, --parent, or --parent-auto-create is set
    let mut exclusive_count = 0;
    if to.is_some() {
        exclusive_count += 1;
    }
    if parent.is_some() {
        exclusive_count += 1;
    }
    if parent_auto_create.is_some() {
        exclusive_count += 1;
    }

    if exclusive_count > 1 {
        return Err(Error::Client(
            "Specify only one of --to, --parent, or --parent-auto-create.".to_string(),
        ));
    }

    let strict = strict_mode;
    let directly_upload_media = !no_directly_upload_media;

    let effective_ignore_dirs =
        merge_csv_options(ctx.config.upload.ignore_dirs.clone(), ignore_dirs);
    let effective_include = merge_csv_options(ctx.config.upload.include.clone(), include);
    let effective_exclude = merge_csv_options(ctx.config.upload.exclude.clone(), exclude);
    let add_resource_args = parse_add_resource_args(resource_args.as_deref())?;

    let effective_timeout = if wait {
        timeout.unwrap_or(60.0).max(ctx.config.timeout)
    } else {
        ctx.config.timeout
    };
    let auth = ctx.config.effective_auth(ctx.sudo);
    let client = client::HttpClient::new(
        &ctx.config.url,
        auth.api_key,
        auth.account,
        auth.user,
        ctx.config.effective_actor_peer_id(),
        effective_timeout,
        ctx.profile.unwrap_or(ctx.config.profile),
        ctx.config.extra_headers.clone(),
    );
    commands::resources::add_resource(
        &client,
        &path,
        to,
        parent,
        parent_auto_create,
        reason,
        instruction,
        wait,
        timeout,
        strict,
        effective_ignore_dirs,
        effective_include,
        effective_exclude,
        directly_upload_media,
        watch_interval,
        add_resource_args,
        ctx.output_format,
        ctx.compact,
        ctx.should_show_progress(),
        ctx.is_verbose(),
    )
    .await
}

fn parse_add_resource_args(raw: Option<&str>) -> Result<Option<Map<String, Value>>> {
    let Some(raw) = raw.map(str::trim).filter(|raw| !raw.is_empty()) else {
        return Ok(None);
    };

    if raw.starts_with('{') {
        let value: Value = serde_json::from_str(raw)
            .map_err(|e| Error::Client(format!("Invalid --args JSON object: {e}")))?;
        return match value {
            Value::Object(map) => Ok(Some(map)),
            _ => Err(Error::Client(
                "--args JSON form must be an object, e.g. '{\"feishu_access_token\":\"u-...\"}'"
                    .to_string(),
            )),
        };
    }

    let mut args = Map::new();
    for item in split_add_resource_args(raw)? {
        let Some((key, value)) = item.split_once(':') else {
            return Err(Error::Client(format!(
                "Invalid --args item '{item}'. Expected key:value."
            )));
        };
        let key = key.trim();
        if key.is_empty() {
            return Err(Error::Client(
                "Invalid --args item with empty key.".to_string(),
            ));
        }
        args.insert(key.to_string(), parse_add_resource_arg_value(value.trim()));
    }
    Ok(Some(args))
}

fn split_add_resource_args(raw: &str) -> Result<Vec<String>> {
    let mut items = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let mut escape = false;
    let mut depth = 0_i32;

    for ch in raw.chars() {
        if escape {
            current.push(ch);
            escape = false;
            continue;
        }
        if ch == '\\' {
            current.push(ch);
            escape = true;
            continue;
        }
        if let Some(q) = quote {
            current.push(ch);
            if ch == q {
                quote = None;
            }
            continue;
        }
        match ch {
            '"' | '\'' => {
                quote = Some(ch);
                current.push(ch);
            }
            '{' | '[' => {
                depth += 1;
                current.push(ch);
            }
            '}' | ']' => {
                depth -= 1;
                if depth < 0 {
                    return Err(Error::Client("Invalid --args nesting.".to_string()));
                }
                current.push(ch);
            }
            ',' if depth == 0 => {
                let item = current.trim();
                if !item.is_empty() {
                    items.push(item.to_string());
                }
                current.clear();
            }
            _ => current.push(ch),
        }
    }

    if quote.is_some() || depth != 0 {
        return Err(Error::Client(
            "Invalid --args quoting or nesting.".to_string(),
        ));
    }
    let item = current.trim();
    if !item.is_empty() {
        items.push(item.to_string());
    }
    Ok(items)
}

fn parse_add_resource_arg_value(raw: &str) -> Value {
    if raw.is_empty() {
        return Value::String(String::new());
    }
    if let Ok(value) = serde_json::from_str::<Value>(raw) {
        return value;
    }
    let unquoted = raw
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .or_else(|| {
            raw.strip_prefix('\'')
                .and_then(|value| value.strip_suffix('\''))
        })
        .unwrap_or(raw);
    Value::String(unquoted.to_string())
}

pub async fn handle_add_skill(
    data: String,
    wait: bool,
    timeout: Option<f64>,
    parent: Option<String>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::resources::add_skill(
        &client,
        &data,
        wait,
        timeout,
        parent.as_deref(),
        ctx.should_show_progress(),
        ctx.is_verbose(),
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_relations(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::list_relations(&client, &uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_link(
    from_uri: String,
    to_uris: Vec<String>,
    reason: String,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::link(
        &client,
        &from_uri,
        &to_uris,
        &reason,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_unlink(from_uri: String, to_uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::unlink(&client, &from_uri, &to_uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_export(
    uri: String,
    to: String,
    include_vectors: bool,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::export(
        &client,
        &uri,
        &to,
        include_vectors,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_backup(to: String, include_vectors: bool, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::backup(
        &client,
        &to,
        include_vectors,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_import(
    file_path: String,
    target_uri: String,
    on_conflict: Option<String>,
    vector_mode: Option<String>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::import(
        &client,
        &file_path,
        &target_uri,
        on_conflict.as_deref(),
        vector_mode.as_deref(),
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_restore(
    file_path: String,
    on_conflict: Option<String>,
    vector_mode: Option<String>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::restore(
        &client,
        &file_path,
        on_conflict.as_deref(),
        vector_mode.as_deref(),
        ctx.output_format,
        ctx.compact,
    )
    .await
}

use crate::{SystemBackendCommands, SystemCommands};

pub async fn handle_system(cmd: SystemCommands, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    match cmd {
        SystemCommands::Wait { timeout } => {
            commands::system::wait(&client, timeout, ctx.output_format, ctx.compact).await
        }
        SystemCommands::Status => {
            commands::system::status(&client, ctx.output_format, ctx.compact).await
        }
        SystemCommands::Health => {
            let _ = commands::system::health(
                &client,
                Some(&ctx.config),
                ctx.output_format,
                ctx.compact,
            )
            .await?;
            Ok(())
        }
        SystemCommands::Consistency { uri } => {
            commands::system::consistency(&client, &uri, ctx.output_format, ctx.compact).await
        }
        SystemCommands::Crypto { action } => commands::crypto::handle_crypto(action).await,
        SystemCommands::Backend { action } => match action {
            SystemBackendCommands::SyncStatus { uri } => {
                commands::system::backend_sync_status(&client, &uri, ctx.output_format, ctx.compact)
                    .await
            }
            SystemBackendCommands::SyncRetry { uri } => {
                commands::system::backend_sync_retry(&client, &uri, ctx.output_format, ctx.compact)
                    .await
            }
        },
    }
}

use crate::ObserverCommands;

pub async fn handle_observer(cmd: ObserverCommands, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    match cmd {
        ObserverCommands::Queue => {
            commands::observer::queue(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::Vikingdb => {
            commands::observer::vikingdb(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::Models => {
            commands::observer::models(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::Retrieval => {
            commands::observer::retrieval(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::Filesystem => {
            commands::observer::filesystem(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::System => {
            commands::observer::system(&client, ctx.output_format, ctx.compact).await
        }
    }
}

use crate::SessionCommands;

pub async fn handle_session(cmd: SessionCommands, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    match cmd {
        SessionCommands::New => {
            commands::session::new_session(&client, ctx.output_format, ctx.compact).await
        }
        SessionCommands::List => {
            commands::session::list_sessions(&client, ctx.output_format, ctx.compact).await
        }
        SessionCommands::Get { session_id } => {
            commands::session::get_session(&client, &session_id, ctx.output_format, ctx.compact)
                .await
        }
        SessionCommands::GetSessionContext {
            session_id,
            token_budget,
        } => {
            commands::session::get_session_context(
                &client,
                &session_id,
                token_budget,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        SessionCommands::GetSessionArchive {
            session_id,
            archive_id,
        } => {
            commands::session::get_session_archive(
                &client,
                &session_id,
                &archive_id,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        SessionCommands::Delete { session_id } => {
            commands::session::delete_session(&client, &session_id, ctx.output_format, ctx.compact)
                .await
        }
        SessionCommands::AddMessage {
            session_id,
            role,
            content,
            peer_id,
        } => {
            commands::session::add_message(
                &client,
                &session_id,
                &role,
                &content,
                peer_id.as_deref(),
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        SessionCommands::AddMessages {
            session_id,
            messages,
        } => {
            commands::session::add_messages(
                &client,
                &session_id,
                &messages,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        SessionCommands::Commit { session_id } => {
            commands::session::commit_session(&client, &session_id, ctx.output_format, ctx.compact)
                .await
        }
    }
}

use crate::AdminCommands;

pub async fn handle_admin(cmd: AdminCommands, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    match cmd {
        AdminCommands::CreateAccount {
            account_id,
            admin_user_id,
            seed,
            user_config_json,
        } => {
            commands::admin::create_account(
                &client,
                &account_id,
                &admin_user_id,
                seed.as_deref(),
                user_config_json.as_deref(),
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        AdminCommands::ListAccounts => {
            commands::admin::list_accounts(&client, ctx.output_format, ctx.compact).await
        }
        AdminCommands::DeleteAccount { account_id } => {
            commands::admin::delete_account(&client, &account_id, ctx.output_format, ctx.compact)
                .await
        }
        AdminCommands::Migrate { cleanup } => {
            commands::admin::migrate(&client, cleanup, ctx.output_format, ctx.compact).await
        }
        AdminCommands::RegisterUser {
            account_id,
            user_id,
            role,
            seed,
            user_config_json,
        } => {
            commands::admin::register_user(
                &client,
                &account_id,
                &user_id,
                &role,
                seed.as_deref(),
                user_config_json.as_deref(),
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        AdminCommands::ListUsers {
            account_id,
            limit,
            name,
            role,
        } => {
            commands::admin::list_users(
                &client,
                &account_id,
                limit,
                name,
                role,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        AdminCommands::RemoveUser {
            account_id,
            user_id,
        } => {
            commands::admin::remove_user(
                &client,
                &account_id,
                &user_id,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        AdminCommands::SetRole {
            account_id,
            user_id,
            role,
        } => {
            commands::admin::set_role(
                &client,
                &account_id,
                &user_id,
                &role,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        AdminCommands::RegenerateKey {
            account_id,
            user_id,
            seed,
        } => {
            commands::admin::regenerate_key(
                &client,
                &account_id,
                &user_id,
                seed.as_deref(),
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
    }
}

pub async fn handle_add_memory(content: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::session::add_memory(&client, &content, ctx.output_format, ctx.compact).await
}

pub async fn handle_privacy(cmd: PrivacyCommands, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    match cmd {
        PrivacyCommands::Categories => {
            commands::privacy::categories(&client, ctx.output_format, ctx.compact).await
        }
        PrivacyCommands::List { category } => {
            commands::privacy::list_targets(&client, &category, ctx.output_format, ctx.compact)
                .await
        }
        PrivacyCommands::Get {
            category,
            target_key,
        } => {
            commands::privacy::get_current(
                &client,
                &category,
                &target_key,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        PrivacyCommands::Upsert {
            category,
            target_key,
            values_json,
            values_file,
            key,
            change_reason,
            labels_json,
        } => {
            commands::privacy::upsert(
                &client,
                &category,
                &target_key,
                values_json.as_deref(),
                values_file.as_deref(),
                &key,
                &change_reason,
                labels_json.as_deref(),
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        PrivacyCommands::Versions {
            category,
            target_key,
        } => {
            commands::privacy::list_versions(
                &client,
                &category,
                &target_key,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        PrivacyCommands::Version {
            category,
            target_key,
            version,
        } => {
            commands::privacy::get_version(
                &client,
                &category,
                &target_key,
                version,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        PrivacyCommands::Activate {
            category,
            target_key,
            version,
        } => {
            commands::privacy::activate(
                &client,
                &category,
                &target_key,
                version,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
    }
}

use crate::ConfigCommands;
use crate::config::Config;
use crate::config_command_ui::{self, SwitchConfigRow};
use crate::config_wizard::{self, ConfigStore};
use crate::i18n::{self, Language};
use crate::output;

// Config commands intentionally edit the persisted ovcli.conf files. Runtime
// overrides carried in CliContext should not change what gets shown or saved.
pub async fn handle_config(cmd: Option<ConfigCommands>, ctx: CliContext) -> Result<()> {
    match cmd {
        Some(ConfigCommands::Show) => {
            let config = Config::load()?;
            output::output_success(
                &config_wizard::redacted_config_value(&config)?,
                output::OutputFormat::Json,
                true,
            );
            Ok(())
        }
        Some(ConfigCommands::Validate) => {
            let config = Config::load()?;
            let store = ConfigStore::new()?;
            let active_name = active_config_name(&store)?;
            match config_wizard::validate_config(&config).await {
                Ok(()) => {
                    print!(
                        "{}",
                        config_command_ui::render_validate_success(&config, active_name.as_deref(),)
                    );
                    Ok(())
                }
                Err(error) => {
                    print!(
                        "{}",
                        config_command_ui::render_validate_failure(
                            &config,
                            active_name.as_deref(),
                            &error,
                        )
                    );
                    Err(Error::AlreadyReported)
                }
            }
        }
        Some(ConfigCommands::Switch { name: None }) => handle_config_switch().await,
        Some(ConfigCommands::Switch { name: Some(name) }) => {
            handle_config_agent_result(config_agent::switch(name, &ctx), &ctx)
        }
        Some(ConfigCommands::List) => handle_config_agent_result(config_agent::list(&ctx), &ctx),
        Some(ConfigCommands::Delete(args)) => {
            handle_config_agent_result(config_agent::delete(args, &ctx), &ctx)
        }
        Some(ConfigCommands::Add { target }) => {
            let result = config_agent::add(target, &ctx).await;
            handle_config_agent_result(result, &ctx)
        }
        Some(ConfigCommands::Edit(args)) => {
            let result = config_agent::edit(args, &ctx).await;
            handle_config_agent_result(result, &ctx)
        }
        None => config_wizard::run_config_wizard().await,
    }
}

fn handle_config_agent_result(
    result: std::result::Result<config_agent::AgentOutput, config_agent::AgentError>,
    ctx: &CliContext,
) -> Result<()> {
    match result {
        Ok(output) => {
            config_agent::print_success(output, ctx);
            Ok(())
        }
        Err(error) => {
            let exit_code = error.exit_code();
            config_agent::print_error(&error, ctx);
            std::process::exit(exit_code);
        }
    }
}

pub async fn handle_language(value: Option<String>) -> Result<()> {
    let language = match value {
        Some(value) => Language::from_code(&value).ok_or_else(|| {
            Error::Language(format!(
                "Unsupported language '{value}'. Use 'en' or 'zh-CN'."
            ))
        })?,
        None => {
            let current = Language::current();
            println!("{}", language_title(current));
            println!(
                "{} {}",
                theme::muted(language_label("Current:", "当前语言：", current)),
                theme::command(current.label()).bold()
            );
            println!();
            let choices = vec![
                Language::En.label().to_string(),
                Language::ZhCn.label().to_string(),
            ];
            match prompt_select(language_prompt(current), &choices, 0)? {
                SelectOutcome::Selected(0) => Language::En,
                SelectOutcome::Selected(1) => Language::ZhCn,
                SelectOutcome::Back | SelectOutcome::Quit => {
                    println!("{}", theme::muted(language_no_change(current)));
                    return Ok(());
                }
                SelectOutcome::Selected(_) => {
                    unreachable!("selection is constrained by language list")
                }
            }
        }
    };

    i18n::save_language(language)?;
    println!("{}", theme::success(language_saved(language)).bold());
    Ok(())
}

fn language_title(language: Language) -> String {
    theme::brand_title(match language {
        Language::En => "OPENVIKING LANGUAGE".to_string(),
        Language::ZhCn => "OPENVIKING 语言设置".to_string(),
    })
    .bold()
    .to_string()
}

fn language_prompt(language: Language) -> &'static str {
    match language {
        Language::En => "Choose language",
        Language::ZhCn => "选择语言",
    }
}

fn language_label<'a>(en: &'a str, zh: &'a str, language: Language) -> &'a str {
    match language {
        Language::En => en,
        Language::ZhCn => zh,
    }
}

fn language_saved(language: Language) -> &'static str {
    match language {
        Language::En => "Language set to English.",
        Language::ZhCn => "语言已切换为简体中文。",
    }
}

fn language_no_change(language: Language) -> &'static str {
    match language {
        Language::En => "Language was not changed.",
        Language::ZhCn => "语言未更改。",
    }
}

/// Interactive configuration switcher
async fn handle_config_switch() -> Result<()> {
    let store = ConfigStore::new()?;
    let report = store.list_configs_report()?;
    let invalid_config_names: Vec<String> = report
        .invalid_configs
        .iter()
        .map(|config| config.name.clone())
        .collect();
    let configs = report.configs;

    if configs.is_empty() {
        print!(
            "{}",
            config_command_ui::render_no_saved_configs(&invalid_config_names)
        );
        return Ok(());
    }

    let active = configs.iter().find(|config| config.is_active);
    print!(
        "{}",
        config_command_ui::render_switch_header(
            active.map(|config| config.name.as_str()),
            active.map(|config| config.kind),
            &invalid_config_names,
        )
    );

    loop {
        let rows: Vec<SwitchConfigRow> = configs
            .iter()
            .map(|config| SwitchConfigRow {
                name: config.name.clone(),
                kind: config.kind,
                is_active: config.is_active,
            })
            .collect();
        let labels = config_command_ui::switch_labels(&rows);
        let language = Language::current();
        let index = match prompt_select(
            config_switch_prompt(language, "Choose config", "选择配置"),
            &labels,
            0,
        )? {
            SelectOutcome::Selected(index) => index,
            SelectOutcome::Back | SelectOutcome::Quit => {
                println!("{}", theme::muted(config_not_changed(language)));
                return Ok(());
            }
        };

        let selected = configs[index].clone();
        if selected.is_active {
            println!(
                "{}",
                theme::muted(config_already_active(language, &selected.name))
            );
            return Ok(());
        }

        let confirmation = switch_confirmation_labels();
        match switch_confirmation_decision(prompt_select(
            &config_switch_confirm_prompt(language, &selected.name),
            &confirmation,
            0,
        )?) {
            SwitchConfirmationDecision::Confirm => {
                println!("{}", theme::muted(validating_target_config(language)));
                if let Err(error) = config_wizard::validate_config(&selected.config).await {
                    print!(
                        "{}",
                        config_command_ui::render_switch_validation_failure(&selected.name, &error,)
                    );
                    return Err(Error::AlreadyReported);
                }
                store.activate_config(&selected.name)?;
                print!(
                    "{}",
                    config_command_ui::render_switch_success(&selected.name)
                );
                return Ok(());
            }
            SwitchConfirmationDecision::Back => continue,
            SwitchConfirmationDecision::Quit => {
                println!("{}", theme::muted(config_not_changed(language)));
                return Ok(());
            }
        }
    }
}

fn active_config_name(store: &ConfigStore) -> Result<Option<String>> {
    Ok(store
        .list_configs()?
        .into_iter()
        .find(|entry| entry.is_active)
        .map(|entry| entry.name))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SelectOutcome {
    Selected(usize),
    Back,
    Quit,
}

fn prompt_select(prompt: &str, items: &[String], default: usize) -> Result<SelectOutcome> {
    use std::io::{self, Write};

    use crossterm::{
        cursor,
        event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
        execute, terminal,
    };

    if items.is_empty() {
        return Ok(SelectOutcome::Back);
    }

    struct RawGuard {
        hide_cursor: bool,
    }
    impl RawGuard {
        fn enter() -> Result<Self> {
            terminal::enable_raw_mode()?;
            let mut stdout = io::stdout();
            if let Err(error) = execute!(stdout, cursor::Hide) {
                let _ = terminal::disable_raw_mode();
                return Err(error.into());
            }
            Ok(Self { hide_cursor: true })
        }
    }
    impl Drop for RawGuard {
        fn drop(&mut self) {
            let _ = crossterm::terminal::disable_raw_mode();
            if self.hide_cursor {
                let _ = execute!(io::stdout(), cursor::Show);
            }
        }
    }

    let mut selected = default.min(items.len().saturating_sub(1));
    let _raw_guard = RawGuard::enter()?;

    // Initial render
    let lines = select_lines(prompt, items, selected);
    let mut rendered_region = RenderedSelectRegion::from_lines(&lines, live_select_columns());
    print!("{}", live_select_block(&lines));
    io::stdout().flush()?;

    loop {
        match event::read()? {
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                match key.code {
                    KeyCode::Up => {
                        selected = if selected == 0 {
                            items.len().saturating_sub(1)
                        } else {
                            selected - 1
                        };
                    }
                    KeyCode::Down => selected = (selected + 1) % items.len(),
                    KeyCode::Enter | KeyCode::Char('\n') | KeyCode::Char('\r') => {
                        clear_rendered_region(&rendered_region)?;
                        return Ok(SelectOutcome::Selected(selected));
                    }
                    KeyCode::Esc => {
                        clear_rendered_region(&rendered_region)?;
                        return Ok(SelectOutcome::Back);
                    }
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        clear_rendered_region(&rendered_region)?;
                        return Ok(SelectOutcome::Quit);
                    }
                    _ => continue,
                }
                clear_rendered_region(&rendered_region)?;
                let lines = select_lines(prompt, items, selected);
                rendered_region = RenderedSelectRegion::from_lines(&lines, live_select_columns());
                print!("{}", live_select_block(&lines));
                io::stdout().flush()?;
            }
            Event::Resize(_, _) => {
                clear_rendered_region(&rendered_region)?;
                let lines = select_lines(prompt, items, selected);
                rendered_region = RenderedSelectRegion::from_lines(&lines, live_select_columns());
                print!("{}", live_select_block(&lines));
                io::stdout().flush()?;
            }
            _ => {}
        }
    }

    fn clear_rendered_region(region: &RenderedSelectRegion) -> Result<()> {
        clear_rendered_lines(region.rows_to_clear(live_select_columns()))
    }
}

fn live_select_columns() -> usize {
    crossterm::terminal::size()
        .map(|(columns, _)| usize::from(columns).saturating_sub(1).max(1))
        .unwrap_or(80)
}

#[cfg(test)]
fn rendered_select_rows(lines: &[String], columns: usize) -> usize {
    crate::terminal_ui::rendered_row_count(lines, columns)
}

fn switch_confirmation_labels() -> Vec<String> {
    match Language::current() {
        Language::En => vec!["Yes".to_string(), "No".to_string()],
        Language::ZhCn => vec!["是".to_string(), "否".to_string()],
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SwitchConfirmationDecision {
    Confirm,
    Back,
    Quit,
}

fn switch_confirmation_decision(outcome: SelectOutcome) -> SwitchConfirmationDecision {
    match outcome {
        SelectOutcome::Selected(0) => SwitchConfirmationDecision::Confirm,
        SelectOutcome::Selected(_) | SelectOutcome::Back => SwitchConfirmationDecision::Back,
        SelectOutcome::Quit => SwitchConfirmationDecision::Quit,
    }
}

fn select_lines(prompt: &str, items: &[String], selected: usize) -> Vec<String> {
    let language = Language::current();
    let mut lines = Vec::new();
    lines.push(format!(
        "{} {}",
        theme::prompt("?").bold(),
        theme::strong(prompt)
    ));
    lines.push(format!("  {}", theme::muted(select_hint(language))));
    lines.push(String::new());
    for (index, item) in items.iter().enumerate() {
        let marker = if index == selected {
            theme::selection("›").bold().to_string()
        } else {
            " ".to_string()
        };
        lines.push(format!(
            "  {marker} {}",
            style_select_item(item, index == selected)
        ));
    }
    lines
}

fn style_select_item(item: &str, selected: bool) -> String {
    if contains_ansi_escape(item) {
        return item.to_string();
    }
    if selected {
        theme::selection(item).bold().to_string()
    } else {
        theme::body(item).to_string()
    }
}

fn contains_ansi_escape(value: &str) -> bool {
    value.contains("\u{1b}[")
}

fn select_hint(language: Language) -> &'static str {
    match language {
        Language::En => "↑/↓ choose · Enter select · Esc back · Ctrl+C exit",
        Language::ZhCn => "↑/↓ 选择 · Enter 确认 · Esc 返回 · Ctrl+C 退出",
    }
}

fn config_switch_prompt<'a>(language: Language, en: &'a str, zh: &'a str) -> &'a str {
    language_label(en, zh, language)
}

fn config_switch_confirm_prompt(language: Language, name: &str) -> String {
    match language {
        Language::En => format!("Switch active config to {name}?"),
        Language::ZhCn => format!("切换当前配置为 {name}？"),
    }
}

fn config_not_changed(language: Language) -> &'static str {
    match language {
        Language::En => "No config was changed.",
        Language::ZhCn => "配置未更改。",
    }
}

fn config_already_active(language: Language, name: &str) -> String {
    match language {
        Language::En => format!("Config '{name}' is already active."),
        Language::ZhCn => format!("配置 '{name}' 已是当前配置。"),
    }
}

fn validating_target_config(language: Language) -> &'static str {
    match language {
        Language::En => "Validating target config...",
        Language::ZhCn => "正在验证目标配置...",
    }
}

pub async fn handle_read(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::read(&client, &uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_abstract(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::abstract_content(&client, &uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_overview(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::overview(&client, &uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_write(
    uri: String,
    content: Option<String>,
    from_file: Option<String>,
    mode: String,
    wait: bool,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    let payload = match (content, from_file) {
        (Some(value), None) => value,
        (None, Some(path)) => std::fs::read_to_string(path)
            .map_err(|e| Error::Client(format!("Failed to read --from-file: {}", e)))?,
        _ => {
            return Err(Error::Client(
                "Specify exactly one of --content or --from-file".into(),
            ));
        }
    };
    commands::content::write(
        &client,
        &uri,
        &payload,
        &mode,
        wait,
        timeout,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_set_tags(
    uri: String,
    tags: Vec<String>,
    mode: String,
    recursive: bool,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::content::set_tags(
        &client,
        &uri,
        tags,
        &mode,
        recursive,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_reindex(uri: String, mode: String, wait: bool, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::reindex(&client, &uri, &mode, wait, ctx.output_format, ctx.compact).await
}

pub async fn handle_get(uri: String, local_path: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::get(&client, &uri, &local_path).await
}

pub async fn handle_find(
    query: String,
    uri: String,
    node_limit: i32,
    threshold: Option<f64>,
    after: Option<String>,
    before: Option<String>,
    level: Option<Vec<i32>>,
    context_type: Option<Vec<String>>,
    tags: Option<Vec<String>>,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![format!("--uri={}", uri), format!("-n {}", node_limit)];
    if let Some(t) = threshold {
        params.push(format!("--threshold {}", t));
    }
    append_time_filter_params(&mut params, after.as_deref(), before.as_deref());
    if let Some(ref l) = level {
        params.push(format!(
            "--level {}",
            l.iter()
                .map(|v| v.to_string())
                .collect::<Vec<_>>()
                .join(",")
        ));
    }
    if let Some(ref context_types) = context_type {
        params.push(format!("--context-type {}", context_types.join(",")));
    }
    if let Some(ref t) = tags {
        params.push(format!("--tags {}", t.join(",")));
    }
    params.push(format!("\"{}\"", query));
    print_command_echo("ov find", &params.join(" "), ctx.config.echo_command);
    let client = ctx.get_client();
    commands::search::find(
        &client,
        &query,
        &uri,
        node_limit,
        threshold,
        after.as_deref(),
        before.as_deref(),
        None,
        level,
        context_type,
        tags,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_search(
    query: String,
    uri: String,
    session_id: Option<String>,
    node_limit: i32,
    threshold: Option<f64>,
    after: Option<String>,
    before: Option<String>,
    level: Option<Vec<i32>>,
    context_type: Option<Vec<String>>,
    tags: Option<Vec<String>>,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![format!("--uri={}", uri), format!("-n {}", node_limit)];
    if let Some(s) = &session_id {
        params.push(format!("--session-id {}", s));
    }
    if let Some(t) = threshold {
        params.push(format!("--threshold {}", t));
    }
    append_time_filter_params(&mut params, after.as_deref(), before.as_deref());
    if let Some(ref l) = level {
        params.push(format!(
            "--level {}",
            l.iter()
                .map(|v| v.to_string())
                .collect::<Vec<_>>()
                .join(",")
        ));
    }
    if let Some(ref context_types) = context_type {
        params.push(format!("--context-type {}", context_types.join(",")));
    }
    if let Some(ref t) = tags {
        params.push(format!("--tags {}", t.join(",")));
    }
    params.push(format!("\"{}\"", query));
    print_command_echo("ov search", &params.join(" "), ctx.config.echo_command);
    let client = ctx.get_client();
    commands::search::search(
        &client,
        &query,
        &uri,
        session_id,
        node_limit,
        threshold,
        after.as_deref(),
        before.as_deref(),
        None,
        level,
        context_type,
        tags,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub fn append_time_filter_params(
    params: &mut Vec<String>,
    after: Option<&str>,
    before: Option<&str>,
) {
    if let Some(value) = after {
        params.push(format!("--after {}", value));
    }
    if let Some(value) = before {
        params.push(format!("--before {}", value));
    }
}

/// Print command with specified parameters for debugging
pub fn print_command_echo(command: &str, params: &str, echo_enabled: bool) {
    if echo_enabled {
        println!("cmd: {} {}", command, params);
    }
}

pub async fn handle_ls(
    uri: String,
    simple: bool,
    recursive: bool,
    abs_limit: i32,
    show_all_hidden: bool,
    node_limit: i32,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![
        uri.clone(),
        format!("-l {}", abs_limit),
        format!("-n {}", node_limit),
    ];
    if simple {
        params.push("-s".to_string());
    }
    if recursive {
        params.push("-r".to_string());
    }
    if show_all_hidden {
        params.push("-a".to_string());
    }
    print_command_echo("ov ls", &params.join(" "), ctx.config.echo_command);

    let client = ctx.get_client();
    let api_output = if ctx.compact { "agent" } else { "original" };
    commands::filesystem::ls(
        &client,
        &uri,
        simple,
        recursive,
        api_output,
        abs_limit,
        show_all_hidden,
        node_limit,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_tree(
    uri: String,
    abs_limit: i32,
    show_all_hidden: bool,
    node_limit: i32,
    level_limit: i32,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![
        uri.clone(),
        format!("-l {}", abs_limit),
        format!("-n {}", node_limit),
        format!("-L {}", level_limit),
    ];
    if show_all_hidden {
        params.push("-a".to_string());
    }
    print_command_echo("ov tree", &params.join(" "), ctx.config.echo_command);

    let client = ctx.get_client();
    let api_output = if ctx.compact { "agent" } else { "original" };
    commands::filesystem::tree(
        &client,
        &uri,
        api_output,
        abs_limit,
        show_all_hidden,
        node_limit,
        level_limit,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_mkdir(uri: String, description: Option<String>, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::mkdir(
        &client,
        &uri,
        description.as_deref(),
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_rm(
    uri: String,
    recursive: bool,
    wait: bool,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::rm(
        &client,
        &uri,
        recursive,
        wait,
        timeout,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_mv(from_uri: String, to_uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::mv(&client, &from_uri, &to_uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_stat(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::stat(&client, &uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_attrs(uri: String, key: Option<String>, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::attrs(
        &client,
        &uri,
        key.as_deref(),
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_grep(
    uri: String,
    exclude_uri: Option<String>,
    pattern: String,
    ignore_case: bool,
    node_limit: i32,
    level_limit: i32,
    ctx: CliContext,
) -> Result<()> {
    // Prevent grep from root directory to avoid excessive server load and timeouts
    if uri == "viking://" || uri == "viking:///" {
        return Err(Error::Client(format!(
            "Cannot grep from root directory 'viking://'. Use a more specific scope, for example `ov grep --uri=viking://resources {pattern}`."
        )));
    }

    let mut params = vec![
        format!("--uri={}", uri),
        format!("-n {}", node_limit),
        format!("-L {}", level_limit),
    ];
    if let Some(excluded) = &exclude_uri {
        params.push(format!("-x {}", excluded));
    }
    if ignore_case {
        params.push("-i".to_string());
    }
    params.push(format!("\"{}\"", pattern));
    print_command_echo("ov grep", &params.join(" "), ctx.config.echo_command);
    let client = ctx.get_client();
    commands::search::grep(
        &client,
        &uri,
        exclude_uri,
        &pattern,
        ignore_case,
        node_limit,
        level_limit,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_glob(
    pattern: String,
    uri: String,
    node_limit: i32,
    ctx: CliContext,
) -> Result<()> {
    let params = [
        format!("--uri={}", uri),
        format!("-n {}", node_limit),
        format!("\"{}\"", pattern),
    ];
    print_command_echo("ov glob", &params.join(" "), ctx.config.echo_command);
    let client = ctx.get_client();
    commands::search::glob(
        &client,
        &pattern,
        &uri,
        node_limit,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_health(ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();

    // Reuse the system health command
    let _ = commands::system::health(&client, Some(&ctx.config), ctx.output_format, ctx.compact)
        .await?;

    Ok(())
}

pub async fn handle_tui(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();

    // Probe health endpoint first with a short timeout
    println!("Connecting to {}...", ctx.config.url);
    match client.get::<serde_json::Value>("/health", &[]).await {
        Ok(value) => {
            let healthy = value
                .get("healthy")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if !healthy {
                println!("Warning: Server reports unhealthy status");
            }
        }
        Err(e) => return Err(e),
    }

    tui::run_tui(client, &uri).await
}

#[cfg(test)]
mod config_switch_prompt_tests {
    use super::*;

    #[test]
    fn live_select_block_uses_crlf_for_raw_mode_rows() {
        let lines = vec!["Choose config".to_string(), "  › local".to_string()];

        let rendered = live_select_block(&lines);

        assert_eq!(rendered, "Choose config\r\n  › local\r\n");
        assert!(!rendered.contains("config\n"));
    }

    #[test]
    fn switch_selector_counts_physical_rows_after_wrapping_and_ansi_styles() {
        let lines = vec![
            "\u{1b}[31m12345678901\u{1b}[0m".to_string(),
            "short".to_string(),
        ];

        assert_eq!(rendered_select_rows(&lines, 10), 3);
    }

    #[test]
    fn switch_selector_recomputes_clear_rows_after_resize() {
        let lines = vec!["x".repeat(90)];
        let region = RenderedSelectRegion::from_lines(&lines, 90);

        assert_eq!(rendered_select_rows(&lines, 90), 1);
        assert_eq!(rendered_select_rows(&lines, 30), 3);
        assert_eq!(region.rows_to_clear(30), 3);
    }

    #[test]
    fn switch_selector_hint_uses_esc_back_language() {
        let lines = select_lines("Choose config", &["local".to_string()], 0);
        let plain = strip_ansi(&lines.join("\n"));

        assert!(plain.contains("Esc back"));
        assert!(!plain.contains("Esc cancel"));
    }

    #[test]
    fn switch_confirmation_labels_are_yes_no_only() {
        assert_eq!(
            switch_confirmation_labels(),
            vec!["Yes".to_string(), "No".to_string()]
        );
    }

    #[test]
    fn switch_confirmation_maps_no_and_esc_to_back_but_ctrl_c_to_quit() {
        assert_eq!(
            switch_confirmation_decision(SelectOutcome::Selected(0)),
            SwitchConfirmationDecision::Confirm
        );
        assert_eq!(
            switch_confirmation_decision(SelectOutcome::Selected(1)),
            SwitchConfirmationDecision::Back
        );
        assert_eq!(
            switch_confirmation_decision(SelectOutcome::Back),
            SwitchConfirmationDecision::Back
        );
        assert_eq!(
            switch_confirmation_decision(SelectOutcome::Quit),
            SwitchConfirmationDecision::Quit
        );
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::new();
        let mut chars = input.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' {
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
            } else {
                output.push(ch);
            }
        }
        output
    }
}
