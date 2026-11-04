//! Resource attributes, ACL, and compile shortcut arguments.

use clap::Subcommand;

#[derive(Subcommand)]
pub(crate) enum AttrsCommands {
    /// Get logical extended attributes
    Get {
        /// Viking URI to get attributes for
        #[arg(value_name = "uri")]
        uri: String,
        /// Optional attrs key, for example tags, memory, or memory.tags
        #[arg(value_name = "key")]
        key: Option<String>,
    },
    /// Update explicit retrieval tags metadata for a file or directory
    SetTags {
        /// Viking URI
        uri: String,
        /// Comma-separated k=v tags, e.g. env=prod,team=search
        #[arg(long = "tags", value_delimiter = ',')]
        tags: Vec<String>,
        /// Tag update mode: replace or append (append replaces existing values by key)
        #[arg(long, default_value = "replace")]
        mode: String,
        /// Recursively update descendant files and semantic nodes when target is a directory
        #[arg(long, default_value = "false")]
        recursive: bool,
    },
}

#[derive(Subcommand)]
pub(crate) enum AclCommands {
    Get {
        uri: String,
    },
    Set {
        uri: String,
        #[arg(long = "entry")]
        entries: Vec<String>,
        /// Whether this node uses inherited grants or direct grants only
        #[arg(long, value_parser = ["inherit", "restricted"])]
        acl_mode: Option<String>,
    },
    Grant {
        uri: String,
        #[arg(long)]
        principal: String,
        #[arg(long)]
        level: String,
    },
    Revoke {
        uri: String,
        #[arg(long)]
        principal: String,
    },
    Rm {
        uri: String,
    },
}

/// Compile shortcuts select a fixed skill and reuse the Compile request pipeline.
#[derive(Subcommand)]
pub(crate) enum CompileCommands {
    /// Consolidate existing memories in place without external sources
    Memory {
        /// Existing writable memory directory; ~ identifies the current request user
        #[arg(long, value_name = "uri", default_value = "viking://~/memories")]
        to: String,
        /// Additional instructions for memory consolidation
        #[arg(long, value_name = "text")]
        instruction: Option<String>,
        /// Provider arguments as JSON
        #[arg(long, value_name = "json")]
        args: Option<String>,
    },
}
