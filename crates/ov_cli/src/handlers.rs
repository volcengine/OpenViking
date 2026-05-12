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
            let _ = commands::system::health(&client, ctx.output_format, ctx.compact).await?;
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
use crate::base_client::BaseClient;
use crate::config::Config;
use crate::output;

pub async fn handle_config(cmd: ConfigCommands, _ctx: CliContext) -> Result<()> {
    match cmd {
        ConfigCommands::Show => {
            let config = Config::load()?;
            output::output_success(
                &serde_json::to_value(config).unwrap(),
                output::OutputFormat::Json,
                true,
            );
            Ok(())
        }
        ConfigCommands::Validate => match Config::load() {
            Ok(_) => {
                println!("Configuration is valid");
                Ok(())
            }
            Err(e) => Err(Error::Config(e.to_string())),
        },
        ConfigCommands::SetupCli => handle_setup_cli().await,
        ConfigCommands::Switch => handle_config_switch().await,
    }
}

/// Interactive setup for CLI configuration
async fn handle_setup_cli() -> Result<()> {
    use colored::Colorize;
    use rustyline::DefaultEditor;

    println!("{}", "Welcome to OpenViking CLI Setup!".bold().green());
    println!();

    // Load existing config if available
    let mut config = match Config::load_default() {
        Ok(c) => c,
        Err(_) => Config::default(),
    };

    let mut rl = DefaultEditor::new()
        .map_err(|e| Error::Config(format!("Failed to initialize input editor: {}", e)))?;

    // Step 1: Get server URL
    let default_url = config.url.clone();
    let prompt = format!("OpenViking Server URL [{}]: ", default_url);
    let url_input = rl.readline(&prompt).unwrap_or_default();
    let url = if url_input.trim().is_empty() {
        default_url
    } else {
        url_input.trim().to_string()
    };

    // Update config with URL immediately for probing
    config.url = url.clone();

    // Step 2: Probe health endpoint
    println!();
    println!("{}", "Probing server health...".blue());
    let probe_result = probe_health(&url, 5.0).await;

    let needs_api_key = match probe_result {
        Ok((healthy, auth_required)) => {
            if healthy {
                println!("{}", "✓ Server is healthy!".green());
            } else {
                println!("{}", "⚠ Server responded but reports unhealthy".yellow());
            }
            auth_required
        }
        Err(e) => {
            println!("{}", format!("⚠ Could not reach server: {}", e).yellow());
            // Default to asking for API key if we can't probe
            true
        }
    };

    // Step 3: Ask for API key if needed
    if needs_api_key {
        println!();
        let default_key = config.api_key.clone().unwrap_or_default();
        let prompt = if default_key.is_empty() {
            "API Key (optional, press Enter to skip): ".to_string()
        } else {
            format!("API Key [{}]: ", "*".repeat(default_key.len().min(8)))
        };
        let key_input = rl.readline(&prompt).unwrap_or_default();
        if !key_input.trim().is_empty() {
            config.api_key = Some(key_input.trim().to_string());
        } else if default_key.is_empty() {
            config.api_key = None;
        }
    }

    // Step 4: Save config
    println!();
    let config_path = crate::config::default_config_path()?;
    println!(
        "{}",
        format!("Saving configuration to: {}", config_path.to_string_lossy()).blue()
    );
    config.save_default()?;

    // Step 5: Optionally save a named backup
    println!();
    let prompt = "Name this configuration (optional, press Enter to skip): ";
    let name_input = rl.readline(prompt).unwrap_or_default();
    if !name_input.trim().is_empty() {
        let config_name = name_input.trim().to_string();
        if let Some(parent) = config_path.parent() {
            let backup_path = parent.join(format!("ovcli.conf.{}", config_name));
            std::fs::copy(&config_path, &backup_path).map_err(|e| {
                Error::Config(format!("Failed to save configuration backup: {}", e))
            })?;
            println!(
                "{}",
                format!(
                    "✓ Configuration saved as backup: {}",
                    backup_path.to_string_lossy()
                )
                .green()
            );
        }
    }

    println!();
    println!("{}", "✓ Setup complete!".bold().green());
    println!(
        "{}",
        "You can now use the 'ov' command to interact with OpenViking.".dimmed()
    );
    println!(
        "{}",
        "Use 'ov config switch' to switch between saved configurations.".dimmed()
    );

    Ok(())
}

/// Probe health endpoint to check server status and auth requirement
async fn probe_health(base_url: &str, timeout_secs: f64) -> Result<(bool, bool)> {
    let client = BaseClient::new_simple(base_url, timeout_secs);

    // First try without API key
    let result: Result<serde_json::Value> = client.get("/health", &[]).await;

    match result {
        Ok(value) => {
            let healthy = value
                .get("healthy")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            Ok((healthy, false))
        }
        Err(Error::Api(msg)) => {
            // If we get auth-related errors, auth is required
            if msg.contains("401")
                || msg.contains("403")
                || msg.contains("unauthorized")
                || msg.contains("forbidden")
                || msg.contains("AuthenticationError")
            {
                Ok((false, true))
            } else {
                Ok((false, false))
            }
        }
        Err(_) => Ok((false, true)),
    }
}

/// Interactive configuration switcher
async fn handle_config_switch() -> Result<()> {
    use colored::Colorize;

    // Step 1: Find all available configurations
    let config_path = crate::config::default_config_path()?;
    let config_dir = config_path
        .parent()
        .ok_or_else(|| Error::Config("Could not determine config directory".to_string()))?;

    let mut configs = Vec::new();

    if let Ok(entries) = std::fs::read_dir(config_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if let Some(filename) = path.file_name().and_then(|f| f.to_str()) {
                if filename.starts_with("ovcli.conf.") && filename != "ovcli.conf" {
                    if let Some(name) = filename.strip_prefix("ovcli.conf.") {
                        // Try to load the config to get its URL for display
                        let url = if let Ok(cfg) = Config::from_file(&path.to_string_lossy()) {
                            cfg.url.clone()
                        } else {
                            String::new()
                        };
                        configs.push((name.to_string(), path, url));
                    }
                }
            }
        }
    }

    if configs.is_empty() {
        println!("{}", "No saved configurations found.".yellow());
        println!(
            "{}",
            "Use 'ov config setup-cli' and save a named configuration first.".dimmed()
        );
        return Ok(());
    }

    // Step 2: Interactive selection using simple numbered menu
    println!("{}", "Select a configuration to use:".bold());
    println!();

    for (i, (name, _, url)) in configs.iter().enumerate() {
        if url.is_empty() {
            println!("  {}. {}", i + 1, name.bold());
        } else {
            println!("  {}. {} ({})", i + 1, name.bold(), url.dimmed());
        }
    }

    println!();
    println!(
        "{}",
        "Enter    '<number>' to use a configuration (e.g. '2')".dimmed()
    );
    println!(
        "{}",
        "Or enter 'del<number>' to delete a configuration (e.g. 'del2')".dimmed()
    );
    println!("{}", "Press Enter without input to cancel".dimmed());

    let mut rl = rustyline::DefaultEditor::new()
        .map_err(|e| Error::Config(format!("Failed to initialize input editor: {}", e)))?;

    loop {
        let input = rl.readline("> ").unwrap_or_default();
        let input = input.trim();

        if input.is_empty() {
            println!();
            println!("{}", "Cancelled.".dimmed());
            return Ok(());
        }

        // Check for delete command
        if let Some(num_str) = input
            .strip_prefix("del")
            .or_else(|| input.strip_prefix("Del"))
        {
            if let Ok(idx) = num_str.parse::<usize>() {
                if idx >= 1 && idx <= configs.len() {
                    let idx = idx - 1;
                    let (name, path, _) = &configs[idx];
                    println!();
                    println!(
                        "{}",
                        format!(
                            "Are you sure you want to delete configuration '{}'? (y/N)",
                            name
                        )
                        .yellow()
                    );

                    let confirm = rl.readline("> ").unwrap_or_default();
                    if confirm.trim().eq_ignore_ascii_case("y")
                        || confirm.trim().eq_ignore_ascii_case("yes")
                    {
                        std::fs::remove_file(path).map_err(|e| {
                            Error::Config(format!("Failed to delete configuration: {}", e))
                        })?;
                        println!("{}", format!("✓ Configuration '{}' deleted.", name).green());
                    } else {
                        println!("{}", "Deletion cancelled.".dimmed());
                    }
                    return Ok(());
                }
            }
            println!("{}", "Invalid selection. Please try again.".yellow());
            continue;
        }

        // Try to parse as selection number
        if let Ok(idx) = input.parse::<usize>() {
            if idx >= 1 && idx <= configs.len() {
                let idx = idx - 1;
                // Handle switching
                let (name, source_path, _) = &configs[idx];

                // First, backup current config as .bak
                if config_path.exists() {
                    let backup_path = config_dir.join("ovcli.conf.bak");
                    std::fs::copy(&config_path, &backup_path).map_err(|e| {
                        Error::Config(format!("Failed to backup current config: {}", e))
                    })?;
                }

                // Copy selected config to main config
                std::fs::copy(source_path, &config_path)
                    .map_err(|e| Error::Config(format!("Failed to switch configuration: {}", e)))?;

                println!();
                println!(
                    "{}",
                    format!("✓ Switched to configuration '{}'", name)
                        .bold()
                        .green()
                );
                println!(
                    "{}",
                    format!("Configuration saved to: {}", config_path.to_string_lossy()).dimmed()
                );

                return Ok(());
            }
        }

        println!(
            "{}",
            format!(
                "Invalid selection. Please enter a number between 1 and {}.",
                configs.len()
            )
            .yellow()
        );
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
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![format!("--uri={}", uri), format!("-n {}", node_limit)];
    if let Some(t) = threshold {
        params.push(format!("--threshold {}", t));
    }
    append_time_filter_params(&mut params, after.as_deref(), before.as_deref());
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
    let _ = commands::system::health(&client, ctx.output_format, ctx.compact).await?;

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
            println!("\nRun `ov config setup-cli` to reconfigure if needed.");
            std::process::exit(1);
        }
    }

    tui::run_tui(client, &uri).await
}
