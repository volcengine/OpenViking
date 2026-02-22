mod client;
mod commands;
mod config;
mod error;
mod output;
mod tui;

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
    pub fn new(output_format: OutputFormat, compact: bool) -> Result<Self> {
        let config = Config::load()?;
        Ok(Self {
            config,
            output_format,
            compact,
        })
    }

    pub fn get_client(&self) -> client::HttpClient {
        client::HttpClient::new(&self.config.url, self.config.api_key.clone())
    }
}

#[derive(Parser)]
#[command(name = "openviking")]
#[command(about = "OpenViking - An Agent-native context database")]
#[command(version = env!("CARGO_PKG_VERSION"))]
#[command(arg_required_else_help = true)]
struct Cli {
    /// Output format
    #[arg(short, long, value_enum, default_value = "table", global = true)]
    output: OutputFormat,

    /// Compact representation, defaults to true - compacts JSON output or uses simplified representation for Table output
    #[arg(short, long, global = true, default_value = "true")]
    compact: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Add resources into OpenViking
    AddResource {
        /// Local path or URL to import
        path: String,
        /// Target URI
        #[arg(long)]
        to: Option<String>,
        /// Reason for import
        #[arg(long, default_value = "")]
        reason: String,
        /// Additional instruction
        #[arg(long, default_value = "")]
        instruction: String,
        /// Wait until processing is complete
        #[arg(long)]
        wait: bool,
        /// Wait timeout in seconds
        #[arg(long)]
        timeout: Option<f64>,
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
        #[arg(long = "node-limit", short = 'n', default_value = "1000")]
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
        #[arg(long = "node-limit", short = 'n', default_value = "1000")]
        node_limit: i32,
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
    /// Run semantic retrieval
    Find {
        /// Search query
        query: String,
        /// Target URI
        #[arg(short, long, default_value = "")]
        uri: String,
        /// Maximum number of results
        #[arg(short = 'n', long, default_value = "10")]
        limit: i32,
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
        #[arg(short = 'n', long, default_value = "10")]
        limit: i32,
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
    },
    /// Run file glob pattern search
    Glob {
        /// Glob pattern
        pattern: String,
        /// Search root URI
        #[arg(short, long, default_value = "viking://")]
        uri: String,
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
        /// Viking URI to start browsing (default: viking://)
        #[arg(default_value = "viking://")]
        uri: String,
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
}

#[derive(Subcommand)]
enum ObserverCommands {
    /// Get queue status
    Queue,
    /// Get VikingDB status
    Vikingdb,
    /// Get VLM status
    Vlm,
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

    let ctx = match CliContext::new(output_format, compact) {
        Ok(ctx) => ctx,
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(2);
        }
    };

    let result = match cli.command {
        Commands::AddResource { path, to, reason, instruction, wait, timeout } => {
            handle_add_resource(path, to, reason, instruction, wait, timeout, ctx).await
        }
        Commands::AddSkill { data, wait, timeout } => {
            handle_add_skill(data, wait, timeout, ctx).await
        }
        Commands::Relations { uri } => {
            handle_relations(uri, ctx).await
        }
        Commands::Link { from_uri, to_uris, reason } => {
            handle_link(from_uri, to_uris, reason, ctx).await
        }
        Commands::Unlink { from_uri, to_uri } => {
            handle_unlink(from_uri, to_uri, ctx).await
        }
        Commands::Export { uri, to } => {
            handle_export(uri, to, ctx).await
        }
        Commands::Import { file_path, target_uri, force, no_vectorize } => {
            handle_import(file_path, target_uri, force, no_vectorize, ctx).await
        }
        Commands::Wait { timeout } => {
            let client = ctx.get_client();
            commands::system::wait(&client, timeout, ctx.output_format, ctx.compact).await
        },
        Commands::Status => {
            let client = ctx.get_client();
            commands::observer::system(&client, ctx.output_format, ctx.compact).await
        },
        Commands::Health => handle_health(ctx).await,
        Commands::System { action } => handle_system(action, ctx).await,
        Commands::Observer { action } => handle_observer(action, ctx).await,
        Commands::Session { action } => handle_session(action, ctx).await,
        Commands::Ls { uri, simple, recursive, abs_limit, all, node_limit } => {
            handle_ls(uri, simple, recursive, abs_limit, all, node_limit, ctx).await
        }
        Commands::Tree { uri, abs_limit, all, node_limit } => {
            handle_tree(uri, abs_limit, all, node_limit, ctx).await
        }
        Commands::Mkdir { uri } => {
            handle_mkdir(uri, ctx).await
        }
        Commands::Rm { uri, recursive } => {
            handle_rm(uri, recursive, ctx).await
        }
        Commands::Mv { from_uri, to_uri } => {
            handle_mv(from_uri, to_uri, ctx).await
        }
        Commands::Stat { uri } => {
            handle_stat(uri, ctx).await
        }
        Commands::AddMemory { content } => {
            handle_add_memory(content, ctx).await
        }
        Commands::Tui { uri } => {
            handle_tui(uri, ctx).await
        }
        Commands::Config { action } => handle_config(action, ctx).await,
        Commands::Version => {
            println!("{}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Commands::Read { uri } => handle_read(uri, ctx).await,
        Commands::Abstract { uri } => handle_abstract(uri, ctx).await,
        Commands::Overview { uri } => handle_overview(uri, ctx).await,
        Commands::Find { query, uri, limit, threshold } => {
            handle_find(query, uri, limit, threshold, ctx).await
        }
        Commands::Search { query, uri, session_id, limit, threshold } => {
            handle_search(query, uri, session_id, limit, threshold, ctx).await
        }
        Commands::Grep { uri, pattern, ignore_case } => {
            handle_grep(uri, pattern, ignore_case, ctx).await
        }
        Commands::Glob { pattern, uri } => {
            handle_glob(pattern, uri, ctx).await
        }
    };

    if let Err(e) = result {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

async fn handle_add_resource(
    mut path: String,
    to: Option<String>,
    reason: String,
    instruction: String,
    wait: bool,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    // Validate path: if it's a local path, check if it exists
    if !path.starts_with("http://") && !path.starts_with("https://") {
        use std::path::Path;
        
        // Unescape path: replace backslash followed by space with just space
        let unescaped_path = path.replace("\\ ", " ");
        let path_obj = Path::new(&unescaped_path);
        if !path_obj.exists() {
            eprintln!("Error: Path '{}' does not exist.", path);
            
            // Check if there might be unquoted spaces
            use std::env;
            let args: Vec<String> = env::args().collect();
            
            if let Some(add_resource_pos) = args.iter().position(|s| s == "add-resource" || s == "add") {
                if args.len() > add_resource_pos + 2 {
                    let extra_args = &args[add_resource_pos + 2..];
                    let suggested_path = format!("{} {}", path, extra_args.join(" "));
                    eprintln!("\nIt looks like you may have forgotten to quote a path with spaces.");
                    eprintln!("Suggested command: ov add-resource \"{}\"", suggested_path);
                }
            }
            
            std::process::exit(1);
        }
        path = unescaped_path;
    }
    
    let client = ctx.get_client();
    commands::resources::add_resource(
        &client, &path, to, reason, instruction, wait, timeout, ctx.output_format, ctx.compact
    ).await
}

async fn handle_add_skill(
    data: String,
    wait: bool,
    timeout: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::resources::add_skill(
        &client, &data, wait, timeout, ctx.output_format, ctx.compact
    ).await
}

async fn handle_relations(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::list_relations(&client, &uri, ctx.output_format, ctx.compact
    ).await
}

async fn handle_link(
    from_uri: String,
    to_uris: Vec<String>,
    reason: String,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::link(
        &client, &from_uri, &to_uris, &reason, ctx.output_format, ctx.compact
    ).await
}

async fn handle_unlink(
    from_uri: String,
    to_uri: String,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::relations::unlink(
        &client, &from_uri, &to_uri, ctx.output_format, ctx.compact
    ).await
}

async fn handle_export(uri: String, to: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::pack::export(&client, &uri, &to, ctx.output_format, ctx.compact
    ).await
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
        &client, &file_path, &target_uri, force, no_vectorize, ctx.output_format, ctx.compact
    ).await
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
            commands::system::health(&client, ctx.output_format, ctx.compact).await
        }
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
            commands::session::get_session(&client, &session_id, ctx.output_format, ctx.compact
            ).await
        }
        SessionCommands::Delete { session_id } => {
            commands::session::delete_session(&client, &session_id, ctx.output_format, ctx.compact
            ).await
        }
        SessionCommands::AddMessage { session_id, role, content } => {
            commands::session::add_message(
                &client, &session_id, &role, &content, ctx.output_format, ctx.compact
            ).await
        }
        SessionCommands::Commit { session_id } => {
            commands::session::commit_session(&client, &session_id, ctx.output_format, ctx.compact
            ).await
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
                true
            );
            Ok(())
        }
        ConfigCommands::Validate => {
            match Config::load() {
                Ok(_) => {
                    println!("Configuration is valid");
                    Ok(())
                }
                Err(e) => {
                    Err(Error::Config(e.to_string()))
                }
            }
        }
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

async fn handle_find(
    query: String,
    uri: String,
    limit: i32,
    threshold: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::search::find(&client, &query, &uri, limit, threshold, ctx.output_format, ctx.compact).await
}

async fn handle_search(
    query: String,
    uri: String,
    session_id: Option<String>,
    limit: i32,
    threshold: Option<f64>,
    ctx: CliContext,
) -> Result<()> {
    let client = ctx.get_client();
    commands::search::search(&client, &query, &uri, session_id, limit, threshold, ctx.output_format, ctx.compact).await
}

async fn handle_ls(uri: String, simple: bool, recursive: bool, abs_limit: i32, show_all_hidden: bool, node_limit: i32, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    let api_output = if ctx.compact { "agent" } else { "original" };
    commands::filesystem::ls(&client, &uri, simple, recursive, api_output, abs_limit, show_all_hidden, node_limit, ctx.output_format, ctx.compact).await
}

async fn handle_tree(uri: String, abs_limit: i32, show_all_hidden: bool, node_limit: i32, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    let api_output = if ctx.compact { "agent" } else { "original" };
    commands::filesystem::tree(&client, &uri, api_output, abs_limit, show_all_hidden, node_limit, ctx.output_format, ctx.compact).await
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

async fn handle_grep(uri: String, pattern: String, ignore_case: bool, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::search::grep(&client, &uri, &pattern, ignore_case, ctx.output_format, ctx.compact).await
}

async fn handle_glob(pattern: String, uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    commands::search::glob(&client, &pattern, &uri, ctx.output_format, ctx.compact).await
}

async fn handle_health(ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    let system_status: serde_json::Value = client.get("/api/v1/observer/system", &[]).await?;
    let is_healthy = system_status.get("is_healthy").and_then(|v| v.as_bool()).unwrap_or(false);
    output::output_success(&serde_json::json!({ "healthy": is_healthy }), ctx.output_format, ctx.compact);
    if !is_healthy {
        std::process::exit(1);
    }
    Ok(())
}

async fn handle_tui(uri: String, ctx: CliContext) -> Result<()> {
    let client = ctx.get_client();
    tui::run_tui(client, &uri).await
}
