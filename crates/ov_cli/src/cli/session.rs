//! Session and session configuration arguments.

use clap::Subcommand;

#[derive(Subcommand)]
pub(crate) enum SessionCommands {
    /// Create a new session
    New {
        /// Optional session ID
        #[arg(long = "session-id", value_name = "session-id")]
        session_id: Option<String>,
        /// Default event-memory tags as comma-separated key=value pairs
        #[arg(long = "event-tags", value_name = "key=value", value_delimiter = ',')]
        event_tags: Vec<String>,
        /// Auto-commit policy as a JSON object
        #[arg(
            long = "auto-commit-policy-json",
            value_name = "json",
            conflicts_with = "no_auto_commit"
        )]
        auto_commit_policy_json: Option<String>,
        /// Disable automatic commits for this session
        #[arg(long = "no-auto-commit", conflicts_with = "auto_commit_policy_json")]
        no_auto_commit: bool,
    },
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
    /// Update mutable session configuration
    Config {
        #[command(subcommand)]
        action: SessionConfigCommands,
    },
    /// Commit a session (archive messages and extract memories)
    Commit {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
        /// Event-memory tags for this commit as comma-separated key=value pairs
        #[arg(
            long = "event-tags",
            value_name = "key=value",
            value_delimiter = ',',
            conflicts_with = "no_event_tags"
        )]
        event_tags: Vec<String>,
        /// Do not apply the session's default event tags to this commit
        #[arg(long = "no-event-tags", conflicts_with = "event_tags")]
        no_event_tags: bool,
    },
}

#[derive(Subcommand)]
pub(crate) enum SessionConfigCommands {
    /// Set mutable session configuration
    Set {
        /// Session ID
        #[arg(value_name = "session-id")]
        session_id: String,
        /// Default event-memory tags as comma-separated key=value pairs
        #[arg(
            long = "event-tags",
            value_name = "key=value",
            value_delimiter = ',',
            required_unless_present_any = [
                "no_event_tags",
                "auto_commit_policy_json",
                "no_auto_commit"
            ],
            conflicts_with = "no_event_tags"
        )]
        event_tags: Vec<String>,
        /// Clear the session's default event-memory tags
        #[arg(
            long = "no-event-tags",
            required_unless_present_any = [
                "event_tags",
                "auto_commit_policy_json",
                "no_auto_commit"
            ],
            conflicts_with = "event_tags"
        )]
        no_event_tags: bool,
        /// Auto-commit policy fields as a JSON object
        #[arg(
            long = "auto-commit-policy-json",
            value_name = "json",
            required_unless_present_any = ["event_tags", "no_event_tags", "no_auto_commit"],
            conflicts_with = "no_auto_commit"
        )]
        auto_commit_policy_json: Option<String>,
        /// Disable automatic commits for this session
        #[arg(
            long = "no-auto-commit",
            required_unless_present_any = [
                "event_tags",
                "no_event_tags",
                "auto_commit_policy_json"
            ],
            conflicts_with = "auto_commit_policy_json"
        )]
        no_auto_commit: bool,
    },
}
