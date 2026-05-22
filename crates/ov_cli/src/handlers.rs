use crate::CliContext;
use crate::PrivacyCommands;
use crate::client;
use crate::commands;
use crate::config::merge_csv_options;
use crate::error::{Error, Result};
use crate::tui;

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
            eprintln!("Error: Path '{}' does not exist.", path);

            // Check if there might be unquoted spaces
            use std::env;
            let args: Vec<String> = env::args().collect();

            if let Some(add_resource_pos) =
                args.iter().position(|s| s == "add-resource" || s == "add")
            {
                if args.len() > add_resource_pos + 2 {
                    let extra_args = &args[add_resource_pos + 2..];
                    let suggested_path = format!("{} {}", path, extra_args.join(" "));
                    eprintln!(
                        "\nIt looks like you may have forgotten to quote a path with spaces."
                    );
                    eprintln!("Suggested command: ov add-resource \"{}\"", suggested_path);
                }
            }

            std::process::exit(1);
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
        eprintln!(
            "Error: Cannot specify more than one of --to, --parent, or --parent-auto-create at the same time."
        );
        std::process::exit(1);
    }

    let strict = strict_mode;
    let directly_upload_media = !no_directly_upload_media;

    let effective_ignore_dirs =
        merge_csv_options(ctx.config.upload.ignore_dirs.clone(), ignore_dirs);
    let effective_include = merge_csv_options(ctx.config.upload.include.clone(), include);
    let effective_exclude = merge_csv_options(ctx.config.upload.exclude.clone(), exclude);

    let effective_timeout = if wait {
        timeout.unwrap_or(60.0).max(ctx.config.timeout)
    } else {
        ctx.config.timeout
    };
    let client = client::HttpClient::new(
        &ctx.config.url,
        ctx.config.api_key.clone(),
        ctx.config.agent_id.clone(),
        ctx.config.account.clone(),
        ctx.config.user.clone(),
        effective_timeout,
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
        ctx.output_format,
        ctx.compact,
        ctx.should_show_progress(),
        ctx.is_verbose(),
    )
    .await
}

pub async fn handle_add_skill(
    data: String,
    wait: bool,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::resources::add_skill(
        &client,
        &data,
        wait,
        timeout,
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

use crate::SystemCommands;

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
        ObserverCommands::Transaction => {
            commands::observer::transaction(&client, ctx.output_format, ctx.compact).await
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
        } => {
            commands::session::add_message(
                &client,
                &session_id,
                &role,
                &content,
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
        } => {
            commands::admin::create_account(
                &client,
                &account_id,
                &admin_user_id,
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
        AdminCommands::RegisterUser {
            account_id,
            user_id,
            role,
        } => {
            commands::admin::register_user(
                &client,
                &account_id,
                &user_id,
                &role,
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
        AdminCommands::ListAgents { account_id } => {
            commands::admin::list_agents(&client, &account_id, ctx.output_format, ctx.compact).await
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
        } => {
            commands::admin::regenerate_key(
                &client,
                &account_id,
                &user_id,
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
use crate::output;

// Config commands intentionally edit the persisted ovcli.conf files. Runtime
// overrides carried in CliContext should not change what gets shown or saved.
pub async fn handle_config(cmd: Option<ConfigCommands>, _ctx: CliContext) -> Result<()> {
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
        Some(ConfigCommands::Switch) => handle_config_switch().await,
        None => config_wizard::run_config_wizard().await,
    }
}

/// Interactive configuration switcher
async fn handle_config_switch() -> Result<()> {
    use colored::Colorize;

    let store = ConfigStore::new()?;
    let configs = store.list_configs()?;

    if configs.is_empty() {
        print!("{}", config_command_ui::render_no_saved_configs());
        return Ok(());
    }

    let active = configs.iter().find(|config| config.is_active);
    print!(
        "{}",
        config_command_ui::render_switch_header(
            active.map(|config| config.name.as_str()),
            active.map(|config| config.kind),
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
        let index = match prompt_select("Choose config", &labels, 0)? {
            SelectOutcome::Selected(index) => index,
            SelectOutcome::Back | SelectOutcome::Quit => {
                println!("{}", "No config was changed.".dimmed());
                return Ok(());
            }
        };

        let selected = configs[index].clone();
        if selected.is_active {
            println!(
                "{}",
                format!("Config '{}' is already active.", selected.name).dimmed()
            );
            return Ok(());
        }

        let confirmation = switch_confirmation_labels();
        match switch_confirmation_decision(prompt_select(
            &format!("Switch active config to {}?", selected.name),
            &confirmation,
            0,
        )?) {
            SwitchConfirmationDecision::Confirm => {
                println!("{}", "Validating target config...".dimmed());
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
                println!("{}", "No config was changed.".dimmed());
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
        event::{self, Event, KeyCode, KeyModifiers},
        execute,
        terminal::{self, Clear, ClearType},
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
    let mut rendered_lines = 0usize;
    let _raw_guard = RawGuard::enter()?;

    loop {
        clear_rendered_lines(rendered_lines)?;
        let lines = select_lines(prompt, items, selected);
        rendered_lines = lines.len();
        print!("{}", live_select_block(&lines));
        io::stdout().flush()?;

        if let Event::Key(key) = event::read()? {
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
                    clear_rendered_lines(rendered_lines)?;
                    return Ok(SelectOutcome::Selected(selected));
                }
                KeyCode::Esc => {
                    clear_rendered_lines(rendered_lines)?;
                    return Ok(SelectOutcome::Back);
                }
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    clear_rendered_lines(rendered_lines)?;
                    return Ok(SelectOutcome::Quit);
                }
                _ => {}
            }
        }
    }

    fn clear_rendered_lines(lines: usize) -> Result<()> {
        if lines == 0 {
            return Ok(());
        }
        let mut stdout = io::stdout();
        execute!(
            stdout,
            cursor::MoveUp(lines as u16),
            cursor::MoveToColumn(0)
        )?;
        for line in 0..lines {
            execute!(
                stdout,
                cursor::MoveToColumn(0),
                Clear(ClearType::CurrentLine)
            )?;
            if line + 1 < lines {
                execute!(stdout, cursor::MoveDown(1))?;
            }
        }
        execute!(
            stdout,
            cursor::MoveUp(lines.saturating_sub(1) as u16),
            cursor::MoveToColumn(0)
        )?;
        Ok(())
    }
}

fn live_select_block(lines: &[String]) -> String {
    if lines.is_empty() {
        return String::new();
    }

    let mut rendered = lines.join("\r\n");
    rendered.push_str("\r\n");
    rendered
}

fn switch_confirmation_labels() -> Vec<String> {
    vec!["Yes".to_string(), "No".to_string()]
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
    use colored::Colorize;

    let mut lines = Vec::new();
    lines.push(format!("{} {}", "?".yellow().bold(), prompt.bold()));
    lines.push(format!(
        "  {}",
        "↑/↓ choose · Enter select · Esc back · Ctrl+C exit".dimmed()
    ));
    lines.push(String::new());
    for (index, item) in items.iter().enumerate() {
        let marker = if index == selected {
            "›".green().bold().to_string()
        } else {
            " ".to_string()
        };
        lines.push(format!("  {marker} {item}"));
    }
    lines
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

pub async fn handle_rm(uri: String, recursive: bool, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::rm(&client, &uri, recursive, ctx.output_format, ctx.compact).await
}

pub async fn handle_mv(from_uri: String, to_uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::mv(&client, &from_uri, &to_uri, ctx.output_format, ctx.compact).await
}

pub async fn handle_stat(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::stat(&client, &uri, ctx.output_format, ctx.compact).await
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
        eprintln!(
            "Error: Cannot grep from root directory 'viking://'.\n\
             Grep from root would search across all scopes (resources, user, agent, session, queue, temp),\n\
             which may cause server timeout or excessive load.\n\
             Please specify a more specific scope, e.g.:\n\
               ov grep --uri=viking://resources '{}'\n\
               ov grep --uri=viking://user '{}'",
            pattern, pattern
        );
        std::process::exit(1);
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
    let params = vec![
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
        Err(e) => {
            println!("Error: Failed to connect to server at {}", ctx.config.url);
            println!("{}", e);
            println!("\nPlease check:");
            println!("  1. The server is running");
            println!("  2. The URL is correct");
            println!("  3. Your API key is valid (if required)");
            println!("\nRun `ov config` to reconfigure if needed.");
            std::process::exit(1);
        }
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
