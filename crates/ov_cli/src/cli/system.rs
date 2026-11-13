//! System, observer, task, watch, and snapshot arguments.

use crate::commands;
use clap::Subcommand;

#[derive(Subcommand)]
pub(crate) enum TaskCommands {
    /// Show status of a specific task
    Status {
        /// Task ID returned by an asynchronous command, including compile
        #[arg(value_name = "task-id")]
        task_id: String,
    },
    /// Cancel a task
    Cancel {
        /// Task ID returned by an asynchronous command, including compile
        #[arg(value_name = "task-id")]
        task_id: String,
    },
    /// List all tracked tasks
    List {
        /// Filter by task type (e.g. add_resource, add_skill, session_commit, reindex)
        #[arg(long, value_name = "type")]
        task_type: Option<String>,
        /// Filter by status (pending, running, cancelling, completed, failed, cancelled)
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
pub(crate) enum SnapshotCmd {
    /// Create a snapshot of current workspace state
    Commit {
        #[arg(short = 'm', long)]
        message: String,
        /// Limit to specific viking:// URIs (comma-separated); accepts files and directories. Directories are expanded recursively with the snapshot pruning rules. Required for USER/ADMIN callers; only local ROOT mode may omit paths to snapshot the full account tree.
        #[arg(long, value_delimiter = ',')]
        paths: Option<Vec<String>>,
        #[arg(long, default_value = "main")]
        branch: String,
        #[arg(long)]
        author_name: Option<String>,
        #[arg(long)]
        author_email: Option<String>,
    },
    /// Restore a project directory or the full account tree to a past snapshot (forward commit)
    Restore {
        /// Commit oid, branch, or tag
        source_commit: String,
        /// Optional viking:// directory or relative tree path; omit for full-tree restore
        project_dir: Option<String>,
        #[arg(long, default_value = "main")]
        branch: String,
        #[arg(long)]
        dry_run: bool,
        #[arg(short = 'm', long)]
        message: Option<String>,
        #[arg(long)]
        author_name: Option<String>,
        #[arg(long)]
        author_email: Option<String>,
    },
    /// Show a commit's metadata, or a single blob at a path
    Show {
        target_ref: String,
        /// viking:// URI of a file; omit to show commit metadata
        #[arg(long)]
        path: Option<String>,
        /// Write blob bytes to this file (default: stdout)
        #[arg(long = "out-file")]
        out_path: Option<std::path::PathBuf>,
    },
    /// Walk commit history (newest first)
    Log {
        #[arg(long, default_value = "main")]
        branch: String,
        #[arg(long, default_value_t = 20)]
        limit: u32,
        /// Show only commits touching any of these viking:// URIs (comma-separated); accepts files and directories.
        #[arg(long, value_delimiter = ',')]
        paths: Option<Vec<String>>,
    },
    /// Compare one file between two snapshots
    Diff {
        /// viking:// URI of the file to compare
        path: String,
        /// Older commit oid, branch, or tag; omit to compare an empty file
        #[arg(long = "from")]
        from_ref: Option<String>,
        /// Newer commit oid, branch, or tag
        #[arg(long = "to")]
        to_ref: String,
    },
    /// List changed source files under directories between two snapshots
    DiffPaths {
        /// Directory URIs (comma-separated)
        #[arg(long, value_delimiter = ',', required = true, num_args = 1..)]
        paths: Vec<String>,
        /// Older commit oid, branch, or tag; omit to compare an empty tree
        #[arg(long = "from")]
        from_ref: Option<String>,
        /// Newer commit oid, branch, or tag
        #[arg(long = "to")]
        to_ref: String,
        #[arg(long, default_value_t = 0)]
        offset: u32,
        #[arg(long, default_value_t = 100, value_parser = clap::value_parser!(u32).range(1..=1000))]
        limit: u32,
    },
    /// Get the account .ovgitignore content
    IgnoreGet,
    /// Set the account .ovgitignore content
    IgnoreSet {
        /// .ovgitignore content passed inline
        #[arg(long = "content")]
        content: Option<String>,
        /// File to read the content from; takes precedence over --content
        #[arg(long = "file")]
        file: Option<std::path::PathBuf>,
    },
    /// Delete the account .ovgitignore
    IgnoreDelete,
}

#[derive(Subcommand)]
pub(crate) enum SystemCommands {
    /// Wait for queued async processing to complete
    Wait,
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
}

#[derive(Subcommand)]
pub(crate) enum ObserverCommands {
    /// Get queue status
    Queue,
    /// Get VikingDB status
    Vikingdb,
    /// Get models status (VLM, Embedding, Rerank)
    Models,
    /// Get retrieval quality metrics
    Retrieval,
    /// Get filesystem operation metrics
    #[command(alias = "fs")]
    Filesystem,
    /// Get overall system status
    System,
}

#[derive(Subcommand)]
pub(crate) enum WatchCommands {
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
