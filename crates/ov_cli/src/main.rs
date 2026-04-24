mod client;
mod commands;
mod config;
mod error;
mod handlers;
mod output;
mod tui;
mod utils;

use clap::{ArgAction, Parser, Subcommand};
use config::Config;
use error::Result;
use output::OutputFormat;

/// CLI context shared across commands
#[derive(Debug, Clone)]
pub struct CliContext {
    pub config: Config,
    pub output_format: OutputFormat,
    pub compact: bool,
    pub sudo: bool,
}

impl CliContext {
    pub fn new(
        output_format: OutputFormat,
        compact: bool,
        account: Option<String>,
        user: Option<String>,
        agent_id: Option<String>,
        sudo: bool,
    ) -> Result<Self> {
        let config = Config::load()?;
        Ok(Self::from_config(
            config,
            output_format,
            compact,
            account,
            user,
            agent_id,
            sudo,
        ))
    }

    fn from_config(
        mut config: Config,
        output_format: OutputFormat,
        compact: bool,
        account: Option<String>,
        user: Option<String>,
        agent_id: Option<String>,
        sudo: bool,
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
            sudo,
        }
    }

    pub fn get_client(&self) -> client::HttpClient {
        self.get_client_with_timeout(None)
    }

    pub fn get_client_with_timeout(&self, timeout_secs: Option<f64>) -> client::HttpClient {
        let api_key = if self.sudo {
            self.config.root_api_key.clone()
        } else {
            self.config.api_key.clone()
        };
        client::HttpClient::new(
            &self.config.url,
            api_key,
            self.config.agent_id.clone(),
            self.config.account.clone(),
            self.config.user.clone(),
            timeout_secs.unwrap_or(self.config.timeout),
            self.config.extra_headers.clone(),
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

    /// Use root API key for admin privileges
    #[arg(long, global = true)]
    sudo: bool,

    #[command(subcommand)]
    command: Commands,
}

// Commands are organized with category tags in their doc comments.
//
// # Command Tagging System
//
// Tags are added at the beginning of command doc comments, e.g.:
// - `[Data]` - Data operations category
// - `[Interactive]` - Interactive tools category
// - `[Status]` - Status & observability category
// - `[Admin]` - Admin tools category
// - `[Experimental]` - Experimental/preview features (API may change)
//
// Some tags can be combined, e.g. `[Experimental][Data]`
#[derive(Subcommand)]
enum Commands {
    // --- Data Operations ---
    /// [Data] Add resources into OpenViking
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
        /// Enable strict mode for directory scanning (fail if any unsupported files found)
        #[arg(long = "strict", action = ArgAction::SetTrue)]
        strict_mode: bool,
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
    /// [Data] Add a skill into OpenViking
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
    /// [Data] List directory contents
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
    /// [Data] Get directory tree
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
    /// [Data] Create directory
    Mkdir {
        /// Directory URI to create
        uri: String,
        /// Initial directory description
        #[arg(long)]
        description: Option<String>,
    },
    /// [Data] Remove resource
    #[command(alias = "del", alias = "delete")]
    Rm {
        /// Viking URI to remove
        uri: String,
        /// Remove recursively
        #[arg(short, long)]
        recursive: bool,
    },
    /// [Data] Move or rename resource
    #[command(alias = "rename")]
    Mv {
        /// Source URI
        from_uri: String,
        /// Target URI
        to_uri: String,
    },
    /// [Data] Get resource metadata
    Stat {
        /// Viking URI to get metadata for
        uri: String,
    },
    /// [Data] Read file content (L2)
    Read {
        /// Viking URI
        uri: String,
    },
    /// [Data] Read abstract content (L0)
    Abstract {
        /// Directory URI
        uri: String,
    },
    /// [Data] Read overview content (L1)
    Overview {
        /// Directory URI
        uri: String,
    },
    /// [Data] Write text content to an existing file
    Write {
        /// Viking URI
        uri: String,
        /// Content to write
        #[arg(long, conflicts_with = "from_file")]
        content: Option<String>,
        /// Read content from a local file
        #[arg(long = "from-file", conflicts_with = "content")]
        from_file: Option<String>,
        /// Append instead of replacing the file
        #[arg(long)]
        append: bool,
        /// Write mode: replace, append, or create (default: replace)
        #[arg(long, value_name = "MODE", conflicts_with = "append")]
        mode: Option<String>,
        /// Wait for async processing to finish
        #[arg(long, default_value = "false")]
        wait: bool,
        /// Optional wait timeout in seconds
        #[arg(long)]
        timeout: Option<f64>,
    },
    /// [Data] Download file to local path (supports binaries/images)
    Get {
        /// Viking URI
        uri: String,
        /// Local path (must not exist yet)
        local_path: String,
    },
    /// [Data] Run semantic retrieval
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
        /// Only include results on or after this time (e.g. 48h, 7d, 2026-03-10, ISO-8601)
        #[arg(long = "after")]
        after: Option<String>,
        /// Only include results on or before this time (e.g. 24h, 2026-03-15, ISO-8601)
        #[arg(long = "before")]
        before: Option<String>,
    },
    /// [Experimental][Data] Run context-aware retrieval
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
        /// Only include results on or after this time (e.g. 48h, 7d, 2026-03-10, ISO-8601)
        #[arg(long = "after")]
        after: Option<String>,
        /// Only include results on or before this time (e.g. 24h, 2026-03-15, ISO-8601)
        #[arg(long = "before")]
        before: Option<String>,
    },
    /// [Data] Run content pattern search
    Grep {
        /// Target URI
        #[arg(short, long, default_value = "viking://")]
        uri: String,
        /// Excluded URI range. Any entry whose URI falls under this URI prefix is skipped
        #[arg(short = 'x', long = "exclude-uri")]
        exclude_uri: Option<String>,
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
        /// Maximum depth level to traverse (default: 10)
        #[arg(short = 'L', long = "level-limit", default_value = "10")]
        level_limit: i32,
    },
    /// [Data] Run file glob pattern search
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
    /// [Data] Session management commands
    Session {
        #[command(subcommand)]
        action: SessionCommands,
    },
    /// [Experimental][Data] Add memory in one shot (creates session, adds messages, commits)
    AddMemory {
        /// Content to memorize. Plain string (treated as user message),
        /// JSON {"role":"...","content":"..."} for a single message,
        /// or JSON array of such objects for multiple messages.
        content: String,
    },
    /// [Experimental][Data] List relations of a resource
    Relations {
        /// Viking URI
        uri: String,
    },
    /// [Experimental][Data] Create relation links from one URI to one or more targets
    Link {
        /// Source URI
        from_uri: String,
        /// One or more target URIs
        to_uris: Vec<String>,
        /// Reason for linking
        #[arg(long, default_value = "")]
        reason: String,
    },
    /// [Experimental][Data] Remove a relation link
    Unlink {
        /// Source URI
        from_uri: String,
        /// Target URI to unlink
        to_uri: String,
    },
    /// [Data] Export context as .ovpack
    Export {
        /// Source URI
        uri: String,
        /// Output .ovpack file path
        to: String,
    },
    /// [Data] Import .ovpack into target URI
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
    // --- Interactive Tools ---
    /// [Interactive] Interactive TUI file explorer
    Tui {
        /// Viking URI to start browsing (default: /)
        #[arg(default_value = "/")]
        uri: String,
    },
    /// [Interactive] Chat with vikingbot agent
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

    // --- Status & Observability ---
    /// [Status] Wait for queued async processing to complete
    Wait {
        /// Wait timeout in seconds
        #[arg(long)]
        timeout: Option<f64>,
    },
    /// [Status] All OpenViking Server components status
    Status,
    /// [Status] Observe OpenViking Server components status
    Observer {
        #[command(subcommand)]
        action: ObserverCommands,
    },
    /// [Status] Quick health check
    Health,
    /// [Status] Configuration management
    Config {
        #[command(subcommand)]
        action: ConfigCommands,
    },
    /// [Status] Show CLI version
    Version,

    // --- Admin Tools ---
    /// [Admin] Account and user management commands (multi-tenant)
    Admin {
        #[command(subcommand)]
        action: AdminCommands,
    },
    /// [Admin] System utility commands
    System {
        #[command(subcommand)]
        action: SystemCommands,
    },
    /// [Admin] Reindex content at URI (regenerates .abstract.md and .overview.md)
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
}

impl Commands {
    /// Returns true if this is an admin command that supports --sudo
    fn is_admin_command(&self) -> bool {
        match self {
            Self::Admin { .. }
            | Self::System { .. }
            | Self::Reindex { .. } => true,
            _ => false,
        }
    }
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
    /// Get models status (VLM, Embedding, Rerank)
    Models,
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
    /// Get full merged session context
    GetSessionContext {
        /// Session ID
        session_id: String,
        /// Token budget for latest archive overview inclusion
        #[arg(long = "token-budget", default_value = "128000")]
        token_budget: i32,
    },
    /// Get one completed archive for a session
    GetSessionArchive {
        /// Session ID
        session_id: String,
        /// Archive ID
        archive_id: String,
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
        /// Maximum number of users to list (default: 100)
        #[arg(long, default_value = "100")]
        limit: u32,
        /// Filter users by name (supports wildcard * and ?)
        #[arg(long)]
        name: Option<String>,
        /// Filter users by role
        #[arg(long)]
        role: Option<String>,
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
        cli.sudo,
    ) {
        Ok(ctx) => ctx,
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(2);
        }
    };

    // Check if --sudo is used but root_api_key is not configured
    if ctx.sudo && ctx.config.root_api_key.is_none() {
        eprintln!("Error: --sudo requires root_api_key to be configured in ~/.openviking/ovcli.conf");
        std::process::exit(2);
    }

    // Check if --sudo is used with non-admin command
    if ctx.sudo && !cli.command.is_admin_command() {
        eprintln!("Error: --sudo is only supported for admin commands (admin, system, reindex)");
        std::process::exit(2);
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
            strict_mode,
            ignore_dirs,
            include,
            exclude,
            no_directly_upload_media,
            watch_interval,
        } => {
            handlers::handle_add_resource(
                path,
                to,
                parent,
                reason,
                instruction,
                wait,
                timeout,
                strict_mode,
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
        } => handlers::handle_add_skill(data, wait, timeout, ctx).await,
        Commands::Relations { uri } => handlers::handle_relations(uri, ctx).await,
        Commands::Link {
            from_uri,
            to_uris,
            reason,
        } => handlers::handle_link(from_uri, to_uris, reason, ctx).await,
        Commands::Unlink { from_uri, to_uri } => handlers::handle_unlink(from_uri, to_uri, ctx).await,
        Commands::Export { uri, to } => handlers::handle_export(uri, to, ctx).await,
        Commands::Import {
            file_path,
            target_uri,
            force,
            no_vectorize,
        } => handlers::handle_import(file_path, target_uri, force, no_vectorize, ctx).await,
        Commands::Wait { timeout } => {
            let client = ctx.get_client();
            commands::system::wait(&client, timeout, ctx.output_format, ctx.compact).await
        }
        Commands::Status => {
            let client = ctx.get_client();
            commands::observer::system(&client, ctx.output_format, ctx.compact).await
        }
        Commands::Health => handlers::handle_health(ctx).await,
        Commands::System { action } => handlers::handle_system(action, ctx).await,
        Commands::Observer { action } => handlers::handle_observer(action, ctx).await,
        Commands::Session { action } => handlers::handle_session(action, ctx).await,
        Commands::Admin { action } => handlers::handle_admin(action, ctx).await,
        Commands::Ls {
            uri,
            simple,
            recursive,
            abs_limit,
            all,
            node_limit,
        } => handlers::handle_ls(uri, simple, recursive, abs_limit, all, node_limit, ctx).await,
        Commands::Tree {
            uri,
            abs_limit,
            all,
            node_limit,
            level_limit,
        } => handlers::handle_tree(uri, abs_limit, all, node_limit, level_limit, ctx).await,
        Commands::Mkdir { uri, description } => handlers::handle_mkdir(uri, description, ctx).await,
        Commands::Rm { uri, recursive } => handlers::handle_rm(uri, recursive, ctx).await,
        Commands::Mv { from_uri, to_uri } => handlers::handle_mv(from_uri, to_uri, ctx).await,
        Commands::Stat { uri } => handlers::handle_stat(uri, ctx).await,
        Commands::AddMemory { content } => handlers::handle_add_memory(content, ctx).await,
        Commands::Tui { uri } => handlers::handle_tui(uri, ctx).await,
        Commands::Chat {
            message,
            session,
            sender,
            stream,
            no_format,
            no_history,
        } => {
            let session_id = session.or_else(|| config::get_or_create_machine_id().ok());
            let endpoint = if let Ok(env_endpoint) = std::env::var("VIKINGBOT_ENDPOINT") {
                env_endpoint
            } else if let Ok(config_url) = std::env::var("OPENVIKING_URL") {
                format!("{}/bot/v1", config_url)
            } else {
                format!("{}/bot/v1", ctx.config.url)
            };
            let api_key = std::env::var("VIKINGBOT_API_KEY")
                .ok()
                .or_else(|| ctx.config.api_key.clone());
            let cmd = commands::chat::ChatCommand {
                endpoint,
                api_key,
                account: ctx.config.account.clone(),
                user: ctx.config.user.clone(),
                session: session_id,
                sender,
                message,
                stream,
                no_format,
                no_history,
            };
            cmd.run().await
        }
        Commands::Config { action } => handlers::handle_config(action, ctx).await,
        Commands::Version => {
            println!("CLI:     {}", env!("OPENVIKING_CLI_VERSION"));

            // Try to get server version from /health endpoint with a short timeout (3 seconds)
            let client = ctx.get_client_with_timeout(Some(3.0));
            match client.get::<serde_json::Value>("/health", &[]).await {
                Ok(health) => {
                    if let Some(version) = health.get("version").and_then(|v| v.as_str()) {
                        println!("Server:  {}", version);
                    }
                }
                Err(_) => {
                    // If can't connect to server, just don't print server version
                }
            }
            Ok(())
        }
        Commands::Read { uri } => handlers::handle_read(uri, ctx).await,
        Commands::Abstract { uri } => handlers::handle_abstract(uri, ctx).await,
        Commands::Overview { uri } => handlers::handle_overview(uri, ctx).await,
        Commands::Write {
            uri,
            content,
            from_file,
            append,
            mode,
            wait,
            timeout,
        } => {
            let effective_mode = if let Some(m) = mode {
                m
            } else if append {
                "append".to_string()
            } else {
                "replace".to_string()
            };
            handlers::handle_write(uri, content, from_file, effective_mode, wait, timeout, ctx).await
        }
        Commands::Reindex {
            uri,
            regenerate,
            wait,
        } => handlers::handle_reindex(uri, regenerate, wait, ctx).await,
        Commands::Get { uri, local_path } => handlers::handle_get(uri, local_path, ctx).await,
        Commands::Find {
            query,
            uri,
            node_limit,
            threshold,
            after,
            before,
        } => handlers::handle_find(query, uri, node_limit, threshold, after, before, ctx).await,
        Commands::Search {
            query,
            uri,
            session_id,
            node_limit,
            threshold,
            after,
            before,
        } => {
            handlers::handle_search(
                query, uri, session_id, node_limit, threshold, after, before, ctx,
            )
            .await
        }
        Commands::Grep {
            uri,
            exclude_uri,
            pattern,
            ignore_case,
            node_limit,
            level_limit,
        } => {
            handlers::handle_grep(
                uri,
                exclude_uri,
                pattern,
                ignore_case,
                node_limit,
                level_limit,
                ctx,
            )
            .await
        }

        Commands::Glob {
            pattern,
            uri,
            node_limit,
        } => handlers::handle_glob(pattern, uri, node_limit, ctx).await,
    };

    if let Err(e) = result {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::{Cli, CliContext};
    use crate::config::Config;
    use crate::handlers;
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
            root_api_key: None,
            account: Some("from-config-account".to_string()),
            user: Some("from-config-user".to_string()),
            agent_id: Some("from-config-agent".to_string()),
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
            upload: Default::default(),
            extra_headers: None,
        };

        let ctx = CliContext::from_config(
            config,
            OutputFormat::Json,
            true,
            Some("from-cli-account".to_string()),
            Some("from-cli-user".to_string()),
            Some("from-cli-agent".to_string()),
            false,
        );

        assert_eq!(ctx.config.account.as_deref(), Some("from-cli-account"));
        assert_eq!(ctx.config.user.as_deref(), Some("from-cli-user"));
        assert_eq!(ctx.config.agent_id.as_deref(), Some("from-cli-agent"));
    }

    #[test]
    fn cli_context_uses_root_api_key_with_sudo() {
        let config = Config {
            url: "http://localhost:1933".to_string(),
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: None,
            user: None,
            agent_id: None,
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
            upload: Default::default(),
            extra_headers: None,
        };

        // Without sudo: use api_key
        let ctx = CliContext::from_config(
            config.clone(),
            OutputFormat::Json,
            true,
            None,
            None,
            None,
            false,
        );
        let client = ctx.get_client();
        assert_eq!(client.api_key(), Some("user-key"));

        // With sudo: use root_api_key
        let ctx = CliContext::from_config(
            config,
            OutputFormat::Json,
            true,
            None,
            None,
            None,
            true,
        );
        let client = ctx.get_client();
        assert_eq!(client.api_key(), Some("root-key"));
    }

    #[test]
    fn cli_write_rejects_removed_semantic_flags() {
        let result = Cli::try_parse_from([
            "ov",
            "write",
            "viking://resources/demo.md",
            "--content",
            "updated",
            "--no-semantics",
            "--no-vectorize",
        ]);

        assert!(result.is_err(), "removed write flags should not parse");
    }

    #[test]
    fn append_time_filter_params_only_emits_after_and_before() {
        let mut params = Vec::new();
        let after = Some("7d".to_string());
        let before = Some("2026-03-12".to_string());

        handlers::append_time_filter_params(&mut params, after.as_deref(), before.as_deref());

        assert_eq!(params, vec!["--after 7d", "--before 2026-03-12"]);
    }
}
