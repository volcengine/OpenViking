//! Command-line schema and command capability rules.

mod admin;
mod config;
mod mcps;
pub(crate) mod preprocess;
mod privacy;
mod resources;
mod session;
mod skills;
mod system;
mod upload;

pub(crate) use admin::AdminCommands;
pub(crate) use config::{
    ConfigAddCustomArgs, ConfigAddOvServiceArgs, ConfigAddTarget, ConfigCommands, ConfigDeleteArgs,
    ConfigEditArgs,
};
pub(crate) use mcps::McpCommands;
pub(crate) use privacy::PrivacyCommands;
pub(crate) use resources::{AclCommands, AttrsCommands, CompileCommands};
pub(crate) use session::{SessionCommands, SessionConfigCommands};
pub(crate) use skills::{SkillAddArgs, SkillCommands};
pub(crate) use system::{
    ObserverCommands, SnapshotCmd, SystemCommands, TaskCommands, WatchCommands,
};
pub(crate) use upload::{UploadCliOptions, legacy_upload_option_error};

use crate::output::OutputFormat;
use clap::{ArgAction, Parser, Subcommand};

const MAX_TIME_DECAY_DURATION_DAYS: u128 = 3000 * 365;

/// Return the unchanged configuration name or a CLI usage error before contacting the server.
fn parse_compile_name(value: &str) -> Result<String, &'static str> {
    if (1..=64).contains(&value.len())
        && !value.starts_with('-')
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    {
        return Ok(value.to_owned());
    }
    Err(
        "Use 1–64 ASCII letters, digits, underscores or hyphens. The first character cannot be a hyphen.",
    )
}

fn parse_event_time_decay_protection(value: &str) -> std::result::Result<String, String> {
    if value == "0" {
        return Ok(value.to_string());
    }
    let Some((amount, unit)) = value.split_at_checked(value.len().saturating_sub(1)) else {
        return Err("must be '0' or a non-negative integer followed by m, h, or d".to_string());
    };
    if amount.is_empty() || !amount.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("must be '0' or a non-negative integer followed by m, h, or d".to_string());
    }
    let amount = amount
        .parse::<u128>()
        .map_err(|_| "exceeds the maximum duration of 1095000d".to_string())?;
    let duration_minutes = match unit {
        "m" => amount,
        "h" => amount.saturating_mul(60),
        "d" => amount.saturating_mul(24 * 60),
        _ => {
            return Err("must be '0' or a non-negative integer followed by m, h, or d".to_string());
        }
    };
    if duration_minutes > MAX_TIME_DECAY_DURATION_DAYS * 24 * 60 {
        return Err("exceeds the maximum duration of 1095000d".to_string());
    }
    Ok(value.to_string())
}

#[derive(Parser)]
#[command(name = "openviking")]
#[command(about = "OpenViking - An Agent-native context database")]
#[command(version = env!("OPENVIKING_CLI_VERSION"))]
#[command(arg_required_else_help = true)]
pub(crate) struct Cli {
    /// Choose human table output or machine-readable JSON
    #[arg(
        short,
        long,
        value_enum,
        global = true,
        hide = true,
        value_name = "table|json"
    )]
    pub(crate) output: Option<OutputFormat>,

    /// Use compact table/JSON rendering
    #[arg(
        short,
        long,
        global = true,
        default_value = "true",
        default_missing_value = "true",
        hide = true,
        num_args = 0..=1,
        require_equals = true,
        action = ArgAction::Set,
        value_name = "bool"
    )]
    pub(crate) compact: bool,

    /// Override X-OpenViking-Account for this command
    #[arg(long, global = true, hide = true)]
    pub(crate) account: Option<String>,

    /// Override X-OpenViking-User for this command
    #[arg(long, global = true, hide = true)]
    pub(crate) user: Option<String>,

    /// Peer actor scope to send as X-OpenViking-Actor-Peer
    #[arg(long = "actor-peer-id", global = true, hide = true)]
    pub(crate) actor_peer_id: Option<String>,

    /// Use root API key for admin, system, reindex, and task status/list commands
    #[arg(long, global = true, hide = true)]
    pub(crate) sudo: bool,

    /// Enable HTTP request profiling for this command
    #[arg(long, global = true, hide = true)]
    pub(crate) profile: bool,

    /// Show upload progress (legacy pre-command placement; prefer command-local --progress)
    #[arg(long, hide = true)]
    pub(crate) progress: bool,

    /// Disable upload progress (legacy pre-command placement; prefer command-local --no-progress)
    #[arg(long = "no-progress", hide = true, conflicts_with = "progress")]
    pub(crate) no_progress: bool,

    /// Enable upload diagnostics (legacy pre-command placement; prefer command-local --verbose)
    #[arg(short, long, hide = true)]
    pub(crate) verbose: bool,

    #[command(subcommand)]
    pub(crate) command: Commands,
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
pub(crate) enum Commands {
    // --- Data Operations ---
    /// [Data] Add resources into OpenViking
    AddResource {
        /// Local path or URL to import
        #[arg(
            value_name = "path-or-url",
            required_unless_present = "manifest",
            conflicts_with = "manifest"
        )]
        path: Option<String>,
        /// Apply an OpenViking Assets manifest (openviking-assets/1): create or sync every selected
        /// asset. Run options go into --args (supported keys: catalog, dry_run, skip_failed)
        #[arg(
            short = 'm',
            long = "manifest",
            value_name = "file",
            help_heading = "Common options",
            conflicts_with_all = [
                "add_type", "to", "parent", "parent_auto_create",
                "strict_mode", "ignore_dirs", "include", "exclude",
                "no_directly_upload_media", "tags", "tag_mode", "acl",
                "reason", "instruction"
            ]
        )]
        manifest: Option<String>,
        /// Explicit Connector source type (e.g. "tos", "git"). Routes the import
        /// through the Connector integration (must be enabled server-side); the
        /// path is sent verbatim and never treated as a local file. Requires --to
        /// and cannot be combined with --manifest, --parent, or
        /// --parent-auto-create
        #[arg(
            long = "add-type",
            value_name = "type",
            requires = "to",
            conflicts_with_all = ["manifest", "parent", "parent_auto_create"],
            help_heading = "Common options"
        )]
        add_type: Option<String>,
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
        #[arg(long, value_name = "minutes", help_heading = "Advanced options")]
        watch_interval: Option<f64>,
        /// Resource processing mode
        #[arg(
            long = "processing-mode",
            default_value = "semantic_and_vectors",
            value_parser = ["semantic_and_vectors", "vectors_only"],
            help_heading = "Advanced options"
        )]
        processing_mode: String,
        /// Extra options as key:value pairs or a JSON object. With a path/URL:
        /// parser-specific import options sent to the server, e.g.
        /// --args feishu_access_token:u-xxx. With --manifest: run options consumed
        /// locally, e.g. --args dry_run:true (supported keys: catalog, dry_run, skip_failed)
        #[arg(long = "args")]
        resource_args: Option<String>,
        /// Comma-separated k=v retrieval tags to apply after import
        #[arg(
            long = "tags",
            value_delimiter = ',',
            value_name = "k=v",
            help_heading = "Common options"
        )]
        tags: Vec<String>,
        /// Tag update mode; clear does not require --tags
        #[arg(
            long = "tag-mode",
            default_value = "replace",
            value_parser = ["replace", "append", "clear"],
            help_heading = "Common options"
        )]
        tag_mode: String,
        #[command(flatten)]
        upload_options: UploadCliOptions,
        /// ACL JSON, for example {"acl_mode":"restricted","entries":[]}
        #[arg(long, value_parser = |s: &str| serde_json::from_str::<serde_json::Value>(s))]
        acl: Option<serde_json::Value>,
    },
    /// [Data] Add skills from a source (same as `skills add`)
    AddSkill(SkillAddArgs),
    /// [Data] Manage installed skills
    Skills {
        #[command(subcommand)]
        action: SkillCommands,
    },
    /// [Data] Manage remote MCP servers and tools
    Mcps {
        #[command(subcommand)]
        action: McpCommands,
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
        /// Include directory L0 abstracts (defaults to the selected output mode)
        #[arg(
            long = "include-abstract",
            default_missing_value = "true",
            num_args = 0..=1,
            require_equals = true,
            action = ArgAction::Set,
            value_name = "bool",
            help_heading = "Output options"
        )]
        include_abstract: Option<bool>,
        /// Include directory L1 overviews
        #[arg(
            long = "include-overview",
            default_missing_value = "true",
            num_args = 0..=1,
            require_equals = true,
            action = ArgAction::Set,
            value_name = "bool",
            help_heading = "Output options"
        )]
        include_overview: Option<bool>,
        /// Maximum overview content length
        #[arg(
            long = "overview-limit",
            default_value = "4000",
            value_parser = clap::value_parser!(i32).range(1..),
            value_name = "n",
            help_heading = "Advanced options"
        )]
        overview_limit: i32,
        /// Show all hidden files
        #[arg(short, long, help_heading = "Common options")]
        all: bool,
        /// Maximum number of nodes to list
        #[arg(
            long = "node-limit",
            short = 'n',
            default_value = "256",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Number of visible entries to skip
        #[arg(
            long,
            default_value = "0",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        offset: i32,
        /// Maximum number of visible entries to return
        #[arg(
            long,
            value_parser = clap::value_parser!(i32).range(1..),
            value_name = "n",
            help_heading = "Common options"
        )]
        limit: Option<i32>,
        /// Sort entries by name or modification time
        #[arg(long, value_parser = ["name", "mtime"], value_name = "field", help_heading = "Common options")]
        sort_by: Option<String>,
        /// Sort direction
        #[arg(
            long,
            requires = "sort_by",
            value_parser = ["asc", "desc"],
            value_name = "order",
            help_heading = "Common options"
        )]
        sort_order: Option<String>,
        /// Comma-separated fields to display (name,uri,path,type,size,mode,mtime,locked,id,count,tags,abstract,overview)
        #[arg(
            short = 'f',
            long = "fields",
            value_delimiter = ',',
            value_name = "FIELDS",
            help_heading = "Output options"
        )]
        fields: Option<Vec<String>>,
        /// Comma-separated k=v retrieval tags; all tags must match
        #[arg(
            long = "tags",
            value_delimiter = ',',
            value_name = "k=v",
            help_heading = "Common options"
        )]
        tags: Vec<String>,
    },
    /// [Data] Get directory tree
    Tree {
        /// Viking URI to get tree for
        #[arg(value_name = "uri")]
        uri: String,
        /// Maximum returned abstract length
        #[arg(
            long = "abs-limit",
            short = 'l',
            default_value = "128",
            value_name = "n",
            help_heading = "Advanced options"
        )]
        abs_limit: i32,
        /// Include directory L0 abstracts (defaults to the selected output mode)
        #[arg(
            long = "include-abstract",
            default_missing_value = "true",
            num_args = 0..=1,
            require_equals = true,
            action = ArgAction::Set,
            value_name = "bool",
            help_heading = "Output options"
        )]
        include_abstract: Option<bool>,
        /// Include directory L1 overviews
        #[arg(
            long = "include-overview",
            default_missing_value = "true",
            num_args = 0..=1,
            require_equals = true,
            action = ArgAction::Set,
            value_name = "bool",
            help_heading = "Output options"
        )]
        include_overview: Option<bool>,
        /// Maximum overview content length
        #[arg(
            long = "overview-limit",
            default_value = "4000",
            value_parser = clap::value_parser!(i32).range(1..),
            value_name = "n",
            help_heading = "Advanced options"
        )]
        overview_limit: i32,
        /// Show all hidden files
        #[arg(short, long, help_heading = "Common options")]
        all: bool,
        /// Only include directories
        #[arg(long = "directories-only", help_heading = "Common options")]
        directories_only: bool,
        /// Maximum number of nodes to list
        #[arg(
            long = "node-limit",
            short = 'n',
            default_value = "256",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Number of visible entries to skip
        #[arg(
            long,
            default_value = "0",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        offset: i32,
        /// Maximum number of visible entries to return
        #[arg(
            long,
            value_parser = clap::value_parser!(i32).range(1..),
            value_name = "n",
            help_heading = "Common options"
        )]
        limit: Option<i32>,
        /// Maximum depth level to traverse (default: 3)
        #[arg(
            short = 'L',
            long = "level-limit",
            default_value = "3",
            value_name = "n",
            help_heading = "Common options"
        )]
        level_limit: i32,
        /// Simple path output (just paths, no tree formatting)
        #[arg(short, long, help_heading = "Common options")]
        simple: bool,
        /// Comma-separated fields to display (name,uri,path,type,size,mode,mtime,locked,id,count,tags,abstract,overview)
        #[arg(
            short = 'f',
            long = "fields",
            value_delimiter = ',',
            value_name = "FIELDS",
            help_heading = "Output options"
        )]
        fields: Option<Vec<String>>,
        /// Comma-separated k=v retrieval tags; all tags must match
        #[arg(
            long = "tags",
            value_delimiter = ',',
            value_name = "k=v",
            help_heading = "Common options"
        )]
        tags: Vec<String>,
    },
    /// [Data] Create directory
    Mkdir {
        /// Directory URI to create
        #[arg(value_name = "uri")]
        uri: String,
        /// Initial directory description
        #[arg(long, value_name = "text", help_heading = "Common options")]
        description: Option<String>,
        /// ACL JSON, for example {"acl_mode":"restricted","entries":[]}
        #[arg(long, value_parser = |s: &str| serde_json::from_str::<serde_json::Value>(s))]
        acl: Option<serde_json::Value>,
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
        /// Wait until semantic refresh is complete
        #[arg(long, help_heading = "Common options")]
        wait: bool,
    },
    /// [Data] Copy a file or directory
    Cp {
        /// Source URI
        #[arg(value_name = "source")]
        from_uri: String,
        /// Target URI
        #[arg(value_name = "target")]
        to_uri: String,
        /// Copy a directory recursively
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
    /// [Data] Get logical extended attributes
    Attrs {
        #[command(subcommand)]
        action: AttrsCommands,
    },
    /// [Data] Manage resource ACL
    Acl {
        #[command(subcommand)]
        action: AclCommands,
    },
    /// [Data] Read file content (Level 2)
    Read {
        /// Viking URI
        #[arg(value_name = "uri")]
        uri: String,
        /// Starting line offset (0-indexed)
        #[arg(long, default_value_t = 0, value_parser = clap::value_parser!(i64).range(0..), help_heading = "Common options")]
        offset: i64,
        /// Number of lines to read; -1 reads to the end
        #[arg(long, default_value_t = -1, allow_negative_numbers = true, value_parser = clap::value_parser!(i64).range(-1..), help_heading = "Common options")]
        limit: i64,
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
        #[arg(long, help_heading = "Advanced options")]
        use_snapshot: bool,
        /// Content post-write processing mode
        #[arg(
            long = "processing-mode",
            default_value = "semantic_and_vectors",
            value_parser = ["semantic_and_vectors", "vectors_only"],
            help_heading = "Advanced options"
        )]
        processing_mode: String,
        /// Comma-separated k=v retrieval tags to write with the content
        #[arg(long = "tags", value_delimiter = ',')]
        tags: Vec<String>,
        /// Tag update mode; clear does not require --tags
        #[arg(long = "tag-mode", default_value = "replace", value_parser = ["replace", "append", "clear"])]
        tag_mode: String,
        /// ACL JSON, for example {"acl_mode":"restricted","entries":[]}
        #[arg(long, value_parser = |s: &str| serde_json::from_str::<serde_json::Value>(s))]
        acl: Option<serde_json::Value>,
    },
    /// [Data] Update explicit retrieval tags metadata for a file or directory
    #[command(hide = true)]
    SetTags {
        /// Viking URI
        uri: String,
        /// Comma-separated k=v tags, e.g. env=prod,team=search
        #[arg(long = "tags", value_delimiter = ',')]
        tags: Vec<String>,
        /// Tag update mode: replace, append, or clear; append supports k=v, k+=number, k-=number
        #[arg(long, default_value = "replace")]
        mode: String,
        /// Recursively update descendant files and semantic nodes when target is a directory
        #[arg(long, default_value = "false")]
        recursive: bool,
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
        query: Option<String>,
        /// Image query: local path, data URI, HTTP URL, or viking:// URI
        #[arg(
            long = "image",
            value_name = "path|uri",
            help_heading = "Common options"
        )]
        image: Option<String>,
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
            value_parser = clap::value_parser!(i32).range(0..),
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
        /// Only include results matching all of these explicit tags
        #[arg(long = "tags", value_delimiter = ',')]
        tags: Option<Vec<String>>,
        /// Include the full visible content for every matched URI
        #[arg(long, help_heading = "Advanced options")]
        read_content: bool,
        /// Enable event decay with protection duration: 0 (immediate) or Xm/Xh/Xd
        #[arg(
            long,
            alias = "events_time_decay_protection",
            value_parser = parse_event_time_decay_protection,
            help_heading = "Advanced options"
        )]
        events_time_decay_protection: Option<String>,
    },
    /// [Experimental][Data] Run context-aware retrieval
    Search {
        /// Search query
        #[arg(value_name = "query")]
        query: Option<String>,
        /// Image query: local path, data URI, HTTP URL, or viking:// URI
        #[arg(
            long = "image",
            value_name = "path|uri",
            help_heading = "Common options"
        )]
        image: Option<String>,
        /// Retrieval type
        #[arg(
            long,
            default_value = "semantic",
            value_parser = ["semantic", "keywords"],
            help_heading = "Common options"
        )]
        search_type: String,
        /// Return ranked hits (list) or assembled context (context)
        #[arg(
            long,
            default_value = "list",
            value_parser = ["list", "context"],
            value_name = "list|context",
            help_heading = "Common options"
        )]
        mode: String,
        /// Target URI (list mode only)
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
            value_parser = clap::value_parser!(i32).range(0..),
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
        /// Filter levels (0=abstract, 1=overview, 2=file); ignored in context mode
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
        /// Only include results matching all of these explicit tags
        #[arg(long = "tags", value_delimiter = ',')]
        tags: Option<Vec<String>>,
        /// Include the full visible content for every matched URI (list mode only)
        #[arg(long, help_heading = "Advanced options")]
        read_content: bool,
        /// Enable event decay with protection duration: 0 (immediate) or Xm/Xh/Xd
        #[arg(
            long,
            alias = "events_time_decay_protection",
            value_parser = parse_event_time_decay_protection,
            help_heading = "Advanced options"
        )]
        events_time_decay_protection: Option<String>,
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
        /// Number of lines to show after each match
        #[arg(
            short = 'a',
            long = "after-context",
            default_value = "0",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        after_context: i32,
        /// Number of lines to show before each match
        #[arg(
            short = 'b',
            long = "before-context",
            default_value = "0",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        before_context: i32,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "256",
            value_parser = clap::value_parser!(i32).range(0..),
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
        /// Comma-separated k=v retrieval tags; all tags must match
        #[arg(
            long = "tags",
            value_delimiter = ',',
            value_name = "k=v",
            help_heading = "Common options"
        )]
        tags: Vec<String>,
        /// Fields to include in output (currently: tags)
        #[arg(
            short = 'f',
            long = "fields",
            value_delimiter = ',',
            value_name = "FIELDS",
            help_heading = "Output options"
        )]
        fields: Option<Vec<String>>,
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
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n",
            help_heading = "Common options"
        )]
        node_limit: i32,
        /// Simple output (one entry per line)
        #[arg(short, long, help_heading = "Common options")]
        simple: bool,
        /// Comma-separated k=v retrieval tags; all tags must match
        #[arg(
            long = "tags",
            value_delimiter = ',',
            value_name = "k=v",
            help_heading = "Common options"
        )]
        tags: Vec<String>,
        /// Comma-separated fields to display (name,uri,path,type,size,mode,mtime,locked,id,tags)
        #[arg(
            short = 'f',
            long = "fields",
            value_delimiter = ',',
            value_name = "FIELDS",
            help_heading = "Output options"
        )]
        fields: Option<Vec<String>>,
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
        action: Box<PrivacyCommands>,
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
    /// [Interactive] Compile source materials with a VikingBot Skill
    #[command(subcommand_negates_reqs = true, args_conflicts_with_subcommands = true)]
    Compile {
        #[command(subcommand)]
        command: Option<CompileCommands>,
        /// Execute this persistent configuration without overriding its inputs
        #[arg(long, value_name = "name", allow_hyphen_values = true, value_parser = parse_compile_name, conflicts_with_all = ["from_uris", "to", "skill", "instruction"])]
        compile_name: Option<String>,
        /// Source file or directory; repeat the flag or separate entries with commas.
        /// Omitted with `--skill memory`, which consolidates `--to` in place.
        #[arg(long = "from", value_delimiter = ',', value_name = "uri")]
        from_uris: Vec<String>,
        /// Target directory; required unless --skill memory selects the current user's memories root
        #[arg(long, value_name = "uri")]
        to: Option<String>,
        /// Skill directory or SKILL.md Viking URI; the literal `memory` runs memory consolidation
        #[arg(long, value_name = "uri", required_unless_present = "compile_name")]
        skill: Option<String>,
        /// Additional instructions for this Compile task
        #[arg(long, value_name = "text")]
        instruction: Option<String>,
        /// JSON args; named runs use only owner_user_id
        #[arg(long, value_name = "json")]
        args: Option<String>,
    },

    // --- Status & Observability ---
    /// [Status] Wait for queued async processing to complete
    Wait,
    /// [Status] Track async resource processing tasks
    Task {
        #[command(subcommand)]
        action: TaskCommands,
    },
    /// [Version] Manage workspace snapshots (commit, restore, show, diff, log)
    Snapshot {
        #[command(subcommand)]
        cmd: SnapshotCmd,
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
        /// Reindex mode: vectors_only rebuilds vectors; semantic_and_vectors regenerates semantic artifacts, then vectors
        #[arg(
            long,
            default_value = "vectors_only",
            value_parser = ["vectors_only", "semantic_and_vectors"],
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
        /// Rebuild all selected semantic/vector data without comparing fingerprints
        #[arg(long, help_heading = "Common options")]
        force: bool,
        /// Comma-separated k=v retrieval tags for rebuilt vector records
        #[arg(
            long = "tags",
            value_delimiter = ',',
            value_name = "k=v",
            help_heading = "Common options"
        )]
        tags: Vec<String>,
        /// Tag update mode; clear does not require --tags
        #[arg(
            long = "tag-mode",
            default_value = "replace",
            value_parser = ["replace", "append", "clear"],
            help_heading = "Common options"
        )]
        tag_mode: String,
        /// Recursively reindex subdirectories (only affects semantic_and_vectors)
        #[arg(
            long,
            default_value_t = true,
            action = ArgAction::Set,
            value_name = "bool",
            help_heading = "Common options"
        )]
        recursive: bool,
    },
}

impl Commands {
    /// Returns true if this command supports running with the root API key.
    pub(crate) fn supports_sudo(&self) -> bool {
        match self {
            Self::Admin { .. } | Self::System { .. } | Self::Reindex { .. } => true,
            Self::Task { action } => matches!(
                action,
                TaskCommands::Status { .. } | TaskCommands::List { .. }
            ),
            _ => false,
        }
    }

    pub(crate) fn supports_upload_options(&self) -> bool {
        matches!(
            self,
            Self::AddResource { .. }
                | Self::AddSkill(_)
                | Self::Skills {
                    action: SkillCommands::Add(_)
                }
        )
    }
}

impl Commands {
    pub(crate) fn requires_cli_config_file(&self) -> bool {
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

    pub(crate) fn allows_invalid_runtime_config(&self) -> bool {
        matches!(self, Commands::Config { .. })
    }
}

#[cfg(test)]
pub(crate) mod tests;
