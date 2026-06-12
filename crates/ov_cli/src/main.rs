mod base_client;
mod client;
mod commands;
mod config;
mod config_agent;
mod config_command_ui;
mod config_wizard;
mod error;
mod error_classifier;
mod error_ui;
mod handlers;
mod health_ui;
mod help_ui;
mod i18n;
mod output;
mod status_ui;
mod theme;
mod tui;
mod utils;

use clap::{ArgAction, Args, Parser, Subcommand};
use colored::Colorize;
use config::Config;
use error::{Error, Result};
use output::OutputFormat;
use std::{
    ffi::OsString,
    io::{self, IsTerminal},
};

/// CLI context shared across commands
#[derive(Debug, Clone)]
pub struct CliContext {
    pub config: Config,
    pub output_format: OutputFormat,
    pub compact: bool,
    pub sudo: bool,
    /// Whether to show upload progress (override config)
    pub show_progress: Option<bool>,
    /// Whether to enable verbose output (override config)
    pub verbose: Option<bool>,
    pub profile: Option<bool>,
}

impl CliContext {
    fn from_config(
        mut config: Config,
        output_format: OutputFormat,
        compact: bool,
        account: Option<String>,
        user: Option<String>,
        sudo: bool,
        show_progress: Option<bool>,
        verbose: Option<bool>,
        profile: Option<bool>,
    ) -> Self {
        if account.is_some() {
            config.account = account;
        }
        if user.is_some() {
            config.user = user;
        }
        Self {
            config,
            output_format,
            compact,
            sudo,
            show_progress,
            verbose,
            profile,
        }
    }

    /// Check if progress should be shown
    pub fn should_show_progress(&self) -> bool {
        self.show_progress.unwrap_or(self.config.show_progress)
    }

    /// Check if verbose output is enabled
    pub fn is_verbose(&self) -> bool {
        self.verbose.unwrap_or(self.config.verbose)
    }

    pub fn get_client(&self) -> client::HttpClient {
        self.get_client_with_timeout(None)
    }

    pub fn get_client_with_timeout(&self, timeout_secs: Option<f64>) -> client::HttpClient {
        let auth = self.config.effective_auth(self.sudo);
        client::HttpClient::new(
            &self.config.url,
            auth.api_key,
            auth.account,
            auth.user,
            timeout_secs.unwrap_or(self.config.timeout),
            self.profile.unwrap_or(self.config.profile),
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
    /// Choose human table output or machine-readable JSON
    #[arg(
        short,
        long,
        value_enum,
        default_value = "table",
        global = true,
        hide = true,
        value_name = "table|json"
    )]
    output: OutputFormat,

    /// Use compact table/JSON rendering
    #[arg(
        short,
        long,
        global = true,
        default_value = "true",
        hide = true,
        action = ArgAction::Set,
        value_name = "bool"
    )]
    compact: bool,

    /// Override X-OpenViking-Account for this command
    #[arg(long, global = true, hide = true)]
    account: Option<String>,

    /// Override X-OpenViking-User for this command
    #[arg(long, global = true, hide = true)]
    user: Option<String>,

    /// Use root API key for admin, system, and reindex commands
    #[arg(long, global = true, hide = true)]
    sudo: bool,

    /// Enable HTTP request profiling for this command
    #[arg(long, global = true, hide = true)]
    profile: bool,

    /// Show upload progress (legacy pre-command placement; prefer command-local --progress)
    #[arg(long, hide = true)]
    progress: bool,

    /// Disable upload progress (legacy pre-command placement; prefer command-local --no-progress)
    #[arg(long = "no-progress", hide = true, conflicts_with = "progress")]
    no_progress: bool,

    /// Enable upload diagnostics (legacy pre-command placement; prefer command-local --verbose)
    #[arg(short, long, hide = true)]
    verbose: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Args, Debug, Clone, Copy, Default)]
struct UploadCliOptions {
    /// Show local file upload progress (overrides config file)
    #[arg(
        long,
        conflicts_with = "no_progress",
        help_heading = "Advanced options"
    )]
    progress: bool,

    /// Disable local file upload progress (overrides config file)
    #[arg(
        long = "no-progress",
        conflicts_with = "progress",
        help_heading = "Advanced options"
    )]
    no_progress: bool,

    /// Print extra diagnostics during local file upload
    #[arg(short, long, help_heading = "Advanced options")]
    verbose: bool,
}

impl UploadCliOptions {
    fn is_set(self) -> bool {
        self.progress || self.no_progress || self.verbose
    }

    fn merged_with_legacy(self, legacy: Self) -> Self {
        Self {
            progress: self.progress || (!self.no_progress && legacy.progress),
            no_progress: self.no_progress || (!self.progress && legacy.no_progress),
            verbose: self.verbose || legacy.verbose,
        }
    }

    fn show_progress_override(self) -> Option<bool> {
        if self.progress {
            Some(true)
        } else if self.no_progress {
            Some(false)
        } else {
            None
        }
    }

    fn verbose_override(self) -> Option<bool> {
        if self.verbose { Some(true) } else { None }
    }
}

impl CliContext {
    fn with_upload_options(mut self, options: UploadCliOptions) -> Self {
        self.show_progress = options.show_progress_override();
        self.verbose = options.verbose_override();
        self
    }
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
        #[arg(value_name = "path-or-url")]
        path: String,
        /// Exact target URI (must not exist yet) (cannot be used with --parent)
        #[arg(long, value_name = "uri", help_heading = "Common options")]
        to: Option<String>,
        /// Target parent URI (must already exist and be a directory) (cannot be used with --to)
        #[arg(long, value_name = "uri", help_heading = "Common options")]
        parent: Option<String>,
        /// Target parent URI (create parent directory if it does not exist) (cannot be used with --to or --parent)
        #[arg(
            short = 'p',
            long = "parent-auto-create",
            value_name = "uri",
            help_heading = "Common options"
        )]
        parent_auto_create: Option<String>,
        /// Reason for import
        #[arg(
            long,
            default_value = "",
            value_name = "text",
            help_heading = "Advanced options"
        )]
        reason: String,
        /// Additional instruction
        #[arg(
            long,
            default_value = "",
            value_name = "text",
            help_heading = "Advanced options"
        )]
        instruction: String,
        /// Wait until processing is complete
        #[arg(long, help_heading = "Common options")]
        wait: bool,
        /// Wait timeout in seconds (only used with --wait)
        #[arg(long, value_name = "seconds", help_heading = "Common options")]
        timeout: Option<f64>,
        /// Enable strict mode for directory scanning (fail if any unsupported files found)
        #[arg(
            long = "strict",
            action = ArgAction::SetTrue,
            help_heading = "Advanced options"
        )]
        strict_mode: bool,
        /// Ignore directories, e.g. --ignore-dirs "node_modules,dist"
        #[arg(long, value_name = "dirs", help_heading = "Advanced options")]
        ignore_dirs: Option<String>,
        /// Include files extensions, e.g. --include "*.pdf,*.md"
        #[arg(long, value_name = "pattern", help_heading = "Common options")]
        include: Option<String>,
        /// Exclude files extensions, e.g. --exclude "*.tmp,*.log"
        #[arg(long, value_name = "pattern", help_heading = "Common options")]
        exclude: Option<String>,
        /// Do not directly upload media files
        #[arg(
            long = "no-directly-upload-media",
            default_value_t = false,
            help_heading = "Advanced options"
        )]
        no_directly_upload_media: bool,
        /// Watch interval in minutes for automatic resource monitoring (0 = no monitoring)
        #[arg(
            long,
            default_value = "0",
            value_name = "minutes",
            help_heading = "Advanced options"
        )]
        watch_interval: f64,
        #[command(flatten)]
        upload_options: UploadCliOptions,
    },
    /// [Data] Add a skill into OpenViking
    AddSkill {
        /// Skill directory, SKILL.md, or raw content
        #[arg(value_name = "skill-path-or-content")]
        data: String,
        /// Wait until processing is complete
        #[arg(long, help_heading = "Common options")]
        wait: bool,
        /// Wait timeout in seconds
        #[arg(long, value_name = "seconds", help_heading = "Common options")]
        timeout: Option<f64>,
        #[command(flatten)]
        upload_options: UploadCliOptions,
    },
    /// [Data] Manage installed skills
    Skills {
        #[command(subcommand)]
        action: SkillCommands,
    },
    /// [Data] List directory contents
    #[command(alias = "list")]
    Ls {
        /// Viking URI to list (default: viking://)
        #[arg(default_value = "viking://", value_name = "uri")]
        uri: String,
        /// Simple path output (just paths, no table)
        #[arg(short, long, help_heading = "Common options")]
        simple: bool,
        /// List all subdirectories recursively
        #[arg(short, long, help_heading = "Common options")]
        recursive: bool,
        /// Abstract content limit (only for agent output)
        #[arg(
            long = "abs-limit",
            short = 'l',
            default_value = "256",
            value_name = "n",
            help_heading = "Advanced options"
        )]
        abs_limit: i32,
        /// Show all hidden files
        #[arg(short, long, help_heading = "Common options")]
        all: bool,
        /// Maximum number of nodes to list
        #[arg(
            long = "node-limit",
            short = 'n',
            alias = "limit",
            default_value = "256",
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
    },
    /// [Data] Get directory tree
    Tree {
        /// Viking URI to get tree for
        #[arg(value_name = "uri")]
        uri: String,
        /// Abstract content limit (only for agent output)
        #[arg(
            long = "abs-limit",
            short = 'l',
            default_value = "128",
            value_name = "n",
            help_heading = "Advanced options"
        )]
        abs_limit: i32,
        /// Show all hidden files
        #[arg(short, long, help_heading = "Common options")]
        all: bool,
        /// Maximum number of nodes to list
        #[arg(
            long = "node-limit",
            short = 'n',
            alias = "limit",
            default_value = "256",
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Maximum depth level to traverse (default: 3)
        #[arg(
            short = 'L',
            long = "level-limit",
            default_value = "3",
            value_name = "n",
            help_heading = "Common options"
        )]
        level_limit: i32,
    },
    /// [Data] Create directory
    Mkdir {
        /// Directory URI to create
        #[arg(value_name = "uri")]
        uri: String,
        /// Initial directory description
        #[arg(long, value_name = "text", help_heading = "Common options")]
        description: Option<String>,
    },
    /// [Data] Remove resource
    #[command(alias = "del", alias = "delete")]
    Rm {
        /// Viking URI to remove
        #[arg(value_name = "uri")]
        uri: String,
        /// Remove recursively
        #[arg(short, long, help_heading = "Common options")]
        recursive: bool,
    },
    /// [Data] Move or rename resource
    #[command(alias = "rename")]
    Mv {
        /// Source URI
        #[arg(value_name = "from-uri")]
        from_uri: String,
        /// Target URI
        #[arg(value_name = "to-uri")]
        to_uri: String,
    },
    /// [Data] Get resource metadata
    Stat {
        /// Viking URI to get metadata for
        #[arg(value_name = "uri")]
        uri: String,
    },
    /// [Data] Read file content (Level 2)
    Read {
        /// Viking URI
        #[arg(value_name = "uri")]
        uri: String,
    },
    /// [Data] Read abstract content (Level 0)
    Abstract {
        /// Directory URI
        #[arg(value_name = "directory-uri")]
        uri: String,
    },
    /// [Data] Read overview content (Level 1)
    Overview {
        /// Directory URI
        #[arg(value_name = "directory-uri")]
        uri: String,
    },
    /// [Data] Write text content to an existing file
    Write {
        /// Viking URI
        #[arg(value_name = "uri")]
        uri: String,
        /// Content to write
        #[arg(
            long,
            conflicts_with = "from_file",
            value_name = "text",
            help_heading = "Common options"
        )]
        content: Option<String>,
        /// Read content from a local file
        #[arg(
            long = "from-file",
            conflicts_with = "content",
            value_name = "path",
            help_heading = "Common options"
        )]
        from_file: Option<String>,
        /// Append instead of replacing the file
        #[arg(long, help_heading = "Common options")]
        append: bool,
        /// Write mode: replace, append, or create (default: replace)
        #[arg(
            long,
            value_name = "replace|append|create",
            conflicts_with = "append",
            help_heading = "Advanced options"
        )]
        mode: Option<String>,
        /// Wait for async processing to finish
        #[arg(long, default_value = "false", help_heading = "Common options")]
        wait: bool,
        /// Optional wait timeout in seconds
        #[arg(long, value_name = "seconds", help_heading = "Common options")]
        timeout: Option<f64>,
    },
    /// [Data] Download file to local path (supports binaries/images)
    Get {
        /// Viking URI
        #[arg(value_name = "uri")]
        uri: String,
        /// Local path (must not exist yet)
        #[arg(value_name = "local-path")]
        local_path: String,
    },
    /// [Data] Run semantic retrieval
    Find {
        /// Search query
        #[arg(value_name = "query")]
        query: String,
        /// Target URI
        #[arg(
            short,
            long,
            default_value = "",
            value_name = "uri",
            help_heading = "Common options"
        )]
        uri: String,
        /// Maximum final results returned
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "10",
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Score threshold
        #[arg(short, long, value_name = "score", help_heading = "Common options")]
        threshold: Option<f64>,
        /// Only include results on or after this time (e.g. 48h, 7d, 2026-03-10, ISO-8601)
        #[arg(long = "after", value_name = "time", help_heading = "Advanced options")]
        after: Option<String>,
        /// Only include results on or before this time (e.g. 24h, 2026-03-15, ISO-8601)
        #[arg(
            long = "before",
            value_name = "time",
            help_heading = "Advanced options"
        )]
        before: Option<String>,
        /// Only include results with specific level(s) (0=abstract, 1=overview, 2=file)
        #[arg(
            short = 'L',
            long = "level",
            value_delimiter = ',',
            value_name = "0,1,2",
            help_heading = "Common options"
        )]
        level: Option<Vec<i32>>,
        /// Only include results with specific context type(s) (memory, resource, skill)
        #[arg(
            long = "context-type",
            value_delimiter = ',',
            value_name = "type",
            help_heading = "Common options"
        )]
        context_type: Option<Vec<String>>,
        /// Limit memory retrieval to this peer plus the current user memory
        #[arg(long = "peer-id", value_name = "id", help_heading = "Advanced options")]
        peer_id: Option<String>,
    },
    /// [Experimental][Data] Run context-aware retrieval
    Search {
        /// Search query
        #[arg(value_name = "query")]
        query: String,
        /// Target URI
        #[arg(
            short,
            long,
            default_value = "",
            value_name = "uri",
            help_heading = "Common options"
        )]
        uri: String,
        /// Session ID for context-aware search
        #[arg(long, value_name = "id", help_heading = "Common options")]
        session_id: Option<String>,
        /// Maximum results per search pass. Search may merge multiple passes.
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "10",
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Score threshold
        #[arg(short, long, value_name = "score", help_heading = "Advanced options")]
        threshold: Option<f64>,
        /// Only include results on or after this time (e.g. 48h, 7d, 2026-03-10, ISO-8601)
        #[arg(long = "after", value_name = "time", help_heading = "Advanced options")]
        after: Option<String>,
        /// Only include results on or before this time (e.g. 24h, 2026-03-15, ISO-8601)
        #[arg(
            long = "before",
            value_name = "time",
            help_heading = "Advanced options"
        )]
        before: Option<String>,
        /// Only include results with specific level(s) (0=abstract, 1=overview, 2=file)
        #[arg(
            short = 'L',
            long = "level",
            value_delimiter = ',',
            value_name = "0,1,2",
            help_heading = "Advanced options"
        )]
        level: Option<Vec<i32>>,
        /// Only include results with specific context type(s) (memory, resource, skill)
        #[arg(
            long = "context-type",
            value_delimiter = ',',
            value_name = "type",
            help_heading = "Advanced options"
        )]
        context_type: Option<Vec<String>>,
        /// Limit memory retrieval to this peer plus the current user memory
        #[arg(long = "peer-id", value_name = "id", help_heading = "Advanced options")]
        peer_id: Option<String>,
    },
    /// [Data] Run content pattern search
    Grep {
        /// Target URI
        #[arg(
            short,
            long,
            default_value = "viking://",
            value_name = "uri",
            help_heading = "Common options"
        )]
        uri: String,
        /// Excluded URI range. Any entry whose URI falls under this URI prefix is skipped
        #[arg(
            short = 'x',
            long = "exclude-uri",
            value_name = "uri",
            help_heading = "Advanced options"
        )]
        exclude_uri: Option<String>,
        /// Search pattern
        #[arg(value_name = "pattern")]
        pattern: String,
        /// Case insensitive
        #[arg(short, long, help_heading = "Common options")]
        ignore_case: bool,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "256",
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Maximum depth level to traverse (default: 10)
        #[arg(
            short = 'L',
            long = "level-limit",
            default_value = "10",
            value_name = "n",
            help_heading = "Advanced options"
        )]
        level_limit: i32,
    },
    /// [Data] Run file glob pattern search
    Glob {
        /// Glob pattern
        #[arg(value_name = "pattern")]
        pattern: String,
        /// Search root URI
        #[arg(
            short,
            long,
            default_value = "viking://",
            value_name = "uri",
            help_heading = "Common options"
        )]
        uri: String,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "256",
            value_name = "n",
            help_heading = "Common options"
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
        #[arg(value_name = "content")]
        content: String,
    },
    /// [Data] Privacy config management commands
    Privacy {
        #[command(subcommand)]
        action: PrivacyCommands,
    },
    /// [Experimental][Data] List relations of a resource
    Relations {
        /// Viking URI
        #[arg(value_name = "uri")]
        uri: String,
    },
    /// [Experimental][Data] Create relation links from one URI to one or more targets
    Link {
        /// Source URI
        #[arg(value_name = "from-uri")]
        from_uri: String,
        /// One or more target URIs
        #[arg(value_name = "to-uri")]
        to_uris: Vec<String>,
        /// Reason for linking
        #[arg(
            long,
            default_value = "",
            value_name = "text",
            help_heading = "Common options"
        )]
        reason: String,
    },
    /// [Experimental][Data] Remove a relation link
    Unlink {
        /// Source URI
        #[arg(value_name = "from-uri")]
        from_uri: String,
        /// Target URI to unlink
        #[arg(value_name = "to-uri")]
        to_uri: String,
    },
    /// [Data] Export context as .ovpack
    Export {
        /// Source URI
        #[arg(value_name = "uri")]
        uri: String,
        /// Output .ovpack file path
        #[arg(value_name = "output.ovpack")]
        to: String,
        /// Include dense vector snapshot when compatible metadata is available
        #[arg(long, default_value_t = false, help_heading = "Common options")]
        include_vectors: bool,
    },
    /// [Data] Back up public OpenViking scopes as a restore-only .ovpack
    Backup {
        /// Output .ovpack file path
        #[arg(value_name = "output.ovpack")]
        to: String,
        /// Include dense vector snapshot when compatible metadata is available
        #[arg(long, default_value_t = false, help_heading = "Common options")]
        include_vectors: bool,
    },
    /// [Data] Import .ovpack into target URI
    Import {
        /// Input .ovpack file path
        #[arg(value_name = "file.ovpack")]
        file_path: String,
        /// Target parent URI
        #[arg(value_name = "target-uri")]
        target_uri: String,
        /// Conflict policy: fail, overwrite, or skip
        #[arg(
            long,
            value_parser = ["fail", "overwrite", "skip"],
            value_name = "policy",
            help_heading = "Common options"
        )]
        on_conflict: Option<String>,
        /// Vector handling: auto restores compatible snapshots, recompute ignores them, require fails if unavailable
        #[arg(
            long,
            value_parser = ["auto", "recompute", "require"],
            value_name = "mode",
            help_heading = "Common options"
        )]
        vector_mode: Option<String>,
    },
    /// [Data] Restore a backup .ovpack to original public scope roots
    Restore {
        /// Input backup .ovpack file path
        #[arg(value_name = "backup.ovpack")]
        file_path: String,
        /// Conflict policy: fail, overwrite, or skip
        #[arg(
            long,
            value_parser = ["fail", "overwrite", "skip"],
            value_name = "policy",
            help_heading = "Common options"
        )]
        on_conflict: Option<String>,
        /// Vector handling: auto restores compatible snapshots, recompute ignores them, require fails if unavailable
        #[arg(
            long,
            value_parser = ["auto", "recompute", "require"],
            value_name = "mode",
            help_heading = "Common options"
        )]
        vector_mode: Option<String>,
    },
    // --- Interactive Tools ---
    /// [Interactive] Interactive TUI file explorer
    Tui {
        /// Viking URI to start browsing (default: /)
        #[arg(default_value = "/", value_name = "uri")]
        uri: String,
    },
    /// [Interactive] Chat with vikingbot agent
    Chat {
        /// Message to send to the agent
        #[arg(short, long, value_name = "text", help_heading = "Common options")]
        message: Option<String>,
        /// Session ID (defaults to machine unique ID)
        #[arg(short, long, value_name = "id", help_heading = "Common options")]
        session: Option<String>,
        /// Sender ID
        #[arg(
            long,
            default_value = "user",
            value_name = "id",
            help_heading = "Advanced options"
        )]
        sender: String,
        /// Stream the response (default: true)
        #[arg(
            long,
            default_value_t = true,
            value_name = "bool",
            help_heading = "Advanced options"
        )]
        stream: bool,
        /// Disable rich formatting / markdown rendering
        #[arg(long, help_heading = "Common options")]
        no_format: bool,
        /// Disable command history
        #[arg(long, help_heading = "Advanced options")]
        no_history: bool,
    },

    // --- Status & Observability ---
    /// [Status] Wait for queued async processing to complete
    Wait {
        /// Wait timeout in seconds
        #[arg(long, value_name = "seconds", help_heading = "Common options")]
        timeout: Option<f64>,
    },
    /// [Status] Track async resource processing tasks
    Task {
        #[command(subcommand)]
        action: TaskCommands,
    },
    /// [Status] All OpenViking Server components status
    Status {
        /// Show full component tables
        #[arg(long, help_heading = "Common options")]
        verbose: bool,
    },
    /// [Status] Observe OpenViking Server components status
    Observer {
        #[command(subcommand)]
        action: ObserverCommands,
    },
    /// [Status] Quick health check
    Health,
    /// [Status] Configuration management; run without a subcommand to add, edit, or delete configs
    Config {
        #[command(subcommand)]
        action: Option<ConfigCommands>,
    },
    /// [Status] Choose CLI display language
    #[command(alias = "lang")]
    Language {
        /// Language code: en or zh-CN
        #[arg(value_name = "en|zh-CN")]
        language: Option<String>,
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
    /// [Admin] Reindex semantic/vector artifacts for a URI
    Reindex {
        /// Viking URI
        #[arg(value_name = "uri")]
        uri: String,
        /// Reindex mode
        #[arg(
            long,
            default_value = "vectors_only",
            value_name = "mode",
            help_heading = "Common options"
        )]
        mode: String,
        /// Wait for reindex to complete
        #[arg(
            long,
            default_value_t = true,
            action = ArgAction::Set,
            value_name = "bool",
            help_heading = "Common options"
        )]
        wait: bool,
    },
}

impl Commands {
    /// Returns true if this is an admin command that supports --sudo
    fn is_admin_command(&self) -> bool {
        matches!(
            self,
            Self::Admin { .. } | Self::System { .. } | Self::Reindex { .. }
        )
    }

    fn supports_upload_options(&self) -> bool {
        matches!(self, Self::AddResource { .. } | Self::AddSkill { .. })
    }
}

fn legacy_upload_option_error(
    options: UploadCliOptions,
    command: &Commands,
) -> Option<&'static str> {
    if options.is_set() && !command.supports_upload_options() {
        Some(
            "--progress, --no-progress, and --verbose are only supported for add-resource and add-skill.",
        )
    } else {
        None
    }
}

#[derive(Subcommand)]
enum TaskCommands {
    /// Show status of a specific task
    Status {
        /// Task ID returned by add-resource/add-skill
        #[arg(value_name = "task-id")]
        task_id: String,
    },
    /// List all tracked tasks
    List {
        /// Filter by task type (e.g. add_resource, add_skill, session_commit, reindex)
        #[arg(long, value_name = "type")]
        task_type: Option<String>,
        /// Filter by status (pending, running, completed, failed)
        #[arg(long, value_name = "status")]
        status: Option<String>,
    },
    /// Watch task management (auto-refresh subscriptions)
    Watch {
        #[command(subcommand)]
        action: WatchCommands,
    },
}

#[derive(Subcommand)]
enum SystemCommands {
    /// Wait for queued async processing to complete
    Wait {
        /// Wait timeout in seconds
        #[arg(long, value_name = "seconds")]
        timeout: Option<f64>,
    },
    /// Show component status
    Status,
    /// Quick health check
    Health,
    /// Check filesystem and vector-index consistency for a URI subtree
    Consistency {
        /// Viking URI to check
        #[arg(value_name = "uri")]
        uri: String,
    },
    /// Cryptographic key management commands
    Crypto {
        #[command(subcommand)]
        action: commands::crypto::CryptoCommands,
    },
    /// Backend sync inspection and repair commands
    Backend {
        #[command(subcommand)]
        action: SystemBackendCommands,
    },
}

#[derive(Subcommand)]
enum SystemBackendCommands {
    /// Show multi-write backend sync status for a URI subtree
    #[command(name = "sync-status")]
    SyncStatus {
        /// Viking URI to inspect
        #[arg(value_name = "uri")]
        uri: String,
    },
    /// Retry pending multi-write backend sync work for a URI subtree
    #[command(name = "sync-retry")]
    SyncRetry {
        /// Viking URI to repair
        #[arg(value_name = "uri")]
        uri: String,
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
    /// Get filesystem operation metrics
    Filesystem,
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
        #[arg(value_name = "session-id")]
        session_id: String,
    },
    /// Get full merged session context
    GetSessionContext {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
        /// Token budget for latest archive overview inclusion
        #[arg(long = "token-budget", default_value = "128000", value_name = "tokens")]
        token_budget: i32,
    },
    /// Get one completed archive for a session
    GetSessionArchive {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
        /// Archive ID
        #[arg(value_name = "archive-id")]
        archive_id: String,
    },
    /// Delete a session
    Delete {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
    },
    /// Add one message to a session
    AddMessage {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
        /// Message role, e.g. user/assistant
        #[arg(long, value_name = "role")]
        role: String,
        /// Message content
        #[arg(long, value_name = "content")]
        content: String,
        /// Stable interaction peer id. Omit for self memory.
        #[arg(long = "peer-id", value_name = "peer-id")]
        peer_id: Option<String>,
    },
    /// Add multiple messages to a session
    AddMessages {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
        /// Messages as JSON array of {role, content} objects
        #[arg(value_name = "messages-json")]
        messages: String,
    },
    /// Commit a session (archive messages and extract memories)
    Commit {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
    },
}

#[derive(Subcommand)]
enum SkillCommands {
    /// Add skills from a source
    Add {
        /// Skill source
        #[arg(value_name = "source")]
        source: String,
        /// Install only the named skill(s); use '*' to install all skills in a source
        #[arg(short = 's', long = "skill", value_name = "NAME", num_args = 1.., value_delimiter = ',')]
        skills: Vec<String>,
        /// List available skills in the source without installing
        #[arg(short = 'l', long = "list")]
        list: bool,
        /// Wait until processing is complete
        #[arg(short = 'w', long)]
        wait: bool,
        /// Skip confirmation prompt
        #[arg(short = 'y', long = "yes")]
        yes: bool,
    },
    /// List installed agent skills
    #[command(alias = "ls")]
    List {
        /// Maximum number of skills to list
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "1000",
            value_name = "n"
        )]
        node_limit: i32,
    },
    /// Find installed agent skills semantically
    Find {
        /// Search query
        #[arg(value_name = "query")]
        query: String,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "10",
            value_name = "n"
        )]
        node_limit: i32,
        /// Score threshold
        #[arg(short, long, value_name = "score")]
        threshold: Option<f64>,
        /// Only include results with specific level(s) (0=abstract, 1=overview, 2=file)
        #[arg(
            short = 'L',
            long = "level",
            value_delimiter = ',',
            value_name = "0,1,2"
        )]
        level: Option<Vec<i32>>,
    },
    /// Show one installed skill
    Show {
        /// Skill name
        #[arg(value_name = "name")]
        name: String,
        /// Detail level to show (0=abstract, 1=overview, 2=SKILL.md content)
        #[arg(
            short = 'L',
            long = "level",
            value_parser = clap::value_parser!(i32).range(0..=2),
            value_name = "0|1|2"
        )]
        level: Option<i32>,
        /// Include files under the skill directory
        #[arg(short = 'f', long = "files")]
        files: bool,
        /// Include source metadata when available
        #[arg(long = "source")]
        source: bool,
        /// Output format for this command
        #[arg(long = "format", value_parser = ["table", "json"], value_name = "table|json")]
        format: Option<String>,
        /// Include full SKILL.md content (legacy alias for --level 2)
        #[arg(long, hide = true)]
        content: bool,
    },
    /// Update installed skills from their recorded source
    Update {
        /// Skill name(s) to update; omit to update all installed skills
        #[arg(value_name = "skill")]
        skills: Vec<String>,
        /// Wait until processing is complete
        #[arg(short = 'w', long)]
        wait: bool,
        /// Skip confirmation prompt
        #[arg(short = 'y', long = "yes")]
        yes: bool,
    },
    /// Remove installed skills
    #[command(alias = "rm", alias = "delete")]
    Remove {
        /// Skill name(s) to remove
        #[arg(value_name = "skill")]
        skills: Vec<String>,
        /// Remove all installed skills
        #[arg(long = "all")]
        all: bool,
        /// Skip confirmation prompt
        #[arg(short = 'y', long = "yes")]
        yes: bool,
    },
    /// Validate a local SKILL.md file or skill directory
    Validate {
        /// SKILL.md file or skill directory path
        #[arg(value_name = "path")]
        path: String,
        /// Strict mode; warnings such as name mismatch are reported as errors
        #[arg(long = "strict")]
        strict: bool,
    },
}

#[derive(Subcommand)]
enum WatchCommands {
    /// List watch tasks (auto-refresh subscriptions)
    Ls {
        /// Only show active (non-paused) tasks
        #[arg(long, default_value_t = false)]
        active_only: bool,
    },
    /// Show details of a single watch task
    Show {
        /// task_id (UUID) or to_uri (viking:// URI)
        #[arg(value_name = "task-or-uri")]
        key: String,
    },
    /// Delete a watch task
    Rm {
        /// task_id (UUID) or to_uri (viking:// URI)
        #[arg(value_name = "task-or-uri")]
        key: String,
    },
    /// Pause a watch task (preserves cadence, stops scheduling)
    Pause {
        /// task_id (UUID) or to_uri (viking:// URI)
        #[arg(value_name = "task-or-uri")]
        key: String,
    },
    /// Resume a paused watch task
    Resume {
        /// task_id (UUID) or to_uri (viking:// URI)
        #[arg(value_name = "task-or-uri")]
        key: String,
    },
    /// Update one or more mutable fields of a watch task.
    /// At least one flag is required.
    Update {
        /// task_id (UUID) or to_uri (viking:// URI)
        #[arg(value_name = "task-or-uri")]
        key: String,
        /// New refresh interval in minutes (must be > 0)
        #[arg(long, value_name = "minutes")]
        interval: Option<f64>,
        /// Set active (true) / paused (false) — alternative to pause/resume shortcuts
        #[arg(long, value_name = "bool")]
        active: Option<bool>,
        /// Human-readable reason for the watch task
        #[arg(long, value_name = "text")]
        reason: Option<String>,
        /// Processing instruction forwarded to the refresh handler
        #[arg(long, value_name = "text")]
        instruction: Option<String>,
    },
    /// Trigger an immediate refresh, bypassing the schedule
    Trigger {
        /// task_id (UUID) or to_uri (viking:// URI)
        #[arg(value_name = "task-or-uri")]
        key: String,
    },
}

#[derive(Subcommand)]
enum PrivacyCommands {
    /// List privacy config categories
    Categories,
    /// List targets by category
    List {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
    },
    /// Get current active config for target
    Get {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
        /// Privacy config target key
        #[arg(value_name = "target-key")]
        target_key: String,
    },
    /// Upsert privacy config values
    Upsert {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
        /// Privacy config target key
        #[arg(value_name = "target-key")]
        target_key: String,
        /// JSON object string for values
        #[arg(long, conflicts_with = "values_file", value_name = "json")]
        values_json: Option<String>,
        /// JSON file path for values
        #[arg(
            long = "values-file",
            conflicts_with = "values_json",
            value_name = "path"
        )]
        values_file: Option<String>,
        /// Existing key updates in key=value format (repeatable)
        #[arg(long = "key", value_name = "key=value")]
        key: Vec<String>,
        /// Change reason
        #[arg(long, default_value = "", value_name = "text")]
        change_reason: String,
        /// Optional labels JSON object string
        #[arg(long = "labels-json", value_name = "json")]
        labels_json: Option<String>,
    },
    /// List versions for target
    Versions {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
        /// Privacy config target key
        #[arg(value_name = "target-key")]
        target_key: String,
    },
    /// Get one version by number
    Version {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
        /// Privacy config target key
        #[arg(value_name = "target-key")]
        target_key: String,
        /// Version number
        #[arg(value_name = "version")]
        version: i32,
    },
    /// Activate a version
    Activate {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
        /// Privacy config target key
        #[arg(value_name = "target-key")]
        target_key: String,
        /// Version number
        #[arg(value_name = "version")]
        version: i32,
    },
}

#[derive(Subcommand)]
enum AdminCommands {
    /// Create a new account with its first admin user
    CreateAccount {
        /// Account ID to create
        #[arg(value_name = "account-id")]
        account_id: String,
        /// First admin user ID
        #[arg(long = "admin", value_name = "user-id")]
        admin_user_id: String,
    },
    /// List all accounts (ROOT only)
    ListAccounts,
    /// Delete an account and all associated users (ROOT only)
    DeleteAccount {
        /// Account ID to delete
        #[arg(value_name = "account-id")]
        account_id: String,
    },
    /// Register a new user in an account
    RegisterUser {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// User ID to register
        #[arg(value_name = "user-id")]
        user_id: String,
        /// Role: admin or user
        #[arg(long, default_value = "user", value_name = "role")]
        role: String,
    },
    /// List all users in an account
    ListUsers {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// Maximum number of users to list (default: 100)
        #[arg(long, default_value = "100", value_name = "n")]
        limit: u32,
        /// Filter users by name (supports wildcard * and ?)
        #[arg(long, value_name = "pattern")]
        name: Option<String>,
        /// Filter users by role
        #[arg(long, value_name = "role")]
        role: Option<String>,
    },
    /// Remove a user from an account
    RemoveUser {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// User ID to remove
        #[arg(value_name = "user-id")]
        user_id: String,
    },
    /// Change a user's role (ROOT only)
    SetRole {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// User ID
        #[arg(value_name = "user-id")]
        user_id: String,
        /// New role: admin or user
        #[arg(value_name = "role")]
        role: String,
    },
    /// Regenerate a user's API key (old key immediately invalidated)
    RegenerateKey {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// User ID
        #[arg(value_name = "user-id")]
        user_id: String,
    },
}

impl Commands {
    fn requires_cli_config_file(&self) -> bool {
        !matches!(
            self,
            Commands::Config {
                action: None
                    | Some(
                        ConfigCommands::Switch { .. }
                            | ConfigCommands::Add { .. }
                            | ConfigCommands::Edit(_)
                            | ConfigCommands::Delete(_)
                            | ConfigCommands::List,
                    ),
            } | Commands::Skills {
                action: SkillCommands::Validate { .. },
            } | Commands::Version
        )
    }
}

#[derive(Subcommand)]
enum ConfigCommands {
    /// Show current configuration
    Show,
    /// Validate configuration file
    Validate,
    /// Switch between saved configs
    Switch {
        /// Saved config name to activate. Omit to open the interactive selector.
        #[arg(value_name = "name")]
        name: Option<String>,
    },
    /// List saved configs
    List,
    /// Delete a saved config
    Delete(ConfigDeleteArgs),
    /// Add a saved config without opening the interactive wizard
    Add {
        #[command(subcommand)]
        target: ConfigAddTarget,
    },
    /// Edit a saved config without opening the interactive wizard
    Edit(ConfigEditArgs),
}

#[derive(Subcommand)]
enum ConfigAddTarget {
    /// Add an OpenViking Service config
    OvService(ConfigAddOvServiceArgs),
    /// Add a custom config
    Custom(ConfigAddCustomArgs),
}

#[derive(Args, Debug, Clone)]
struct ConfigAddOvServiceArgs {
    /// Saved config name. Agents should pass this for idempotent retries; generated when omitted.
    #[arg(long, value_name = "name", help_heading = "Common options")]
    name: Option<String>,
    /// Read API key from stdin
    #[arg(long, conflicts_with = "api_key_env", help_heading = "Common options")]
    api_key_stdin: bool,
    /// Read API key from an environment variable
    #[arg(
        long,
        conflicts_with = "api_key_stdin",
        value_name = "env",
        help_heading = "Common options"
    )]
    api_key_env: Option<String>,
    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, value_name = "account", help_heading = "Advanced options")]
    account: Option<String>,
    /// User identifier to send as X-OpenViking-User
    #[arg(long, value_name = "user", help_heading = "Advanced options")]
    user: Option<String>,
    /// Make the saved config active after validation
    #[arg(long, help_heading = "Common options")]
    activate: bool,
    /// Replace an existing saved config
    #[arg(long, help_heading = "Common options")]
    force: bool,
}

#[derive(Args, Debug, Clone)]
struct ConfigAddCustomArgs {
    /// Saved config name. Agents should pass this for idempotent retries; generated when omitted.
    #[arg(long, value_name = "name", help_heading = "Common options")]
    name: Option<String>,
    /// OpenViking server URL
    #[arg(long, value_name = "url", help_heading = "Common options")]
    url: Option<String>,
    /// Read API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["api_key_env", "root_api_key_stdin"],
        help_heading = "Common options"
    )]
    api_key_stdin: bool,
    /// Read API key from an environment variable
    #[arg(
        long,
        conflicts_with = "api_key_stdin",
        value_name = "env",
        help_heading = "Common options"
    )]
    api_key_env: Option<String>,
    /// Read root API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_env", "api_key_stdin"],
        help_heading = "Common options"
    )]
    root_api_key_stdin: bool,
    /// Read root API key from an environment variable
    #[arg(
        long,
        conflicts_with = "root_api_key_stdin",
        value_name = "env",
        help_heading = "Common options"
    )]
    root_api_key_env: Option<String>,
    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, value_name = "account", help_heading = "Advanced options")]
    account: Option<String>,
    /// User identifier to send as X-OpenViking-User
    #[arg(long, value_name = "user", help_heading = "Advanced options")]
    user: Option<String>,
    /// Make the saved config active after validation
    #[arg(long, help_heading = "Common options")]
    activate: bool,
    /// Replace an existing saved config
    #[arg(long, help_heading = "Common options")]
    force: bool,
}

#[derive(Args, Debug, Clone)]
struct ConfigEditArgs {
    /// Saved config name to edit
    #[arg(value_name = "name")]
    name: String,
    /// Rename the saved config
    #[arg(long, value_name = "name", help_heading = "Common options")]
    new_name: Option<String>,
    /// New server URL. OpenViking Service configs use a fixed URL.
    #[arg(long, value_name = "url", help_heading = "Common options")]
    url: Option<String>,
    /// Read replacement API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["api_key_env", "clear_api_key", "root_api_key_stdin"],
        help_heading = "Common options"
    )]
    api_key_stdin: bool,
    /// Read replacement API key from an environment variable
    #[arg(
        long,
        conflicts_with_all = ["api_key_stdin", "clear_api_key"],
        value_name = "env",
        help_heading = "Common options"
    )]
    api_key_env: Option<String>,
    /// Remove the API key
    #[arg(
        long,
        conflicts_with_all = ["api_key_stdin", "api_key_env"],
        help_heading = "Common options"
    )]
    clear_api_key: bool,
    /// Read replacement root API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_env", "clear_root_api_key", "api_key_stdin"],
        help_heading = "Common options"
    )]
    root_api_key_stdin: bool,
    /// Read replacement root API key from an environment variable
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_stdin", "clear_root_api_key"],
        value_name = "env",
        help_heading = "Common options"
    )]
    root_api_key_env: Option<String>,
    /// Remove the root API key
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_stdin", "root_api_key_env"],
        help_heading = "Common options"
    )]
    clear_root_api_key: bool,
    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, value_name = "account", help_heading = "Advanced options")]
    account: Option<String>,
    /// User identifier to send as X-OpenViking-User
    #[arg(long, value_name = "user", help_heading = "Advanced options")]
    user: Option<String>,
    /// Make the saved config active after validation
    #[arg(long, help_heading = "Common options")]
    activate: bool,
    /// Replace an existing saved config when renaming
    #[arg(long, help_heading = "Common options")]
    force: bool,
}

#[derive(Args, Debug, Clone)]
struct ConfigDeleteArgs {
    /// Saved config name to delete
    #[arg(value_name = "name")]
    name: String,
    /// Delete even when the saved config file cannot be parsed
    #[arg(long, help_heading = "Common options")]
    force: bool,
}

fn find_command_index(args: &[OsString]) -> Option<usize> {
    let mut i = 1;
    while i < args.len() {
        let token = args[i].to_string_lossy();
        match token.as_ref() {
            "--output" | "-o" | "--compact" | "--account" | "--user" => {
                i += 2;
            }
            "--sudo" | "--progress" | "--no-progress" | "--verbose" | "-v" => {
                i += 1;
            }
            _ if token.starts_with('-') => {
                i += 1;
            }
            _ => return Some(i),
        }
    }
    None
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PlainHelpMisuse {
    help_command: String,
}

fn plain_help_misuse(args: &[OsString]) -> Option<PlainHelpMisuse> {
    let tokens = command_tokens_for_plain_help(args);
    let help_index = tokens.iter().position(|token| token == "help")?;
    if help_index == 0 {
        return Some(PlainHelpMisuse {
            help_command: "ov --help".to_string(),
        });
    }

    let command = tokens
        .first()
        .map(|token| canonical_plain_help_token(token))?;
    if allows_plain_help_as_user_input(command) {
        return None;
    }

    let mut path = vec![command.to_string()];
    if is_plain_help_group(command) {
        if help_index == 1 {
            return Some(PlainHelpMisuse {
                help_command: prefixed_help_command(&path),
            });
        }
        if let Some(subcommand) = tokens.get(1).map(|token| canonical_plain_help_token(token)) {
            path.push(subcommand.to_string());
        }
        if command == "task"
            && path.get(1).is_some_and(|token| token == "watch")
            && let Some(watch_subcommand) =
                tokens.get(2).map(|token| canonical_plain_help_token(token))
            && watch_subcommand != "help"
        {
            path.push(watch_subcommand.to_string());
        }
        if command == "system"
            && path.get(1).is_some_and(|token| token == "backend")
            && let Some(backend_subcommand) =
                tokens.get(2).map(|token| canonical_plain_help_token(token))
            && backend_subcommand != "help"
        {
            path.push(backend_subcommand.to_string());
        }
        return Some(PlainHelpMisuse {
            help_command: prefixed_help_command(&path),
        });
    }

    Some(PlainHelpMisuse {
        help_command: prefixed_help_command(&path),
    })
}

fn command_tokens_for_plain_help(args: &[OsString]) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut i = 1;
    while i < args.len() {
        let token = args[i].to_string_lossy();
        let token_ref = token.as_ref();
        if consumes_plain_help_value(token_ref) {
            i += if token_ref.contains('=') { 1 } else { 2 };
            continue;
        }
        if token_ref.starts_with('-') {
            i += 1;
            continue;
        }
        tokens.push(token.into_owned());
        i += 1;
    }
    tokens
}

fn consumes_plain_help_value(token: &str) -> bool {
    matches!(
        token,
        "-o" | "--output"
            | "-c"
            | "--compact"
            | "--account"
            | "--user"
            | "-u"
            | "--uri"
            | "-x"
            | "--exclude-uri"
            | "--to"
            | "--parent"
            | "-p"
            | "--parent-auto-create"
            | "--reason"
            | "--instruction"
            | "--timeout"
            | "--watch-interval"
            | "--ignore-dirs"
            | "--include"
            | "--exclude"
            | "--description"
            | "-n"
            | "--node-limit"
            | "--limit"
            | "-l"
            | "--abs-limit"
            | "-L"
            | "--level"
            | "--level-limit"
            | "-t"
            | "--threshold"
            | "--after"
            | "--before"
            | "--context-type"
            | "--peer-id"
            | "-s"
            | "--skill"
            | "--session"
            | "--session-id"
            | "-m"
            | "--sender"
            | "--message"
            | "--role"
            | "--content"
            | "--from-file"
            | "--mode"
            | "--stream"
            | "--on-conflict"
            | "--vector-mode"
            | "--task-type"
            | "--status"
            | "--token-budget"
            | "--interval"
            | "--active"
            | "--values-json"
            | "--values-file"
            | "--key"
            | "--change-reason"
            | "--labels-json"
            | "--admin"
            | "--name"
            | "--new-name"
            | "--url"
            | "--api-key-env"
            | "--root-api-key-env"
            | "--output-file"
            | "--format"
    ) || token.starts_with("--output=")
        || token.starts_with("--compact=")
        || token.starts_with("--account=")
        || token.starts_with("--user=")
        || token.starts_with("--uri=")
        || token.starts_with("--exclude-uri=")
        || token.starts_with("--to=")
        || token.starts_with("--parent=")
        || token.starts_with("--parent-auto-create=")
        || token.starts_with("--reason=")
        || token.starts_with("--instruction=")
        || token.starts_with("--timeout=")
        || token.starts_with("--watch-interval=")
        || token.starts_with("--ignore-dirs=")
        || token.starts_with("--include=")
        || token.starts_with("--exclude=")
        || token.starts_with("--description=")
        || token.starts_with("--node-limit=")
        || token.starts_with("--limit=")
        || token.starts_with("--abs-limit=")
        || token.starts_with("--level=")
        || token.starts_with("--level-limit=")
        || token.starts_with("--threshold=")
        || token.starts_with("--after=")
        || token.starts_with("--before=")
        || token.starts_with("--context-type=")
        || token.starts_with("--peer-id=")
        || token.starts_with("--skill=")
        || token.starts_with("--session=")
        || token.starts_with("--session-id=")
        || token.starts_with("--sender=")
        || token.starts_with("--message=")
        || token.starts_with("--role=")
        || token.starts_with("--content=")
        || token.starts_with("--from-file=")
        || token.starts_with("--mode=")
        || token.starts_with("--stream=")
        || token.starts_with("--on-conflict=")
        || token.starts_with("--vector-mode=")
        || token.starts_with("--task-type=")
        || token.starts_with("--status=")
        || token.starts_with("--token-budget=")
        || token.starts_with("--interval=")
        || token.starts_with("--active=")
        || token.starts_with("--values-json=")
        || token.starts_with("--values-file=")
        || token.starts_with("--key=")
        || token.starts_with("--change-reason=")
        || token.starts_with("--labels-json=")
        || token.starts_with("--admin=")
        || token.starts_with("--name=")
        || token.starts_with("--new-name=")
        || token.starts_with("--url=")
        || token.starts_with("--api-key-env=")
        || token.starts_with("--root-api-key-env=")
        || token.starts_with("--output-file=")
        || token.starts_with("--format=")
}

fn canonical_plain_help_token(token: &str) -> &str {
    match token {
        "list" => "ls",
        "del" | "delete" => "rm",
        "rename" => "mv",
        "lang" => "language",
        other => other,
    }
}

fn allows_plain_help_as_user_input(command: &str) -> bool {
    matches!(
        command,
        "add-resource" | "add-skill" | "find" | "search" | "grep" | "glob" | "add-memory"
    )
}

fn is_plain_help_group(command: &str) -> bool {
    matches!(
        command,
        "config" | "task" | "admin" | "system" | "session" | "privacy" | "observer" | "skills"
    )
}

fn prefixed_help_command(path: &[String]) -> String {
    if path.is_empty() {
        "ov --help".to_string()
    } else {
        format!("ov {} --help", path.join(" "))
    }
}

fn pre_parse_requires_cli_config_file(args: &[OsString]) -> bool {
    let tokens = command_tokens_for_config_gate(args);
    let Some(command) = tokens
        .first()
        .map(|token| canonical_plain_help_token(token))
    else {
        return false;
    };

    match command {
        "config" => config_command_requires_cli_config_file(&tokens),
        "language" | "version" => false,
        "task" => known_task_command_requires_config(&tokens),
        "admin" => tokens
            .get(1)
            .map(|token| is_admin_subcommand(token))
            .unwrap_or(false),
        "system" => known_system_command_requires_config(&tokens),
        "session" => tokens
            .get(1)
            .map(|token| is_session_subcommand(token))
            .unwrap_or(false),
        "privacy" => tokens
            .get(1)
            .map(|token| is_privacy_subcommand(token))
            .unwrap_or(false),
        "skills" => tokens
            .get(1)
            .map(|token| is_skill_subcommand(token))
            .unwrap_or(false),
        "observer" => tokens
            .get(1)
            .map(|token| is_observer_subcommand(token))
            .unwrap_or(false),
        _ => is_top_level_server_command(command),
    }
}

fn config_command_requires_cli_config_file(tokens: &[String]) -> bool {
    matches!(tokens.get(1).map(String::as_str), Some("show" | "validate"))
}

fn is_config_agent_command_request(args: &[OsString]) -> bool {
    let tokens = command_tokens_for_config_gate(args);
    if tokens.first().map(String::as_str) != Some("config") {
        return false;
    }

    match tokens.get(1).map(String::as_str) {
        Some("add" | "edit" | "delete" | "list") => true,
        Some("switch") => tokens.get(2).is_some(),
        _ => false,
    }
}

fn command_tokens_for_config_gate(args: &[OsString]) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut seen_command = false;
    let mut i = 1;

    while i < args.len() {
        let token = args[i].to_string_lossy();
        let token_ref = token.as_ref();

        if token_ref == "--" {
            tokens.extend(
                args.iter()
                    .skip(i + 1)
                    .map(|arg| arg.to_string_lossy().to_string()),
            );
            break;
        }

        if !seen_command {
            if token_ref == "--compact" || token_ref == "-c" {
                i += if args
                    .get(i + 1)
                    .is_some_and(|value| is_bool_arg(&value.to_string_lossy()))
                {
                    2
                } else {
                    1
                };
                continue;
            }
            if token_ref.starts_with("--") {
                i += if global_option_takes_value(token_ref) && !token_ref.contains('=') {
                    2
                } else {
                    1
                };
                continue;
            }
            if token_ref.starts_with('-') {
                i += if global_short_option_takes_value(token_ref) {
                    2
                } else {
                    1
                };
                continue;
            }
            seen_command = true;
            tokens.push(token.into_owned());
            i += 1;
            continue;
        }

        if token_ref == "--compact" || token_ref == "-c" {
            i += if args
                .get(i + 1)
                .is_some_and(|value| is_bool_arg(&value.to_string_lossy()))
            {
                2
            } else {
                1
            };
            continue;
        }
        if consumes_plain_help_value(token_ref) {
            i += if token_ref.contains('=') { 1 } else { 2 };
            continue;
        }
        if token_ref.starts_with('-') {
            i += 1;
            continue;
        }
        tokens.push(token.into_owned());
        i += 1;
    }

    tokens
}

fn known_task_command_requires_config(tokens: &[String]) -> bool {
    match tokens.get(1).map(String::as_str) {
        Some("status" | "list") => true,
        Some("watch") => match tokens.get(2).map(String::as_str) {
            None => true,
            Some(token) => is_watch_subcommand(token),
        },
        _ => false,
    }
}

fn known_system_command_requires_config(tokens: &[String]) -> bool {
    match tokens.get(1).map(String::as_str) {
        Some("wait" | "status" | "health" | "consistency") => true,
        Some("backend") => matches!(
            tokens.get(2).map(String::as_str),
            None | Some("sync-status" | "sync-retry")
        ),
        Some("crypto") => matches!(tokens.get(2).map(String::as_str), None | Some("init-key")),
        _ => false,
    }
}

fn is_top_level_server_command(command: &str) -> bool {
    matches!(
        command,
        "add-resource"
            | "add-skill"
            | "ls"
            | "tree"
            | "mkdir"
            | "rm"
            | "mv"
            | "stat"
            | "read"
            | "abstract"
            | "overview"
            | "write"
            | "get"
            | "find"
            | "search"
            | "grep"
            | "glob"
            | "add-memory"
            | "relations"
            | "link"
            | "unlink"
            | "export"
            | "backup"
            | "import"
            | "restore"
            | "tui"
            | "chat"
            | "wait"
            | "status"
            | "health"
            | "reindex"
    )
}

fn is_watch_subcommand(token: &str) -> bool {
    matches!(
        token,
        "ls" | "show" | "rm" | "pause" | "resume" | "update" | "trigger"
    )
}

fn is_skill_subcommand(token: &str) -> bool {
    matches!(
        token,
        "add" | "list" | "ls" | "find" | "show" | "update" | "remove" | "rm" | "delete"
    )
}

fn is_admin_subcommand(token: &str) -> bool {
    matches!(
        token,
        "create-account"
            | "list-accounts"
            | "delete-account"
            | "register-user"
            | "list-users"
            | "remove-user"
            | "set-role"
            | "regenerate-key"
    )
}

fn is_observer_subcommand(token: &str) -> bool {
    matches!(
        token,
        "queue" | "vikingdb" | "models" | "transaction" | "retrieval" | "filesystem" | "system"
    )
}

fn is_session_subcommand(token: &str) -> bool {
    matches!(
        token,
        "new"
            | "list"
            | "get"
            | "get-session-context"
            | "get-session-archive"
            | "delete"
            | "add-message"
            | "add-messages"
            | "commit"
    )
}

fn is_privacy_subcommand(token: &str) -> bool {
    matches!(
        token,
        "categories" | "list" | "get" | "upsert" | "versions" | "version" | "activate"
    )
}

fn preprocess_privacy_get_shortcut(args: Vec<OsString>) -> Vec<OsString> {
    let Some(cmd_idx) = find_command_index(&args) else {
        return args;
    };
    if args[cmd_idx].to_string_lossy() != "privacy" {
        return args;
    }
    let Some(next) = args.get(cmd_idx + 1) else {
        return args;
    };
    let next_token = next.to_string_lossy();
    if next_token.starts_with('-') || is_privacy_subcommand(&next_token) {
        return args;
    }

    let mut out = Vec::with_capacity(args.len() + 1);
    out.extend(args[..=cmd_idx].iter().cloned());
    out.push(OsString::from("get"));
    out.extend(args[cmd_idx + 1..].iter().cloned());
    out
}

fn preprocess_privacy_upsert_key_flags(args: Vec<OsString>) -> Vec<OsString> {
    let Some(cmd_idx) = find_command_index(&args) else {
        return args;
    };
    if args[cmd_idx].to_string_lossy() != "privacy" {
        return args;
    }
    if args
        .get(cmd_idx + 1)
        .map(|s| s.to_string_lossy().to_string())
        != Some("upsert".to_string())
    {
        return args;
    }

    let mut converted: Vec<OsString> = Vec::with_capacity(args.len());
    let mut i = 0;

    while i < args.len() {
        let arg_lossy = args[i].to_string_lossy();

        if i > cmd_idx + 1 && arg_lossy == "--" {
            i += 1;
            continue;
        }

        if i > cmd_idx + 1 && arg_lossy.starts_with("--key-") {
            let suffix = &arg_lossy[6..];
            if suffix.is_empty() {
                converted.push(args[i].clone());
                i += 1;
                continue;
            }

            if let Some((key, value)) = suffix.split_once('=') {
                converted.push(OsString::from("--key"));
                converted.push(OsString::from(format!("{}={}", key, value)));
                i += 1;
                continue;
            }

            if i + 1 < args.len() {
                let next_val = args[i + 1].to_string_lossy();
                converted.push(OsString::from("--key"));
                converted.push(OsString::from(format!("{}={}", suffix, next_val)));
                i += 2;
                continue;
            }

            converted.push(args[i].clone());
            i += 1;
            continue;
        }

        converted.push(args[i].clone());
        i += 1;
    }

    converted
}

fn preprocess_privacy_args(args: Vec<OsString>) -> Vec<OsString> {
    let args = preprocess_privacy_get_shortcut(args);
    preprocess_privacy_upsert_key_flags(args)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LanguageGateAction {
    Continue,
    Prompt,
    ExitNonInteractive,
}

fn language_gate_action(
    args: &[OsString],
    has_saved_language: bool,
    is_interactive: bool,
) -> LanguageGateAction {
    if has_saved_language
        || is_language_command_request(args)
        || is_config_agent_command_request(args)
    {
        LanguageGateAction::Continue
    } else if is_interactive {
        LanguageGateAction::Prompt
    } else {
        LanguageGateAction::ExitNonInteractive
    }
}

async fn ensure_language_selected_before_command(args: &[OsString]) -> Result<bool> {
    match language_gate_action(
        args,
        i18n::has_saved_language(),
        io::stdin().is_terminal() && io::stdout().is_terminal(),
    ) {
        LanguageGateAction::Continue => Ok(true),
        LanguageGateAction::Prompt => {
            handlers::handle_language(None).await?;
            Ok(i18n::has_saved_language())
        }
        LanguageGateAction::ExitNonInteractive => {
            eprintln!("{}", language_required_message());
            std::process::exit(2);
        }
    }
}

fn is_language_command_request(args: &[OsString]) -> bool {
    matches!(
        first_command_token(args).as_deref(),
        Some("language" | "lang")
    )
}

fn first_command_token(args: &[OsString]) -> Option<String> {
    let mut index = 1usize;

    while index < args.len() {
        let token = args[index].to_string_lossy();
        if token == "--" {
            return args
                .get(index + 1)
                .map(|value| value.to_string_lossy().to_string());
        }
        if token == "--compact" || token == "-c" {
            index += if args
                .get(index + 1)
                .is_some_and(|value| is_bool_arg(&value.to_string_lossy()))
            {
                2
            } else {
                1
            };
            continue;
        }
        if token.starts_with("--") {
            index += if global_option_takes_value(&token) && !token.contains('=') {
                2
            } else {
                1
            };
            continue;
        }
        if token.starts_with('-') {
            index += if global_short_option_takes_value(&token) {
                2
            } else {
                1
            };
            continue;
        }
        return Some(token.to_string());
    }

    None
}

fn global_option_takes_value(option: &str) -> bool {
    matches!(option, "--output" | "--account" | "--user")
}

fn global_short_option_takes_value(option: &str) -> bool {
    matches!(option, "-o")
}

fn is_bool_arg(value: &str) -> bool {
    matches!(
        value,
        "true" | "false" | "True" | "False" | "TRUE" | "FALSE"
    )
}

fn language_required_message() -> String {
    format!(
        "{} {}\n{}:\n  {}\n  {}",
        theme::brand_title("OpenViking").bold(),
        theme::body("needs a display language before running commands."),
        theme::strong("Run one of"),
        theme::command("ov language en").bold(),
        theme::command("ov language zh-CN").bold(),
    )
}

fn language_command_can_run_picker(has_language_value: bool, is_interactive: bool) -> bool {
    has_language_value || is_interactive
}

#[tokio::main]
async fn main() {
    let args = preprocess_privacy_args(std::env::args_os().collect());
    let command_display = error_ui::display_command(&args);
    match ensure_language_selected_before_command(&args).await {
        Ok(true) => {}
        Ok(false) => return,
        Err(e) => {
            let report = error_ui::report_for_runtime_error(&command_display, &e);
            error_ui::print_report(&report, false);
            std::process::exit(2);
        }
    }

    if help_ui::is_top_level_help_request(&args) {
        print!("{}", help_ui::render_top_level_help());
        return;
    }
    if let Some(help) = help_ui::render_command_help_request(&args) {
        print!("{help}");
        return;
    }
    if let Some(misuse) = plain_help_misuse(&args) {
        let report = error_ui::report_for_plain_help_error(&command_display, misuse.help_command);
        error_ui::print_report(&report, false);
        std::process::exit(2);
    }

    let mut preloaded_required_config = if pre_parse_requires_cli_config_file(&args) {
        match Config::load_required() {
            Ok(config) => Some(config),
            Err(e) => {
                let report = error_ui::report_for_runtime_error(&command_display, &e);
                error_ui::print_report(&report, false);
                std::process::exit(2);
            }
        }
    } else {
        None
    };

    let cli = match Cli::try_parse_from(args.clone()) {
        Ok(cli) => cli,
        Err(error) => {
            if error.exit_code() == 0 {
                let _ = error.print();
            } else {
                let report = error_ui::report_for_clap_error(&args, &error.to_string());
                error_ui::print_report(&report, false);
            }
            std::process::exit(error.exit_code());
        }
    };

    let output_format = cli.output;
    let compact = cli.compact;
    let legacy_upload_options = UploadCliOptions {
        progress: cli.progress,
        no_progress: cli.no_progress,
        verbose: cli.verbose,
    };
    if let Some(message) = legacy_upload_option_error(legacy_upload_options, &cli.command) {
        let report = error_ui::report_for_message_error(
            &command_display,
            "Command Error",
            message,
            vec![
                error_ui::ErrorAction::new("ov add-resource --help", "Show add-resource options"),
                error_ui::ErrorAction::new("ov add-skill --help", "Show add-skill options"),
            ],
        );
        error_ui::print_report(&report, false);
        std::process::exit(2);
    }

    // Check this before loading config so misplaced --sudo reports the command rule directly.
    if cli.sudo && !cli.command.is_admin_command() {
        let language = i18n::Language::current();
        let (title, message, actions) = match language {
            i18n::Language::En => (
                "Command Error",
                "--sudo is only supported for admin, system, and reindex commands.",
                vec![
                    error_ui::ErrorAction::new("ov admin --help", "Show admin commands"),
                    error_ui::ErrorAction::new("ov system --help", "Show system commands"),
                    error_ui::ErrorAction::new("ov reindex --help", "Show reindex options"),
                ],
            ),
            i18n::Language::ZhCn => (
                "命令错误",
                "--sudo 只支持 admin、system 和 reindex 命令。",
                vec![
                    error_ui::ErrorAction::new("ov admin --help", "查看管理命令"),
                    error_ui::ErrorAction::new("ov system --help", "查看系统命令"),
                    error_ui::ErrorAction::new("ov reindex --help", "查看重建索引选项"),
                ],
            ),
        };
        let report = error_ui::report_for_message_error(&command_display, title, message, actions);
        error_ui::print_report(&report, false);
        std::process::exit(2);
    }

    if let Commands::Language { language } = &cli.command {
        if !language_command_can_run_picker(
            language.is_some(),
            io::stdin().is_terminal() && io::stdout().is_terminal(),
        ) {
            eprintln!("{}", language_required_message());
            std::process::exit(2);
        }
        if let Err(e) = handlers::handle_language(language.clone()).await {
            let report = error_ui::report_for_runtime_error(&command_display, &e);
            error_ui::print_report(&report, cli.verbose);
            std::process::exit(2);
        }
        return;
    }

    let config_result = if cli.command.requires_cli_config_file() {
        match preloaded_required_config.take() {
            Some(config) => Ok(config),
            None => Config::load_required(),
        }
    } else {
        Config::load_default()
    };
    let config = match config_result {
        Ok(config) => config,
        Err(e) => {
            let report = error_ui::report_for_runtime_error(&command_display, &e);
            error_ui::print_report(&report, cli.verbose);
            std::process::exit(2);
        }
    };
    let ctx = CliContext::from_config(
        config,
        output_format,
        compact,
        cli.account.clone(),
        cli.user.clone(),
        cli.sudo,
        None,
        None,
        if cli.profile { Some(true) } else { None },
    );
    let verbose_errors = ctx.is_verbose();

    // Check if --sudo is used but root_api_key is not configured
    if ctx.sudo && ctx.config.root_api_key.is_none() {
        let language = i18n::Language::current();
        let (title, message, actions) = match language {
            i18n::Language::En => (
                "Configuration Error",
                "--sudo requires root_api_key in ~/.openviking/ovcli.conf.",
                vec![
                    error_ui::ErrorAction::new("ov config", "Edit the active config"),
                    error_ui::ErrorAction::new("ov config show", "Show the active config"),
                ],
            ),
            i18n::Language::ZhCn => (
                "配置错误",
                "--sudo 需要在 ~/.openviking/ovcli.conf 中配置 root_api_key。",
                vec![
                    error_ui::ErrorAction::new("ov config", "编辑当前配置"),
                    error_ui::ErrorAction::new("ov config show", "显示当前配置"),
                ],
            ),
        };
        let report = error_ui::report_for_message_error(&command_display, title, message, actions);
        error_ui::print_report(&report, false);
        std::process::exit(2);
    };

    let result = match cli.command {
        Commands::AddResource {
            path,
            to,
            parent,
            parent_auto_create,
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
            upload_options,
        } => {
            let ctx =
                ctx.with_upload_options(upload_options.merged_with_legacy(legacy_upload_options));
            handlers::handle_add_resource(
                path,
                to,
                parent,
                parent_auto_create,
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
            upload_options,
        } => {
            let ctx =
                ctx.with_upload_options(upload_options.merged_with_legacy(legacy_upload_options));
            handlers::handle_add_skill(data, wait, timeout, ctx).await
        }
        Commands::Skills { action } => match action {
            SkillCommands::Add {
                source,
                skills,
                list,
                wait,
                yes,
            } => {
                let client = ctx.get_client();
                commands::skills::add(
                    &client,
                    &source,
                    skills,
                    list,
                    wait,
                    yes,
                    ctx.should_show_progress(),
                    ctx.is_verbose(),
                    ctx.output_format,
                    ctx.compact,
                )
                .await
            }
            SkillCommands::List { node_limit } => {
                let client = ctx.get_client();
                commands::skills::list(&client, node_limit, ctx.output_format, ctx.compact).await
            }
            SkillCommands::Find {
                query,
                node_limit,
                threshold,
                level,
            } => {
                let client = ctx.get_client();
                commands::skills::find(
                    &client,
                    &query,
                    node_limit,
                    threshold,
                    level,
                    ctx.output_format,
                    ctx.compact,
                )
                .await
            }
            SkillCommands::Show {
                name,
                level,
                files,
                source,
                format,
                content,
            } => {
                let client = ctx.get_client();
                let output_format = format
                    .as_deref()
                    .map(OutputFormat::from)
                    .unwrap_or(ctx.output_format);
                let level = if content { Some(2) } else { level };
                commands::skills::show(
                    &client,
                    &name,
                    level,
                    files,
                    source,
                    output_format,
                    ctx.compact,
                )
                .await
            }
            SkillCommands::Update { skills, wait, yes } => {
                let client = ctx.get_client();
                commands::skills::update(&client, skills, wait, yes, ctx.output_format, ctx.compact)
                    .await
            }
            SkillCommands::Remove { skills, all, yes } => {
                let client = ctx.get_client();
                commands::skills::remove(&client, skills, all, yes, ctx.output_format, ctx.compact)
                    .await
            }
            SkillCommands::Validate { path, strict } => {
                let client = ctx.get_client();
                commands::skills::validate(&client, &path, strict, ctx.output_format, ctx.compact)
                    .await
            }
        },
        Commands::Relations { uri } => handlers::handle_relations(uri, ctx).await,
        Commands::Link {
            from_uri,
            to_uris,
            reason,
        } => handlers::handle_link(from_uri, to_uris, reason, ctx).await,
        Commands::Unlink { from_uri, to_uri } => {
            handlers::handle_unlink(from_uri, to_uri, ctx).await
        }
        Commands::Export {
            uri,
            to,
            include_vectors,
        } => handlers::handle_export(uri, to, include_vectors, ctx).await,
        Commands::Backup {
            to,
            include_vectors,
        } => handlers::handle_backup(to, include_vectors, ctx).await,
        Commands::Import {
            file_path,
            target_uri,
            on_conflict,
            vector_mode,
        } => handlers::handle_import(file_path, target_uri, on_conflict, vector_mode, ctx).await,
        Commands::Restore {
            file_path,
            on_conflict,
            vector_mode,
        } => handlers::handle_restore(file_path, on_conflict, vector_mode, ctx).await,
        Commands::Wait { timeout } => {
            let client = ctx.get_client();
            commands::system::wait(&client, timeout, ctx.output_format, ctx.compact).await
        }
        Commands::Task { action } => match action {
            TaskCommands::Status { task_id } => {
                let client = ctx.get_client();
                commands::task::status(&client, &task_id, ctx.output_format, ctx.compact).await
            }
            TaskCommands::List { task_type, status } => {
                let client = ctx.get_client();
                commands::task::list(
                    &client,
                    task_type.as_deref(),
                    status.as_deref(),
                    ctx.output_format,
                    ctx.compact,
                )
                .await
            }
            TaskCommands::Watch { action } => {
                let client = ctx.get_client();
                match action {
                    WatchCommands::Ls { active_only } => {
                        commands::watch::ls(&client, active_only, ctx.output_format, ctx.compact)
                            .await
                    }
                    WatchCommands::Show { key } => {
                        commands::watch::show(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Rm { key } => {
                        commands::watch::rm(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Pause { key } => {
                        commands::watch::pause(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Resume { key } => {
                        commands::watch::resume(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Update {
                        key,
                        interval,
                        active,
                        reason,
                        instruction,
                    } => {
                        commands::watch::update(
                            &client,
                            &key,
                            interval,
                            active,
                            reason,
                            instruction,
                            ctx.output_format,
                            ctx.compact,
                        )
                        .await
                    }
                    WatchCommands::Trigger { key } => {
                        commands::watch::trigger(&client, &key, ctx.output_format, ctx.compact)
                            .await
                    }
                }
            }
        },
        Commands::Status { verbose } => {
            let client = ctx.get_client();
            commands::system::diagnostic_status(
                &client,
                &ctx.config,
                ctx.output_format,
                ctx.compact,
                verbose,
            )
            .await
        }
        Commands::Health => handlers::handle_health(ctx).await,
        Commands::System { action } => handlers::handle_system(action, ctx).await,
        Commands::Observer { action } => handlers::handle_observer(action, ctx).await,
        Commands::Session { action } => handlers::handle_session(action, ctx).await,
        Commands::Admin { action } => handlers::handle_admin(action, ctx).await,
        Commands::Privacy { action } => handlers::handle_privacy(action, ctx).await,
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
        Commands::Language { .. } => unreachable!("language command is handled before config load"),
        Commands::Version => {
            println!(
                "{}     {}",
                theme::muted("CLI:"),
                theme::version(env!("OPENVIKING_CLI_VERSION")).bold()
            );

            // Try to get server version from /health endpoint with a short timeout (3 seconds)
            let client = ctx.get_client_with_timeout(Some(3.0));
            match client.get::<serde_json::Value>("/health", &[]).await {
                Ok(health) => {
                    if let Some(version) = health.get("version").and_then(|v| v.as_str()) {
                        println!(
                            "{}  {}",
                            theme::muted("Server:"),
                            theme::sky_value(version).bold()
                        );
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
            handlers::handle_write(uri, content, from_file, effective_mode, wait, timeout, ctx)
                .await
        }
        Commands::Reindex { uri, mode, wait } => {
            handlers::handle_reindex(uri, mode, wait, ctx).await
        }
        Commands::Get { uri, local_path } => handlers::handle_get(uri, local_path, ctx).await,
        Commands::Find {
            query,
            uri,
            node_limit,
            threshold,
            after,
            before,
            level,
            context_type,
            peer_id,
        } => {
            handlers::handle_find(
                query,
                uri,
                node_limit,
                threshold,
                after,
                before,
                level,
                context_type,
                peer_id,
                ctx,
            )
            .await
        }
        Commands::Search {
            query,
            uri,
            session_id,
            node_limit,
            threshold,
            after,
            before,
            level,
            context_type,
            peer_id,
        } => {
            handlers::handle_search(
                query,
                uri,
                session_id,
                node_limit,
                threshold,
                after,
                before,
                level,
                context_type,
                peer_id,
                ctx,
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
        if !matches!(e, Error::AlreadyReported) {
            let report = error_ui::report_for_runtime_error(&command_display, &e);
            error_ui::print_report(&report, verbose_errors);
        }
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        Cli, CliContext, Commands, ConfigAddTarget, ConfigCommands, LanguageGateAction,
        PrivacyCommands, SkillCommands, UploadCliOptions, first_command_token,
        is_language_command_request, language_command_can_run_picker, language_gate_action,
        language_required_message, legacy_upload_option_error, plain_help_misuse,
        pre_parse_requires_cli_config_file, preprocess_privacy_args,
    };
    use crate::config::{Config, DEFAULT_CUSTOM_URL};
    use crate::output::OutputFormat;
    use crate::{SystemBackendCommands, SystemCommands, handlers};
    use clap::{CommandFactory, Parser};
    use std::ffi::OsString;

    fn os_args(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    #[test]
    fn cli_parses_global_identity_override_flags() {
        let cli = Cli::try_parse_from(["ov", "--account", "acme", "--user", "alice", "ls"])
            .expect("cli should parse");

        assert_eq!(cli.account.as_deref(), Some("acme"));
        assert_eq!(cli.user.as_deref(), Some("alice"));
    }

    #[test]
    fn cli_parses_find_peer_id() {
        let cli = Cli::try_parse_from(["ov", "find", "invoice", "--peer-id", "web-visitor-alice"])
            .expect("find peer id should parse");

        match cli.command {
            Commands::Find { peer_id, .. } => {
                assert_eq!(peer_id.as_deref(), Some("web-visitor-alice"));
            }
            _ => panic!("expected find command"),
        }
    }

    #[test]
    fn cli_parses_find_context_type() {
        let cli =
            Cli::try_parse_from(["ov", "find", "invoice", "--context-type", "memory,resource"])
                .expect("find context type should parse");

        match cli.command {
            Commands::Find { context_type, .. } => {
                assert_eq!(
                    context_type,
                    Some(vec!["memory".to_string(), "resource".to_string()])
                );
            }
            _ => panic!("expected find command"),
        }
    }

    #[test]
    fn cli_parses_search_peer_id() {
        let cli =
            Cli::try_parse_from(["ov", "search", "invoice", "--peer-id", "web-visitor-alice"])
                .expect("search peer id should parse");

        match cli.command {
            Commands::Search { peer_id, .. } => {
                assert_eq!(peer_id.as_deref(), Some("web-visitor-alice"));
            }
            _ => panic!("expected search command"),
        }
    }

    #[test]
    fn cli_parses_search_context_type() {
        let cli = Cli::try_parse_from(["ov", "search", "invoice", "--context-type", "skill"])
            .expect("search context type should parse");

        match cli.command {
            Commands::Search { context_type, .. } => {
                assert_eq!(context_type, Some(vec!["skill".to_string()]));
            }
            _ => panic!("expected search command"),
        }
    }

    #[test]
    fn cli_chat_sender_uses_long_flag_and_session_keeps_short_s() {
        Cli::command().debug_assert();

        let cli = Cli::try_parse_from([
            "ov",
            "chat",
            "-s",
            "session-1",
            "--sender",
            "agent-1",
            "--message",
            "hello",
        ])
        .expect("chat flags should parse without short alias conflicts");

        match cli.command {
            Commands::Chat {
                session, sender, ..
            } => {
                assert_eq!(session.as_deref(), Some("session-1"));
                assert_eq!(sender, "agent-1");
            }
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn cli_parses_system_backend_sync_status() {
        let cli = Cli::try_parse_from(["ov", "system", "backend", "sync-status", "viking://a"])
            .expect("system backend sync-status should parse");

        match cli.command {
            Commands::System { action } => match action {
                SystemCommands::Backend { action } => match action {
                    SystemBackendCommands::SyncStatus { uri } => assert_eq!(uri, "viking://a"),
                    _ => panic!("expected backend sync-status command"),
                },
                _ => panic!("expected system backend command"),
            },
            _ => panic!("expected system command"),
        }
    }

    #[test]
    fn server_commands_require_existing_cli_config() {
        let cli = Cli::try_parse_from(["ov", "ls"]).expect("ls should parse");
        let health = Cli::try_parse_from(["ov", "health"]).expect("health should parse");

        assert!(cli.command.requires_cli_config_file());
        assert!(health.command.requires_cli_config_file());
    }

    #[test]
    fn bare_config_switch_and_version_do_not_require_existing_cli_config() {
        let setup = Cli::try_parse_from(["ov", "config"]).expect("bare config should parse");
        let switch =
            Cli::try_parse_from(["ov", "config", "switch"]).expect("config switch should parse");
        let switch_named = Cli::try_parse_from(["ov", "config", "switch", "prod"])
            .expect("named config switch should parse");
        let version = Cli::try_parse_from(["ov", "version"]).expect("version should parse");

        assert!(!setup.command.requires_cli_config_file());
        assert!(!switch.command.requires_cli_config_file());
        assert!(!switch_named.command.requires_cli_config_file());
        assert!(!version.command.requires_cli_config_file());

        let skills_validate = Cli::try_parse_from(["ov", "skills", "validate", "./skills/foo"])
            .expect("skills validate should parse");
        assert!(!skills_validate.command.requires_cli_config_file());
    }

    #[test]
    fn config_agent_commands_parse_without_secret_value_flags() {
        let add_ov_service = Cli::try_parse_from([
            "ov",
            "config",
            "add",
            "ov-service",
            "--name",
            "prod",
            "--api-key-stdin",
            "--activate",
        ])
        .expect("ov-service add should parse");
        let Commands::Config {
            action:
                Some(ConfigCommands::Add {
                    target: ConfigAddTarget::OvService(ov_service),
                }),
        } = add_ov_service.command
        else {
            panic!("expected ov-service add command");
        };
        assert_eq!(ov_service.name.as_deref(), Some("prod"));
        assert!(ov_service.api_key_stdin);
        assert!(ov_service.activate);

        let add_custom = Cli::try_parse_from([
            "ov",
            "config",
            "add",
            "custom",
            "--url",
            "https://ov.example.com",
            "--api-key-env",
            "OV_KEY",
        ])
        .expect("custom add should parse");
        let Commands::Config {
            action:
                Some(ConfigCommands::Add {
                    target: ConfigAddTarget::Custom(custom),
                }),
        } = add_custom.command
        else {
            panic!("expected custom add command");
        };
        assert_eq!(custom.url.as_deref(), Some("https://ov.example.com"));
        assert_eq!(custom.api_key_env.as_deref(), Some("OV_KEY"));

        let edit = Cli::try_parse_from([
            "ov",
            "config",
            "edit",
            "prod",
            "--clear-api-key",
            "--activate",
        ])
        .expect("config edit should parse");
        let Commands::Config {
            action: Some(ConfigCommands::Edit(edit)),
        } = edit.command
        else {
            panic!("expected edit command");
        };
        assert_eq!(edit.name, "prod");
        assert!(edit.clear_api_key);
        assert!(edit.activate);

        assert!(
            Cli::try_parse_from(["ov", "config", "add", "ov-service", "--api-key", "secret"])
                .is_err(),
            "plain API key flag must stay rejected"
        );
        assert!(
            Cli::try_parse_from(["ov", "config", "add", "cloud", "--api-key-stdin"]).is_err(),
            "old cloud provider subcommand must stay rejected"
        );
        assert!(
            Cli::try_parse_from(["ov", "config", "add", "self-managed"]).is_err(),
            "old self-managed provider subcommand must stay rejected"
        );
        assert!(
            Cli::try_parse_from([
                "ov",
                "config",
                "add",
                "custom",
                "--use-root-key-for-normal-commands",
            ])
            .is_err(),
            "root-as-normal flag is obsolete and must stay rejected"
        );
        assert!(
            Cli::try_parse_from([
                "ov",
                "config",
                "edit",
                "prod",
                "--use-root-key-for-normal-commands",
            ])
            .is_err(),
            "root-as-normal edit flag is obsolete and must stay rejected"
        );
    }

    #[test]
    fn pre_parse_config_gate_catches_incomplete_known_server_commands() {
        for args in [
            &["ov", "find"][..],
            &["ov", "task", "status"],
            &["ov", "task", "watch", "show"],
            &["ov", "config", "validate"],
            &["ov", "config", "show"],
            &["ov", "system", "consistency"],
            &["ov", "system", "backend", "sync-status"],
            &["ov", "admin", "list-accounts"],
        ] {
            assert!(
                pre_parse_requires_cli_config_file(&os_args(args)),
                "{args:?} should require ovcli.conf before Clap positional validation"
            );
        }
    }

    #[test]
    fn pre_parse_config_gate_covers_known_top_level_server_commands() {
        for command in [
            "add-resource",
            "add-skill",
            "ls",
            "tree",
            "mkdir",
            "rm",
            "mv",
            "stat",
            "read",
            "abstract",
            "overview",
            "write",
            "get",
            "find",
            "search",
            "grep",
            "glob",
            "add-memory",
            "relations",
            "link",
            "unlink",
            "export",
            "backup",
            "import",
            "restore",
            "tui",
            "chat",
            "wait",
            "status",
            "health",
            "reindex",
        ] {
            assert!(
                pre_parse_requires_cli_config_file(&os_args(&["ov", command])),
                "{command} should require ovcli.conf before Clap validation"
            );
        }
    }

    #[test]
    fn pre_parse_config_gate_covers_known_group_server_commands() {
        let cases: &[&[&str]] = &[
            &["ov", "config", "show"],
            &["ov", "config", "validate"],
            &["ov", "task", "status"],
            &["ov", "task", "list"],
            &["ov", "task", "watch"],
            &["ov", "task", "watch", "ls"],
            &["ov", "task", "watch", "show"],
            &["ov", "task", "watch", "rm"],
            &["ov", "task", "watch", "pause"],
            &["ov", "task", "watch", "resume"],
            &["ov", "task", "watch", "update"],
            &["ov", "task", "watch", "trigger"],
            &["ov", "admin", "create-account"],
            &["ov", "admin", "list-accounts"],
            &["ov", "admin", "delete-account"],
            &["ov", "admin", "register-user"],
            &["ov", "admin", "list-users"],
            &["ov", "admin", "remove-user"],
            &["ov", "admin", "set-role"],
            &["ov", "admin", "regenerate-key"],
            &["ov", "system", "wait"],
            &["ov", "system", "status"],
            &["ov", "system", "health"],
            &["ov", "system", "consistency"],
            &["ov", "system", "backend"],
            &["ov", "system", "backend", "sync-status"],
            &["ov", "system", "backend", "sync-retry"],
            &["ov", "system", "crypto"],
            &["ov", "system", "crypto", "init-key"],
            &["ov", "session", "new"],
            &["ov", "session", "list"],
            &["ov", "session", "get"],
            &["ov", "session", "get-session-context"],
            &["ov", "session", "get-session-archive"],
            &["ov", "session", "delete"],
            &["ov", "session", "add-message"],
            &["ov", "session", "add-messages"],
            &["ov", "session", "commit"],
            &["ov", "privacy", "categories"],
            &["ov", "privacy", "list"],
            &["ov", "privacy", "get"],
            &["ov", "privacy", "upsert"],
            &["ov", "privacy", "versions"],
            &["ov", "privacy", "version"],
            &["ov", "privacy", "activate"],
            &["ov", "observer", "queue"],
            &["ov", "observer", "vikingdb"],
            &["ov", "observer", "models"],
            &["ov", "observer", "transaction"],
            &["ov", "observer", "retrieval"],
            &["ov", "observer", "filesystem"],
            &["ov", "observer", "system"],
            &["ov", "skills", "add"],
            &["ov", "skills", "list"],
            &["ov", "skills", "ls"],
            &["ov", "skills", "find"],
            &["ov", "skills", "show"],
            &["ov", "skills", "update"],
            &["ov", "skills", "remove"],
            &["ov", "skills", "rm"],
            &["ov", "skills", "delete"],
        ];

        for args in cases {
            assert!(
                pre_parse_requires_cli_config_file(&os_args(args)),
                "{args:?} should require ovcli.conf before Clap validation"
            );
        }

        assert!(
            pre_parse_requires_cli_config_file(&preprocess_privacy_args(os_args(&[
                "ov", "privacy", "skill", "demo",
            ]))),
            "privacy shortcut should require ovcli.conf after preprocessing"
        );
    }

    #[test]
    fn pre_parse_config_gate_respects_aliases_and_valid_help_values() {
        for args in [
            &["ov", "list"][..],
            &["ov", "delete"],
            &["ov", "rename"],
            &["ov", "lang", "en"],
        ] {
            let expected = args[1] != "lang";
            assert_eq!(
                pre_parse_requires_cli_config_file(&os_args(args)),
                expected,
                "{args:?} pre-parse gate mismatch"
            );
        }
    }

    #[test]
    fn pre_parse_config_gate_allows_non_server_and_unknown_commands() {
        for args in [
            &["ov", "con"][..],
            &["ov", "task", "nope"],
            &["ov", "config"],
            &["ov", "config", "switch"],
            &["ov", "config", "switch", "prod"],
            &["ov", "config", "list"],
            &["ov", "config", "delete", "prod"],
            &["ov", "config", "add", "ov-service", "--api-key-stdin"],
            &["ov", "config", "edit", "prod", "--activate"],
            &["ov", "config", "setup-cli"],
            &["ov", "skills", "validate", "./skills/foo"],
            &["ov", "skills", "validate", "./skills/foo", "--strict"],
            &["ov", "version"],
            &["ov", "language", "en"],
            &["ov", "lang", "en"],
        ] {
            assert!(
                !pre_parse_requires_cli_config_file(&os_args(args)),
                "{args:?} should not be gated before Clap parsing"
            );
        }
    }

    #[test]
    fn cli_tree_help_hides_upload_and_admin_only_flags() {
        let err = Cli::command()
            .try_get_matches_from(["ov", "tree", "--help"])
            .expect_err("help should exit through clap error");
        let help = err.to_string();

        assert!(!help.contains("--progress"));
        assert!(!help.contains("--no-progress"));
        assert!(!help.contains("--verbose"));
        assert!(!help.contains("--sudo"));
    }

    #[test]
    fn cli_add_resource_help_shows_upload_flags() {
        let err = Cli::command()
            .try_get_matches_from(["ov", "add-resource", "--help"])
            .expect_err("help should exit through clap error");
        let help = err.to_string();

        assert!(help.contains("--progress"));
        assert!(help.contains("--no-progress"));
        assert!(help.contains("--verbose"));
    }

    #[test]
    fn cli_add_skill_help_shows_upload_flags() {
        let err = Cli::command()
            .try_get_matches_from(["ov", "add-skill", "--help"])
            .expect_err("help should exit through clap error");
        let help = err.to_string();

        assert!(help.contains("--progress"));
        assert!(help.contains("--no-progress"));
        assert!(help.contains("--verbose"));
    }

    #[test]
    fn cli_tree_rejects_upload_flags_after_subcommand() {
        assert!(Cli::try_parse_from(["ov", "tree", "viking://", "--progress"]).is_err());
        assert!(Cli::try_parse_from(["ov", "tree", "viking://", "--no-progress"]).is_err());
        assert!(Cli::try_parse_from(["ov", "tree", "viking://", "--verbose"]).is_err());
    }

    #[test]
    fn cli_parses_sudo_as_hidden_global_flag_after_subcommand() {
        let cli = Cli::try_parse_from(["ov", "tree", "viking://", "--sudo"])
            .expect("sudo should parse anywhere so runtime can show a precise command error");

        assert!(cli.sudo);
    }

    #[test]
    fn cli_parses_upload_flags_on_upload_commands() {
        let add_resource = Cli::try_parse_from([
            "ov",
            "add-resource",
            "./README.md",
            "--progress",
            "--verbose",
        ])
        .expect("add-resource upload flags should parse");
        match add_resource.command {
            Commands::AddResource { upload_options, .. } => {
                assert!(upload_options.progress);
                assert!(upload_options.verbose);
            }
            _ => panic!("expected add-resource command"),
        }

        let add_skill = Cli::try_parse_from(["ov", "add-skill", "./skill", "--no-progress"])
            .expect("add-skill upload flags should parse");
        match add_skill.command {
            Commands::AddSkill { upload_options, .. } => {
                assert!(upload_options.no_progress);
            }
            _ => panic!("expected add-skill command"),
        }

        assert!(Cli::try_parse_from(["ov", "skills", "add", "./skill", "--progress"]).is_err());
        assert!(Cli::try_parse_from(["ov", "skills", "update", "--progress"]).is_err());
    }

    #[test]
    fn cli_parses_skills_command_group() {
        let list = Cli::try_parse_from(["ov", "skills", "list", "--limit", "25"])
            .expect("skills list should parse");
        match list.command {
            Commands::Skills {
                action: SkillCommands::List { node_limit },
            } => assert_eq!(node_limit, 25),
            _ => panic!("expected skills list"),
        }

        let find = Cli::try_parse_from([
            "ov",
            "skills",
            "find",
            "review code",
            "--threshold",
            "0.4",
            "--level",
            "0,1",
        ])
        .expect("skills find should parse");
        match find.command {
            Commands::Skills {
                action:
                    SkillCommands::Find {
                        query,
                        threshold,
                        level,
                        ..
                    },
            } => {
                assert_eq!(query, "review code");
                assert_eq!(threshold, Some(0.4));
                assert_eq!(level, Some(vec![0, 1]));
            }
            _ => panic!("expected skills find"),
        }

        let update =
            Cli::try_parse_from(["ov", "skills", "update", "code-review", "--wait", "--yes"])
                .expect("skills update should parse");
        match update.command {
            Commands::Skills {
                action:
                    SkillCommands::Update {
                        skills, wait, yes, ..
                    },
            } => {
                assert_eq!(skills, vec!["code-review"]);
                assert!(wait);
                assert!(yes);
            }
            _ => panic!("expected skills update"),
        }

        let add_selected = Cli::try_parse_from([
            "ov",
            "skills",
            "add",
            "https://github.com/acme/skills.git",
            "--skill",
            "foo",
            "bar",
            "--list",
            "--yes",
        ])
        .expect("skills add RFC flags should parse");
        match add_selected.command {
            Commands::Skills {
                action:
                    SkillCommands::Add {
                        source,
                        skills,
                        list,
                        yes,
                        ..
                    },
            } => {
                assert_eq!(source, "https://github.com/acme/skills.git");
                assert_eq!(skills, vec!["foo", "bar"]);
                assert!(list);
                assert!(yes);
            }
            _ => panic!("expected skills add"),
        }

        let show = Cli::try_parse_from([
            "ov",
            "skills",
            "show",
            "code-review",
            "--level",
            "2",
            "--files",
            "--source",
            "--format",
            "json",
        ])
        .expect("skills show RFC flags should parse");
        match show.command {
            Commands::Skills {
                action:
                    SkillCommands::Show {
                        level,
                        files,
                        source,
                        format,
                        ..
                    },
            } => {
                assert_eq!(level, Some(2));
                assert!(files);
                assert!(source);
                assert_eq!(format.as_deref(), Some("json"));
            }
            _ => panic!("expected skills show"),
        }

        let show_global_output =
            Cli::try_parse_from(["ov", "skills", "show", "code-review", "-o", "json"])
                .expect("skills show should accept global -o after the subcommand");
        assert_eq!(show_global_output.output, OutputFormat::Json);

        let remove = Cli::try_parse_from(["ov", "skills", "remove", "foo", "bar", "--yes"])
            .expect("skills remove --yes should parse");
        match remove.command {
            Commands::Skills {
                action: SkillCommands::Remove { skills, yes, all },
            } => {
                assert_eq!(skills, vec!["foo", "bar"]);
                assert!(yes);
                assert!(!all);
            }
            _ => panic!("expected skills remove"),
        }

        let validate =
            Cli::try_parse_from(["ov", "skills", "validate", "./skills/foo", "--strict"])
                .expect("skills validate --strict should parse");
        match validate.command {
            Commands::Skills {
                action: SkillCommands::Validate { path, strict },
            } => {
                assert_eq!(path, "./skills/foo");
                assert!(strict);
            }
            _ => panic!("expected skills validate"),
        }
    }

    #[test]
    fn cli_keeps_legacy_pre_command_upload_flags() {
        let cli = Cli::try_parse_from([
            "ov",
            "--progress",
            "--verbose",
            "add-resource",
            "./README.md",
        ])
        .expect("legacy pre-command upload flags should still parse");

        assert!(cli.progress);
        assert!(cli.verbose);
    }

    #[test]
    fn legacy_pre_command_upload_flags_only_allow_upload_commands() {
        let upload_options = UploadCliOptions {
            progress: true,
            no_progress: false,
            verbose: false,
        };

        let tree = Cli::try_parse_from(["ov", "--progress", "tree", "viking://"])
            .expect("hidden legacy flag still parses before runtime validation");
        assert_eq!(
            legacy_upload_option_error(upload_options, &tree.command),
            Some(
                "--progress, --no-progress, and --verbose are only supported for add-resource and add-skill."
            )
        );

        let add_resource = Cli::try_parse_from(["ov", "--progress", "add-resource", "./README.md"])
            .expect("legacy pre-command upload flags should parse for add-resource");
        assert!(legacy_upload_option_error(upload_options, &add_resource.command).is_none());

        let skills_add = Cli::try_parse_from(["ov", "--progress", "skills", "add", "./skill"])
            .expect("hidden legacy flag still parses before runtime validation");
        assert_eq!(
            legacy_upload_option_error(upload_options, &skills_add.command),
            Some(
                "--progress, --no-progress, and --verbose are only supported for add-resource and add-skill."
            )
        );
    }

    #[test]
    fn cli_parses_sudo_before_admin_command() {
        let cli = Cli::try_parse_from(["ov", "--sudo", "admin", "list-accounts"])
            .expect("pre-command sudo should parse");

        assert!(cli.sudo);
    }

    #[test]
    fn cli_parses_sudo_after_admin_command() {
        let cli = Cli::try_parse_from(["ov", "admin", "list-accounts", "--sudo"])
            .expect("post-command sudo should parse");

        assert!(cli.sudo);
    }

    #[test]
    fn cli_config_without_subcommand_parses_as_setup_entrypoint() {
        Cli::try_parse_from(["ov", "config"]).expect("bare config command should parse");
    }

    #[test]
    fn cli_config_single_purpose_subcommands_still_parse() {
        for subcommand in ["show", "validate", "switch"] {
            Cli::try_parse_from(["ov", "config", subcommand])
                .unwrap_or_else(|_| panic!("config {subcommand} should parse"));
        }
    }

    #[test]
    fn cli_config_rejects_removed_setup_cli_subcommand() {
        assert!(Cli::try_parse_from(["ov", "config", "setup-cli"]).is_err());
    }

    #[test]
    fn cli_language_command_and_alias_parse() {
        let cli = Cli::try_parse_from(["ov", "language", "zh-CN"])
            .expect("language command should parse");
        match cli.command {
            Commands::Language { language } => assert_eq!(language.as_deref(), Some("zh-CN")),
            _ => panic!("expected language command"),
        }

        let cli = Cli::try_parse_from(["ov", "lang"]).expect("language alias should parse");
        match cli.command {
            Commands::Language { language } => assert!(language.is_none()),
            _ => panic!("expected language command"),
        }
    }

    #[test]
    fn language_gate_detects_language_command_after_global_flags() {
        assert!(is_language_command_request(&os_args(&["ov", "language"])));
        assert!(is_language_command_request(&os_args(&[
            "ov", "lang", "zh-CN"
        ])));
        assert!(is_language_command_request(&os_args(&[
            "ov", "--output", "json", "language", "en",
        ])));
        assert!(is_language_command_request(&os_args(&[
            "ov",
            "--output=json",
            "--compact",
            "false",
            "lang",
        ])));
        assert!(is_language_command_request(&os_args(&[
            "ov",
            "--compact",
            "lang",
        ])));
        assert!(!is_language_command_request(&os_args(&["ov", "status"])));
        assert!(!is_language_command_request(&os_args(&["ov", "--help"])));
    }

    #[test]
    fn language_gate_action_continues_prompts_or_exits() {
        assert_eq!(
            language_gate_action(&os_args(&["ov", "status"]), true, false),
            LanguageGateAction::Continue
        );
        assert_eq!(
            language_gate_action(&os_args(&["ov", "language"]), false, false),
            LanguageGateAction::Continue
        );
        assert_eq!(
            language_gate_action(&os_args(&["ov", "status"]), false, true),
            LanguageGateAction::Prompt
        );
        assert_eq!(
            language_gate_action(&os_args(&["ov", "status"]), false, false),
            LanguageGateAction::ExitNonInteractive
        );
    }

    #[test]
    fn language_gate_allows_config_agent_commands_without_saved_language() {
        for args in [
            &["ov", "config", "add", "ov-service", "--api-key-stdin"][..],
            &["ov", "config", "add", "custom"],
            &["ov", "config", "edit", "prod", "--activate"],
            &["ov", "config", "delete", "prod"],
            &["ov", "config", "list"],
            &["ov", "config", "switch", "prod"],
        ] {
            assert_eq!(
                language_gate_action(&os_args(args), false, false),
                LanguageGateAction::Continue,
                "{args:?} should bypass first-run language selection"
            );
        }

        assert_eq!(
            language_gate_action(&os_args(&["ov", "config"]), false, false),
            LanguageGateAction::ExitNonInteractive
        );
    }

    #[test]
    fn language_gate_message_points_to_explicit_language_commands() {
        let message = language_required_message();

        assert!(message.contains("ov language en"));
        assert!(message.contains("ov language zh-CN"));
    }

    #[test]
    fn language_command_without_value_requires_interactive_picker() {
        assert!(language_command_can_run_picker(true, false));
        assert!(language_command_can_run_picker(false, true));
        assert!(!language_command_can_run_picker(false, false));
    }

    #[test]
    fn first_command_token_skips_global_flags() {
        assert_eq!(
            first_command_token(&os_args(&[
                "ov",
                "--output",
                "json",
                "--account=acme",
                "--sudo",
                "admin",
            ]))
            .as_deref(),
            Some("admin")
        );
        assert_eq!(
            first_command_token(&os_args(&["ov", "--help"])).as_deref(),
            None
        );
    }

    #[test]
    fn cli_status_parses_verbose_flag() {
        let cli =
            Cli::try_parse_from(["ov", "status", "--verbose"]).expect("status verbose parses");

        match cli.command {
            Commands::Status { verbose } => assert!(verbose),
            _ => panic!("expected status command"),
        }
    }

    #[test]
    fn plain_help_is_rejected_for_top_level_group_and_structured_leaf_commands() {
        for (args, expected_help) in [
            (vec!["ov", "help"], "ov --help"),
            (vec!["ov", "config", "help"], "ov config --help"),
            (vec!["ov", "task", "help"], "ov task --help"),
            (vec!["ov", "admin", "help"], "ov admin --help"),
            (vec!["ov", "ls", "help"], "ov ls --help"),
            (vec!["ov", "tree", "help"], "ov tree --help"),
            (vec!["ov", "read", "help"], "ov read --help"),
            (
                vec!["ov", "task", "status", "help"],
                "ov task status --help",
            ),
            (
                vec!["ov", "system", "consistency", "help"],
                "ov system consistency --help",
            ),
            (
                vec!["ov", "system", "backend", "sync-status", "help"],
                "ov system backend sync-status --help",
            ),
        ] {
            let misuse = plain_help_misuse(&os_args(&args))
                .unwrap_or_else(|| panic!("{args:?} should be plain-help misuse"));
            assert_eq!(misuse.help_command, expected_help);
        }
    }

    #[test]
    fn plain_help_is_allowed_as_free_form_user_input() {
        for args in [
            vec!["ov", "find", "help"],
            vec!["ov", "search", "help"],
            vec!["ov", "grep", "help"],
            vec!["ov", "glob", "help"],
            vec!["ov", "add-memory", "help"],
            vec!["ov", "add-resource", "help"],
            vec!["ov", "add-skill", "help"],
        ] {
            assert!(
                plain_help_misuse(&os_args(&args)).is_none(),
                "{args:?} should remain normal user input"
            );
        }
    }

    #[test]
    fn plain_help_ignores_option_values() {
        assert!(
            plain_help_misuse(&os_args(&[
                "ov",
                "read",
                "--account",
                "help",
                "viking://resource",
            ]))
            .is_none()
        );
        assert!(plain_help_misuse(&os_args(&["ov", "grep", "--uri", "help", "TODO",])).is_none());

        for args in [
            &["ov", "mkdir", "--description", "help", "viking://notes"][..],
            &[
                "ov",
                "write",
                "viking://notes/todo.md",
                "--from-file",
                "help",
            ],
            &[
                "ov",
                "session",
                "add-message",
                "abc",
                "--peer-id",
                "help",
                "--role",
                "user",
                "--content",
                "hi",
            ],
            &[
                "ov",
                "privacy",
                "upsert",
                "cat",
                "target",
                "--values-json",
                "help",
            ],
            &["ov", "admin", "create-account", "acct", "--admin", "help"],
            &["ov", "config", "add", "custom", "--url", "help"],
            &["ov", "task", "list", "--status", "help"],
            &[
                "ov",
                "system",
                "crypto",
                "init-key",
                "--output-file",
                "help",
            ],
        ] {
            assert!(
                plain_help_misuse(&os_args(args)).is_none(),
                "{args:?} should treat help as an option value"
            );
        }
    }

    #[test]
    fn cli_context_overrides_identity_from_cli_flags() {
        let config = Config {
            url: DEFAULT_CUSTOM_URL.to_string(),
            api_key: Some("test-key".to_string()),
            root_api_key: None,
            account: Some("from-config-account".to_string()),
            user: Some("from-config-user".to_string()),
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
            show_progress: false,
            verbose: false,
            upload: Default::default(),
            extra_headers: None,
            profile: false,
        };

        let ctx = CliContext::from_config(
            config,
            OutputFormat::Json,
            true,
            Some("from-cli-account".to_string()),
            Some("from-cli-user".to_string()),
            false,
            None,
            None,
            None,
        );

        assert_eq!(ctx.config.account.as_deref(), Some("from-cli-account"));
        assert_eq!(ctx.config.user.as_deref(), Some("from-cli-user"));
    }

    #[test]
    fn cli_context_uses_root_api_key_with_sudo() {
        let config = Config {
            url: DEFAULT_CUSTOM_URL.to_string(),
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: None,
            user: None,
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
            show_progress: false,
            verbose: false,
            profile: false,
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
            false,
            None,
            None,
            None,
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
            true,
            None,
            None,
            None,
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
    fn cli_import_rejects_removed_vectorize_flag() {
        let result = Cli::try_parse_from([
            "ov",
            "import",
            "./exports/demo.ovpack",
            "viking://resources/imported/",
            "--no-vectorize",
        ]);

        assert!(
            result.is_err(),
            "removed import vectorize flag should not parse"
        );
    }

    #[test]
    fn cli_import_rejects_removed_force_flag() {
        let result = Cli::try_parse_from([
            "ov",
            "import",
            "./exports/demo.ovpack",
            "viking://resources/imported/",
            "--force",
        ]);

        assert!(
            result.is_err(),
            "removed import force flag should not parse"
        );
    }

    #[test]
    fn cli_parses_reindex_command() {
        let result = Cli::try_parse_from([
            "ov",
            "reindex",
            "viking://resources/demo",
            "--mode",
            "semantic_and_vectors",
            "--wait=false",
        ]);

        assert!(result.is_ok(), "reindex command should parse");
    }

    #[test]
    fn append_time_filter_params_only_emits_after_and_before() {
        let mut params = Vec::new();
        let after = Some("7d".to_string());
        let before = Some("2026-03-12".to_string());

        handlers::append_time_filter_params(&mut params, after.as_deref(), before.as_deref());

        assert_eq!(params, vec!["--after 7d", "--before 2026-03-12"]);
    }

    #[test]
    fn preprocess_key_dynamic_flag_to_static_form() {
        let args = vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("upsert"),
            OsString::from("skill"),
            OsString::from("demo"),
            OsString::from("--key-api_key"),
            OsString::from("secret-v1"),
        ];

        let converted = preprocess_privacy_args(args);
        let converted_strs: Vec<String> = converted
            .into_iter()
            .map(|s| s.to_string_lossy().to_string())
            .collect();

        assert_eq!(
            converted_strs,
            vec![
                "ov",
                "privacy",
                "upsert",
                "skill",
                "demo",
                "--key",
                "api_key=secret-v1",
            ]
        );
    }

    #[test]
    fn cli_parses_privacy_upsert_with_key_dynamic_flag() {
        let cli = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("upsert"),
            OsString::from("skill"),
            OsString::from("demo"),
            OsString::from("--key-api_key"),
            OsString::from("secret-v2"),
        ]));

        match cli.command {
            Commands::Privacy { action } => match action {
                PrivacyCommands::Upsert {
                    category,
                    target_key,
                    key,
                    ..
                } => {
                    assert_eq!(category, "skill");
                    assert_eq!(target_key, "demo");
                    assert_eq!(key, vec!["api_key=secret-v2"]);
                }
                _ => panic!("expected privacy upsert"),
            },
            _ => panic!("expected privacy command"),
        }
    }

    #[test]
    fn cli_parses_privacy_shortcut_as_get() {
        let cli = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("skill"),
            OsString::from("demo"),
        ]));

        match cli.command {
            Commands::Privacy { action } => match action {
                PrivacyCommands::Get {
                    category,
                    target_key,
                } => {
                    assert_eq!(category, "skill");
                    assert_eq!(target_key, "demo");
                }
                _ => panic!("expected privacy get"),
            },
            _ => panic!("expected privacy command"),
        }
    }
}
