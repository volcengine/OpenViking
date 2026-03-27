mod client;
mod commands;
mod config;
mod error;
mod output;
mod tui;
mod utils;

use clap::{Parser, Subcommand};
use config::Config;
use error::{Error, Result};
use output::OutputFormat;

/// CLI context shared across commands
#[derive(Debug, Clone)]
pub struct CliContext {
    pub config: Config,
    pub output_format: OutputFormat,
    pub compact: bool,
}

impl CliContext {
    pub fn new(
        output_format: OutputFormat,
        compact: bool,
        account: Option<String>,
        user: Option<String>,
        agent_id: Option<String>,
    ) -> Result<Self> {
        let config = Config::load()?;
        Ok(Self::from_config(
            config,
            output_format,
            compact,
            account,
            user,
            agent_id,
        ))
    }

    fn from_config(
        mut config: Config,
        output_format: OutputFormat,
        compact: bool,
        account: Option<String>,
        user: Option<String>,
        agent_id: Option<String>,
    ) -> Self {
        if account.is_some() {
            config.account = account;
        }
        if user.is_some() {
            config.user = user;
        }
        if agent_id.is_some() {
            config.agent_id = agent_id;
        }
        Self {
            config,
            output_format,
            compact,
        }
    }

    pub fn get_client(&self) -> client::HttpClient {
        client::HttpClient::new(
            &self.config.url,
            self.config.api_key.clone(),
            self.config.agent_id.clone(),
            self.config.account.clone(),
            self.config.user.clone(),
            self.config.timeout,
        )
    }
}

#[derive(Parser)]
#[command(name = "openviking")]
#[command(about = "OpenViking - An Agent-native context database")]
#[command(version = env!("OPENVIKING_CLI_VERSION"))]
#[command(arg_required_else_help = true)]
struct Cli {
    /// Output format
    #[arg(short, long, value_enum, default_value = "table", global = true)]
    output: OutputFormat,

    /// Compact representation, defaults to true - compacts JSON output or uses simplified representation for Table output
    #[arg(short, long, global = true, default_value = "true")]
    compact: bool,

    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, global = true)]
    account: Option<String>,

    /// User identifier to send as X-OpenViking-User
    #[arg(long, global = true)]
    user: Option<String>,

    /// Agent identifier to send as X-OpenViking-Agent
    #[arg(long = "agent-id", global = true)]
    agent_id: Option<String>,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Add resources into OpenViking
    AddResource {
        /// Local path or URL to import
        path: String,
        /// Exact target URI (must not exist yet) (cannot be used with --parent)
        #[arg(long)]
        to: Option<String>,
        /// Target parent URI (must already exist and be a directory) (cannot be used with --to)
        #[arg(long)]
        parent: Option<String>,
        /// Reason for import
        #[arg(long, default_value = "")]
        reason: String,
        /// Additional instruction
        #[arg(long, default_value = "")]
        instruction: String,
        /// Wait until processing is complete
        #[arg(long)]
        wait: bool,
        /// Wait timeout in seconds (only used with --wait)
        #[arg(long)]
        timeout: Option<f64>,
        /// No strict mode for directory scanning
        #[arg(long = "no-strict", default_value_t = false)]
        no_strict: bool,
        /// Ignore directories, e.g. --ignore-dirs "node_modules,dist"
        #[arg(long)]
        ignore_dirs: Option<String>,
        /// Include files extensions, e.g. --include "*.pdf,*.md"
        #[arg(long)]
        include: Option<String>,
        /// Exclude files extensions, e.g. --exclude "*.tmp,*.log"
        #[arg(long)]
        exclude: Option<String>,
        /// Do not directly upload media files
        #[arg(long = "no-directly-upload-media", default_value_t = false)]
        no_directly_upload_media: bool,
        /// Watch interval in minutes for automatic resource monitoring (0 = no monitoring)
        #[arg(long, default_value = "0")]
        watch_interval: f64,
    },
    /// Add a skill into OpenViking
    AddSkill {
        /// Skill directory, SKILL.md, or raw content
        data: String,
        /// Wait until processing is complete
        #[arg(long)]
        wait: bool,
        /// Wait timeout in seconds
        #[arg(long)]
        timeout: Option<f64>,
    },
    /// List relations of a resource
    Relations {
        /// Viking URI
        uri: String,
    },
    /// Create relation links from one URI to one or more targets
    Link {
        /// Source URI
        from_uri: String,
        /// One or more target URIs
        to_uris: Vec<String>,
        /// Reason for linking
        #[arg(long, default_value = "")]
        reason: String,
    },
    /// Remove a relation link
    Unlink {
        /// Source URI
        from_uri: String,
        /// Target URI to unlink
        to_uri: String,
    },
    /// Export context as .ovpack
    Export {
        /// Source URI
        uri: String,
        /// Output .ovpack file path
        to: String,
    },
    /// Import .ovpack into target URI
    Import {
        /// Input .ovpack file path
        file_path: String,
        /// Target parent URI
        target_uri: String,
        /// Overwrite when conflicts exist
        #[arg(long)]
        force: bool,
        /// Disable vectorization after import
        #[arg(long)]
        no_vectorize: bool,
    },
    /// Wait for queued async processing to complete
    Wait {
        /// Wait timeout in seconds
        #[arg(long)]
        timeout: Option<f64>,
    },
    /// Show OpenViking component status
    Status,
    /// Quick health check
    Health,
    /// System utility commands
    System {
        #[command(subcommand)]
        action: SystemCommands,
    },
    /// Observer status commands
    Observer {
        #[command(subcommand)]
        action: ObserverCommands,
    },
    /// Session management commands
    Session {
        #[command(subcommand)]
        action: SessionCommands,
    },
    /// Account and user management commands (multi-tenant)
    Admin {
        #[command(subcommand)]
        action: AdminCommands,
    },
    /// List directory contents
    #[command(alias = "list")]
    Ls {
        /// Viking URI to list (default: viking://)
        #[arg(default_value = "viking://")]
        uri: String,
        /// Simple path output (just paths, no table)
        #[arg(short, long)]
        simple: bool,
        /// List all subdirectories recursively
        #[arg(short, long)]
        recursive: bool,
        /// Abstract content limit (only for agent output)
        #[arg(long = "abs-limit", short = 'l', default_value = "256")]
        abs_limit: i32,
        /// Show all hidden files
        #[arg(short, long)]
        all: bool,
        /// Maximum number of nodes to list
        #[arg(
            long = "node-limit",
            short = 'n',
            alias = "limit",
            default_value = "256"
        )]
        node_limit: i32,
    },
    /// Get directory tree
    Tree {
        /// Viking URI to get tree for
        uri: String,
        /// Abstract content limit (only for agent output)
        #[arg(long = "abs-limit", short = 'l', default_value = "128")]
        abs_limit: i32,
        /// Show all hidden files
        #[arg(short, long)]
        all: bool,
        /// Maximum number of nodes to list
        #[arg(
            long = "node-limit",
            short = 'n',
            alias = "limit",
            default_value = "256"
        )]
        node_limit: i32,
        /// Maximum depth level to traverse (default: 3)
        #[arg(short = 'L', long = "level-limit", default_value = "3")]
        level_limit: i32,
    },
    /// Create directory
    Mkdir {
        /// Directory URI to create
        uri: String,
    },
    /// Remove resource
    #[command(alias = "del", alias = "delete")]
    Rm {
        /// Viking URI to remove
        uri: String,
        /// Remove recursively
        #[arg(short, long)]
        recursive: bool,
    },
    /// Move or rename resource
    #[command(alias = "rename")]
    Mv {
        /// Source URI
        from_uri: String,
        /// Target URI
        to_uri: String,
    },
    /// Get resource metadata
    Stat {
        /// Viking URI to get metadata for
        uri: String,
    },
    /// Read file content (L2)
    Read {
        /// Viking URI
        uri: String,
    },
    /// Read abstract content (L0)
    Abstract {
        /// Viking URI
        uri: String,
    },
    /// Read overview content (L1)
    Overview {
        /// Viking URI
        uri: String,
    },
    /// Reindex content at URI (regenerates .abstract.md and .overview.md)
    Reindex {
        /// Viking URI
        uri: String,
        /// Force regenerate summaries even if they exist
        #[arg(short, long)]
        regenerate: bool,
        /// Wait for reindex to complete
        #[arg(long, default_value = "true")]
        wait: bool,
    },
    /// Download file to local path (supports binaries/images)
    Get {
        /// Viking URI
        uri: String,
        /// Local path (must not exist yet)
        local_path: String,
    },
    /// Run semantic retrieval
    Find {
        /// Search query
        query: String,
        /// Target URI
        #[arg(short, long, default_value = "")]
        uri: String,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "10"
        )]
        node_limit: i32,
        /// Score threshold
        #[arg(short, long)]
        threshold: Option<f64>,
    },
    /// Run context-aware retrieval
    Search {
        /// Search query
        query: String,
        /// Target URI
        #[arg(short, long, default_value = "")]
        uri: String,
        /// Session ID for context-aware search
        #[arg(long)]
        session_id: Option<String>,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "10"
        )]
        node_limit: i32,
        /// Score threshold
        #[arg(short, long)]
        threshold: Option<f64>,
    },
    /// Run content pattern search
    Grep {
        /// Target URI
        #[arg(short, long, default_value = "viking://")]
        uri: String,
        /// Search pattern
        pattern: String,
        /// Case insensitive
        #[arg(short, long)]
        ignore_case: bool,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "256"
        )]
        node_limit: i32,
    },
    /// Run file glob pattern search
    Glob {
        /// Glob pattern
        pattern: String,
        /// Search root URI
        #[arg(short, long, default_value = "viking://")]
        uri: String,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "256"
        )]
        node_limit: i32,
    },
    /// Add memory in one shot (creates session, adds messages, commits)
    AddMemory {
        /// Content to memorize. Plain string (treated as user message),
        /// JSON {"role":"...","content":"..."} for a single message,
        /// or JSON array of such objects for multiple messages.
        content: String,
    },
    /// Interactive TUI file explorer
    Tui {
        /// Viking URI to start browsing (default: /)
        #[arg(default_value = "/")]
        uri: String,
    },
    /// Chat with vikingbot agent
    Chat {
        /// Message to send to the agent
        #[arg(short, long)]
        message: Option<String>,
        /// Session ID (defaults to machine unique ID)
        #[arg(short, long)]
        session: Option<String>,
        /// Sender ID
        #[arg(short, long, default_value = "user")]
        sender: String,
        /// Stream the response (default: true)
        #[arg(long, default_value_t = true)]
        stream: bool,
        /// Disable rich formatting / markdown rendering
        #[arg(long)]
        no_format: bool,
        /// Disable command history
        #[arg(long)]
        no_history: bool,
    },
    /// Configuration management
    Config {
        #[command(subcommand)]
        action: ConfigCommands,
    },
    /// Show CLI version
    Version,
}

#[derive(Subcommand)]
enum SystemCommands {
    /// Wait for queued async processing to complete
    Wait {
        /// Wait timeout in seconds
        #[arg(long)]
        timeout: Option<f64>,
    },
    /// Show component status
    Status,
    /// Quick health check
    Health,
    /// Cryptographic key management commands
    Crypto {
        #[command(subcommand)]
        action: commands::crypto::CryptoCommands,
    },
}

#[derive(Subcommand)]
enum ObserverCommands {
    /// Get queue status
    Queue,
    /// Get VikingDB status
    Vikingdb,
    /// Get VLM status
    Vlm,
    /// Get transaction system status
    Transaction,
    /// Get retrieval quality metrics
    Retrieval,
    /// Get overall system status
    System,
}

#[derive(Subcommand)]
enum SessionCommands {
    /// Create a new session
    New,
    /// List sessions
    List,
    /// Get session details
    Get {
        /// Session ID
        session_id: String,
    },
    /// Delete a session
    Delete {
        /// Session ID
        session_id: String,
    },
    /// Add one message to a session
    AddMessage {
        /// Session ID
        session_id: String,
        /// Message role, e.g. user/assistant
        #[arg(long)]
        role: String,
        /// Message content
        #[arg(long)]
        content: String,
    },
    /// Commit a session (archive messages and extract memories)
    Commit {
        /// Session ID
        session_id: String,
    },
}

#[derive(Subcommand)]
enum AdminCommands {
    /// Create a new account with its first admin user
    CreateAccount {
        /// Account ID to create
        account_id: String,
        /// First admin user ID
        #[arg(long = "admin")]
        admin_user_id: String,
    },
    /// List all accounts (ROOT only)
    ListAccounts,
    /// Delete an account and all associated users (ROOT only)
    DeleteAccount {
        /// Account ID to delete
        account_id: String,
    },
    /// Register a new user in an account
    RegisterUser {
        /// Account ID
        account_id: String,
        /// User ID to register
        user_id: String,
        /// Role: admin or user
        #[arg(long, default_value = "user")]
        role: String,
    },
    /// List all users in an account
    ListUsers {
        /// Account ID
        account_id: String,
    },
    /// Remove a user from an account
    RemoveUser {
        /// Account ID
        account_id: String,
        /// User ID to remove
        user_id: String,
    },
    /// Change a user's role (ROOT only)
    SetRole {
        /// Account ID
        account_id: String,
        /// User ID
        user_id: String,
        /// New role: admin or user
        role: String,
    },
    /// Regenerate a user's API key (old key immediately invalidated)
    RegenerateKey {
        /// Account ID
        account_id: String,
        /// User ID
        user_id: String,
    },
}

#[derive(Subcommand)]
enum ConfigCommands {
    /// Show current configuration
    Show,
    /// Validate configuration file
    Validate,
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();

    let output_format = cli.output;
    let compact = cli.compact;

    let ctx = match CliContext::new(
        output_format,
        compact,
        cli.account.clone(),
        cli.user.clone(),
        cli.agent_id.clone(),
    ) {
        Ok(ctx) => ctx,
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(2);
        }
    };

    let result = match cli.command {
        Commands::AddResource {
            path,
            to,
            parent,
            reason,
            instruction,
            wait,
            timeout,
            no_strict,
            ignore_dirs,
            include,
            exclude,
            no_directly_upload_media,
            watch_interval,
        } => {
            handle_add_resource(
                path,
                to,
                parent,
                reason,
                instruction,
                wait,
                timeout,
                no_strict,
                ignore_dirs,
                include,
                exclude,
                no_directly_upload_media,
                watch_interval,
                ctx,
            )
            .await
        }
        Commands::AddSkill {
            data,
            wait,
            timeout,
        } => handle_add_skill(data, wait, timeout, ctx).await,
        Commands::Relations { uri } => handle_relations(uri, ctx).await,
        Commands::Link {
            from_uri,
            to_uris,
            reason,
        } => handle_link(from_uri, to_uris, reason, ctx).await,
        Commands::Unlink { from_uri, to_uri } => handle_unlink(from_uri, to_uri, ctx).await,
        Commands::Export { uri, to } => handle_export(uri, to, ctx).await,
        Commands::Import {
            file_path,
            target_uri,
            force,
            no_vectorize,
        } => handle_import(file_path, target_uri, force, no_vectorize, ctx).await,
        Commands::Wait { timeout } => {
            let client = ctx.get_client();
            commands::system::wait(&client, timeout, ctx.output_format, ctx.compact).await
        }
        Commands::Status => {
            let client = ctx.get_client();
            commands::observer::system(&client, ctx.output_format, ctx.compact).await
        }
        Commands::Health => handle_health(ctx).await,
        Commands::System { action } => handle_system(action, ctx).await,
        Commands::Observer { action } => handle_observer(action, ctx).await,
        Commands::Session { action } => handle_session(action, ctx).await,
        Commands::Admin { action } => handle_admin(action, ctx).await,
        Commands::Ls {
            uri,
            simple,
            recursive,
            abs_limit,
            all,
            node_limit,
        } => handle_ls(uri, simple, recursive, abs_limit, all, node_limit, ctx).await,
        Commands::Tree {
            uri,
            abs_limit,
            all,
            node_limit,
            level_limit,
        } => handle_tree(uri, abs_limit, all, node_limit, level_limit, ctx).await,
        Commands::Mkdir { uri } => handle_mkdir(uri, ctx).await,
        Commands::Rm { uri, recursive } => handle_rm(uri, recursive, ctx).await,
        Commands::Mv { from_uri, to_uri } => handle_mv(from_uri, to_uri, ctx).await,
        Commands::Stat { uri } => handle_stat(uri, ctx).await,
        Commands::AddMemory { content } => handle_add_memory(content, ctx).await,
        Commands::Tui { uri } => handle_tui(uri, ctx).await,
        Commands::Chat {
            message,
            session,
            sender,
            stream,
            no_format,
            no_history,
        } => {
            let session_id = session.or_else(|| config::get_or_create_machine_id().ok());
            let cmd = commands::chat::ChatCommand {
                endpoint: std::env::var("VIKINGBOT_ENDPOINT")
                    .unwrap_or_else(|_| "http://localhost:1933/bot/v1".to_string()),
                api_key: std::env::var("VIKINGBOT_API_KEY").ok(),
                session: session_id,
                sender,
                message,
                stream,
                no_format,
                no_history,
            };
            cmd.run().await
        }
        Commands::Config { action } => handle_config(action, ctx).await,
        Commands::Version => {
            println!("{}", env!("OPENVIKING_CLI_VERSION"));
            Ok(())
        }
        Commands::Read { uri } => handle_read(uri, ctx).await,
        Commands::Abstract { uri } => handle_abstract(uri, ctx).await,
        Commands::Overview { uri } => handle_overview(uri, ctx).await,
        Commands::Reindex {
            uri,
            regenerate,
            wait,
        } => handle_reindex(uri, regenerate, wait, ctx).await,
        Commands::Get { uri, local_path } => handle_get(uri, local_path, ctx).await,
        Commands::Find {
            query,
            uri,
            node_limit,
            threshold,
        } => handle_find(query, uri, node_limit, threshold, ctx).await,
        Commands::Search {
            query,
            uri,
            session_id,
            node_limit,
            threshold,
        } => handle_search(query, uri, session_id, node_limit, threshold, ctx).await,
        Commands::Grep {
            uri,
            pattern,
            ignore_case,
            node_limit,
        } => handle_grep(uri, pattern, ignore_case, node_limit, ctx).await,

        Commands::Glob {
            pattern,
            uri,
            node_limit,
        } => handle_glob(pattern, uri, node_limit, ctx).await,
    };

    if let Err(e) = result {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

async fn handle_add_resource(
    mut path: String,
    to: Option<String>,
    parent: Option<String>,
    reason: String,
    instruction: String,
    wait: bool,
    timeout: Option<f64>,
    no_strict: bool,
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

    let strict = !no_strict;
    let directly_upload_media = !no_directly_upload_media;

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
        ignore_dirs,
        include,
        exclude,
        directly_upload_media,
        watch_interval,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

async fn handle_add_skill(
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

async fn handle_relations(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::list_relations(&client, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_link(
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

async fn handle_unlink(from_uri: String, to_uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::unlink(&client, &from_uri, &to_uri, ctx.output_format, ctx.compact).await
}

async fn handle_export(uri: String, to: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::export(&client, &uri, &to, ctx.output_format, ctx.compact).await
}

async fn handle_import(
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

async fn handle_system(cmd: SystemCommands, ctx: CliContext) -> Result<()> {
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

async fn handle_observer(cmd: ObserverCommands, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    match cmd {
        ObserverCommands::Queue => {
            commands::observer::queue(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::Vikingdb => {
            commands::observer::vikingdb(&client, ctx.output_format, ctx.compact).await
        }
        ObserverCommands::Vlm => {
            commands::observer::vlm(&client, ctx.output_format, ctx.compact).await
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

async fn handle_session(cmd: SessionCommands, ctx: CliContext) -> Result<()> {
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

async fn handle_admin(cmd: AdminCommands, ctx: CliContext) -> Result<()> {
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

async fn handle_add_memory(content: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::session::add_memory(&client, &content, ctx.output_format, ctx.compact).await
}

async fn handle_config(cmd: ConfigCommands, _ctx: CliContext) -> Result<()> {
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

async fn handle_read(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::read(&client, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_abstract(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::abstract_content(&client, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_overview(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::overview(&client, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_reindex(uri: String, regenerate: bool, wait: bool, ctx: CliContext) -> Result<()> {
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

async fn handle_get(uri: String, local_path: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::content::get(&client, &uri, &local_path).await
}

async fn handle_find(
    query: String,
    uri: String,
    node_limit: i32,
    threshold: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![format!("--uri={}", uri), format!("-n {}", node_limit)];
    if let Some(t) = threshold {
        params.push(format!("--threshold {}", t));
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
        ctx.output_format,
        ctx.compact,
    )
    .await
}

async fn handle_search(
    query: String,
    uri: String,
    session_id: Option<String>,
    node_limit: i32,
    threshold: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![format!("--uri={}", uri), format!("-n {}", node_limit)];
    if let Some(s) = &session_id {
        params.push(format!("--session-id {}", s));
    }
    if let Some(t) = threshold {
        params.push(format!("--threshold {}", t));
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
        ctx.output_format,
        ctx.compact,
    )
    .await
}

/// Print command with specified parameters for debugging
fn print_command_echo(command: &str, params: &str, echo_enabled: bool) {
    if echo_enabled {
        println!("cmd: {} {}", command, params);
    }
}

async fn handle_ls(
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

async fn handle_tree(
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

async fn handle_mkdir(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::mkdir(&client, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_rm(uri: String, recursive: bool, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::rm(&client, &uri, recursive, ctx.output_format, ctx.compact).await
}

async fn handle_mv(from_uri: String, to_uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::mv(&client, &from_uri, &to_uri, ctx.output_format, ctx.compact).await
}

async fn handle_stat(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::filesystem::stat(&client, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_grep(
    uri: String,
    pattern: String,
    ignore_case: bool,
    node_limit: i32,
    ctx: CliContext,
) -> Result<()> {
    let mut params = vec![format!("--uri={}", uri), format!("-n {}", node_limit)];
    if ignore_case {
        params.push("-i".to_string());
    }
    params.push(format!("\"{}\"", pattern));
    print_command_echo("ov grep", &params.join(" "), ctx.config.echo_command);
    let client = ctx.get_client();
    commands::search::grep(
        &client,
        &uri,
        &pattern,
        ignore_case,
        node_limit,
        ctx.output_format,
        ctx.compact,
    )
    .await
}

async fn handle_glob(pattern: String, uri: String, node_limit: i32, ctx: CliContext) -> Result<()> {
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

async fn handle_health(ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();

    // Reuse the system health command
    let _ = commands::system::health(&client, ctx.output_format, ctx.compact).await?;

    Ok(())
}

async fn handle_tui(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    tui::run_tui(client, &uri).await
}

#[cfg(test)]
mod tests {
    use super::{Cli, CliContext};
    use crate::config::Config;
    use crate::output::OutputFormat;
    use clap::Parser;

    #[test]
    fn cli_parses_global_identity_override_flags() {
        let cli = Cli::try_parse_from([
            "ov",
            "--account",
            "acme",
            "--user",
            "alice",
            "--agent-id",
            "assistant-1",
            "ls",
        ])
        .expect("cli should parse");

        assert_eq!(cli.account.as_deref(), Some("acme"));
        assert_eq!(cli.user.as_deref(), Some("alice"));
        assert_eq!(cli.agent_id.as_deref(), Some("assistant-1"));
    }

    #[test]
    fn cli_context_overrides_identity_from_cli_flags() {
        let config = Config {
            url: "http://localhost:1933".to_string(),
            api_key: Some("test-key".to_string()),
            account: Some("from-config-account".to_string()),
            user: Some("from-config-user".to_string()),
            agent_id: Some("from-config-agent".to_string()),
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
        };

        let ctx = CliContext::from_config(
            config,
            OutputFormat::Json,
            true,
            Some("from-cli-account".to_string()),
            Some("from-cli-user".to_string()),
            Some("from-cli-agent".to_string()),
        );

        assert_eq!(ctx.config.account.as_deref(), Some("from-cli-account"));
        assert_eq!(ctx.config.user.as_deref(), Some("from-cli-user"));
        assert_eq!(ctx.config.agent_id.as_deref(), Some("from-cli-agent"));
    }
}
