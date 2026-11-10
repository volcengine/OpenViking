//! Local configuration management arguments.

use clap::{Args, Subcommand};

#[derive(Subcommand)]
pub(crate) enum ConfigCommands {
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
pub(crate) enum ConfigAddTarget {
    /// Add an OpenViking Service config
    OvService(ConfigAddOvServiceArgs),
    /// Add a custom config
    Custom(ConfigAddCustomArgs),
}

#[derive(Args, Debug, Clone)]
pub(crate) struct ConfigAddOvServiceArgs {
    /// Saved config name. Agents should pass this for idempotent retries; generated when omitted.
    #[arg(long, value_name = "name", help_heading = "Common options")]
    pub(crate) name: Option<String>,
    /// Read API key from stdin
    #[arg(long, conflicts_with = "api_key_env", help_heading = "Common options")]
    pub(crate) api_key_stdin: bool,
    /// Read API key from an environment variable
    #[arg(
        long,
        conflicts_with = "api_key_stdin",
        value_name = "env",
        help_heading = "Common options"
    )]
    pub(crate) api_key_env: Option<String>,
    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, value_name = "account", help_heading = "Advanced options")]
    pub(crate) account: Option<String>,
    /// User identifier to send as X-OpenViking-User
    #[arg(long, value_name = "user", help_heading = "Advanced options")]
    pub(crate) user: Option<String>,
    /// Peer actor scope to send as X-OpenViking-Actor-Peer
    #[arg(long = "actor-peer-id", hide = true)]
    pub(crate) actor_peer_id: Option<String>,
    /// Make the saved config active after validation
    #[arg(long, help_heading = "Common options")]
    pub(crate) activate: bool,
    /// Replace an existing saved config
    #[arg(long, help_heading = "Common options")]
    pub(crate) force: bool,
}

#[derive(Args, Debug, Clone)]
pub(crate) struct ConfigAddCustomArgs {
    /// Saved config name. Agents should pass this for idempotent retries; generated when omitted.
    #[arg(long, value_name = "name", help_heading = "Common options")]
    pub(crate) name: Option<String>,
    /// OpenViking server URL
    #[arg(long, value_name = "url", help_heading = "Common options")]
    pub(crate) url: Option<String>,
    /// Read API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["api_key_env", "root_api_key_stdin"],
        help_heading = "Common options"
    )]
    pub(crate) api_key_stdin: bool,
    /// Read API key from an environment variable
    #[arg(
        long,
        conflicts_with = "api_key_stdin",
        value_name = "env",
        help_heading = "Common options"
    )]
    pub(crate) api_key_env: Option<String>,
    /// Read root API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_env", "api_key_stdin"],
        help_heading = "Common options"
    )]
    pub(crate) root_api_key_stdin: bool,
    /// Read root API key from an environment variable
    #[arg(
        long,
        conflicts_with = "root_api_key_stdin",
        value_name = "env",
        help_heading = "Common options"
    )]
    pub(crate) root_api_key_env: Option<String>,
    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, value_name = "account", help_heading = "Advanced options")]
    pub(crate) account: Option<String>,
    /// User identifier to send as X-OpenViking-User
    #[arg(long, value_name = "user", help_heading = "Advanced options")]
    pub(crate) user: Option<String>,
    /// Peer actor scope to send as X-OpenViking-Actor-Peer
    #[arg(long = "actor-peer-id", hide = true)]
    pub(crate) actor_peer_id: Option<String>,
    /// Make the saved config active after validation
    #[arg(long, help_heading = "Common options")]
    pub(crate) activate: bool,
    /// Replace an existing saved config
    #[arg(long, help_heading = "Common options")]
    pub(crate) force: bool,
}

#[derive(Args, Debug, Clone)]
pub(crate) struct ConfigEditArgs {
    /// Saved config name to edit
    #[arg(value_name = "name")]
    pub(crate) name: String,
    /// Rename the saved config
    #[arg(long, value_name = "name", help_heading = "Common options")]
    pub(crate) new_name: Option<String>,
    /// New server URL. OpenViking Service configs use a fixed URL.
    #[arg(long, value_name = "url", help_heading = "Common options")]
    pub(crate) url: Option<String>,
    /// Read replacement API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["api_key_env", "clear_api_key", "root_api_key_stdin"],
        help_heading = "Common options"
    )]
    pub(crate) api_key_stdin: bool,
    /// Read replacement API key from an environment variable
    #[arg(
        long,
        conflicts_with_all = ["api_key_stdin", "clear_api_key"],
        value_name = "env",
        help_heading = "Common options"
    )]
    pub(crate) api_key_env: Option<String>,
    /// Remove the API key
    #[arg(
        long,
        conflicts_with_all = ["api_key_stdin", "api_key_env"],
        help_heading = "Common options"
    )]
    pub(crate) clear_api_key: bool,
    /// Read replacement root API key from stdin
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_env", "clear_root_api_key", "api_key_stdin"],
        help_heading = "Common options"
    )]
    pub(crate) root_api_key_stdin: bool,
    /// Read replacement root API key from an environment variable
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_stdin", "clear_root_api_key"],
        value_name = "env",
        help_heading = "Common options"
    )]
    pub(crate) root_api_key_env: Option<String>,
    /// Remove the root API key
    #[arg(
        long,
        conflicts_with_all = ["root_api_key_stdin", "root_api_key_env"],
        help_heading = "Common options"
    )]
    pub(crate) clear_root_api_key: bool,
    /// Account identifier to send as X-OpenViking-Account
    #[arg(long, value_name = "account", help_heading = "Advanced options")]
    pub(crate) account: Option<String>,
    /// User identifier to send as X-OpenViking-User
    #[arg(long, value_name = "user", help_heading = "Advanced options")]
    pub(crate) user: Option<String>,
    /// Peer actor scope to send as X-OpenViking-Actor-Peer
    #[arg(
        long = "actor-peer-id",
        hide = true,
        conflicts_with = "clear_actor_peer_id"
    )]
    pub(crate) actor_peer_id: Option<String>,
    /// Remove the peer actor scope
    #[arg(
        long = "clear-actor-peer-id",
        hide = true,
        conflicts_with = "actor_peer_id"
    )]
    pub(crate) clear_actor_peer_id: bool,
    /// Make the saved config active after validation
    #[arg(long, help_heading = "Common options")]
    pub(crate) activate: bool,
    /// Replace an existing saved config when renaming
    #[arg(long, help_heading = "Common options")]
    pub(crate) force: bool,
}

#[derive(Args, Debug, Clone)]
pub(crate) struct ConfigDeleteArgs {
    /// Saved config name to delete
    #[arg(value_name = "name")]
    pub(crate) name: String,
    /// Delete even when the saved config file cannot be parsed
    #[arg(long, help_heading = "Common options")]
    pub(crate) force: bool,
}
