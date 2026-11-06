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
        /// Resolve the server under a specific tools root URI
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
        /// Resolve the server under a specific tools root URI
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
        /// Resolve the server under a specific tools root URI
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        target_uri: Option<String>,
    },
    /// Check a registered MCP server
    Status {
        /// Registered MCP server name
        #[arg(value_name = "name")]
        name: String,
        /// Resolve the server under a specific tools root URI
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
    /// List live context nodes mounted from an MCP server
    Mounts {
        /// Registered MCP server name
        #[arg(long = "mcp", value_name = "name")]
        mcp: String,
        /// Optional MCP tool name
        #[arg(long = "tool", value_name = "tool")]
        tool: Option<String>,
        /// Resolve the server under a specific tools root URI
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
    },
}
