use crate::client;
use crate::commands;
use crate::config::merge_csv_options;
use crate::error::{Error, Result};
use crate::tui;
use crate::CliContext;

pub async fn handle_add_resource(
    mut path: String,
    to: Option<String>,
    parent: Option<String>,
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

    // Check that only one of --to or --parent is set
    if to.is_some() && parent.is_some() {
        eprintln!("Error: Cannot specify both --to and --parent at the same time.");
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
    );
    commands::resources::add_resource(
        &client,
        &path,
        to,
        parent,
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

pub async fn handle_export(uri: String, to: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::export(&client, &uri, &to, ctx.output_format, ctx.compact).await
}

pub async fn handle_import(
    file_path: String,
    target_uri: String,
    force: bool,
    no_vectorize: bool,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::import(
        &client,
        &file_path,
        &target_uri,
        force,
        no_vectorize,
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
        AdminCommands::ListUsers { account_id } => {
            commands::admin::list_users(&client, &account_id, ctx.output_format, ctx.compact).await
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

use crate::ConfigCommands;
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
    append: bool,
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
        append,
        wait,
        timeout,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

pub async fn handle_reindex(uri: String, regenerate: bool, wait: bool, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::reindex(
        &client,
        &uri,
        regenerate,
        wait,
        ctx.output_format,
        ctx.compact,
    )
    .await
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

pub async fn handle_glob(pattern: String, uri: String, node_limit: i32, ctx: CliContext) -> Result<()> {
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
    tui::run_tui(client, &uri).await
}
