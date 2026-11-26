//! MCP server, tool, and live context mount arguments.

use clap::Subcommand;

#[derive(Subcommand)]
pub(crate) enum McpCommands {
    /// Register a Streamable HTTP MCP server
    Add {
        /// MCP configuration JSON containing an mcpServers object
        #[arg(value_name = "json")]
        config: String,
        /// Wait until tool processing is complete
        #[arg(short = 'w', long)]
        wait: bool,
        /// Tools root URI; defaults to the current user's private tools
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Remove a registered MCP server
    Remove {
        /// MCP server name
        #[arg(value_name = "name")]
        name: String,
        /// Restrict to this tools root; defaults to private first, then shared
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// List registered MCP servers
    List {
        /// Restrict listing to a specific tools root URI
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Find tools from registered MCP catalogs
    Find {
        /// Tool name or description query
        #[arg(value_name = "query")]
        query: String,
        /// Maximum number of results
        #[arg(
            short = 'n',
            long = "limit",
            default_value = "10",
            value_parser = clap::value_parser!(u8).range(1..=100),
            value_name = "n"
        )]
        limit: u8,
        /// Restrict search to a specific tools root URI
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Validate connectivity and tool discovery
    Validate {
        /// MCP configuration JSON containing an mcpServers object
        #[arg(value_name = "json")]
        config: String,
    },
    /// Show one MCP server and its tools
    Show {
        /// MCP server name
        #[arg(value_name = "name")]
        name: String,
        /// Restrict to this tools root; defaults to private first, then shared
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Refresh a registered MCP server's tool catalog
    Sync {
        /// MCP server name
        #[arg(value_name = "name")]
        name: String,
        /// Wait until tool processing is complete
        #[arg(short = 'w', long)]
        wait: bool,
        /// Restrict to this tools root; defaults to private first, then shared
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Check a registered MCP server
    Status {
        /// Registered MCP server name
        #[arg(value_name = "name")]
        name: String,
        /// Restrict to this tools root; defaults to private first, then shared
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Mount an MCP tool as a live context node
    Mount {
        /// Tool URI under a user or shared tools root
        #[arg(value_name = "mcp-tool-uri")]
        tool_uri: String,
        /// Markdown URI to create for the live context node
        #[arg(value_name = "live-context-node-uri")]
        node_uri: String,
        /// Title written to the live context node
        #[arg(long, value_name = "title")]
        title: String,
        /// Description written to the live context node
        #[arg(long, value_name = "description")]
        description: Option<String>,
        /// MCP Tool call timeout in seconds
        #[arg(long, default_value_t = 60, value_parser = clap::value_parser!(u64).range(1..))]
        timeout_seconds: u64,
        /// Fixed tool arguments as a JSON object
        #[arg(long = "args", default_value = "{}", value_name = "json")]
        arguments: String,
        /// Wait until processing is complete
        #[arg(short = 'w', long)]
        wait: bool,
    },
    /// Remove a mounted live context node
    Unmount {
        /// Live context Markdown node URI
        #[arg(value_name = "live-context-node-uri")]
        node_uri: String,
        /// Wait until processing is complete
        #[arg(short = 'w', long)]
        wait: bool,
    },
    /// List all live context mounts, optionally filtered by MCP server
    Mounts {
        /// Filter by MCP server name; defaults to all servers
        #[arg(long = "mcp", value_name = "name")]
        mcp: Option<String>,
        /// Filter by one or more comma-separated mount statuses; defaults to all
        #[arg(
            long,
            value_name = "status",
            value_delimiter = ',',
            value_parser = ["VALID", "REVIEW", "INVALID", "REMOVED", "UNKNOWN"],
            ignore_case = true,
            hide_possible_values = true,
            long_help = concat!(
                "Filter by comma-separated statuses (case-insensitive); defaults to all.\n\n",
                "Available statuses:\n",
                "  VALID    Tool input definition matches the mounted version\n",
                "  REVIEW   Tool input definition changed; preset arguments are still valid\n",
                "  INVALID  Tool input definition changed; preset arguments are no longer valid\n",
                "  REMOVED  Local tool definition no longer exists\n",
                "  UNKNOWN  Local tool definition cannot be read or is corrupt"
            )
        )]
        status: Vec<String>,
        /// Optional MCP tool name
        #[arg(long = "tool", value_name = "tool")]
        tool: Option<String>,
        /// Restrict to this tools root; without --mcp, defaults to both spaces
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Invoke a registered MCP tool
    Invoke {
        /// Tool URI under a user or shared tools root
        #[arg(value_name = "uri")]
        uri: String,
        /// Tool arguments as a JSON object
        #[arg(long = "args", default_value = "{}", value_name = "json")]
        arguments: String,
        /// Per-call timeout in seconds; use the Live Context node's value when present
        #[arg(long, value_name = "seconds")]
        timeout_seconds: Option<f64>,
    },
}
