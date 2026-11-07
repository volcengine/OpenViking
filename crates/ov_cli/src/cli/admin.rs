//! Account, user, and group administration arguments.

use clap::{ArgAction, Subcommand};

#[derive(Subcommand)]
pub(crate) enum AdminCommands {
    /// Create a new account with its first admin user
    CreateAccount {
        /// Account ID to create
        #[arg(value_name = "account-id")]
        account_id: String,
        /// First admin user ID
        #[arg(long = "admin", value_name = "user-id")]
        admin_user_id: String,
        /// Deterministic API key seed
        #[arg(long, value_name = "seed")]
        seed: Option<String>,
        /// Initial config for the first admin user as JSON
        #[arg(long = "user-config-json", value_name = "json")]
        user_config_json: Option<String>,
    },
    /// List all accounts (ROOT only)
    ListAccounts {
        /// Filter accounts by ID (supports wildcard * and ?)
        #[arg(long, value_name = "pattern")]
        name: Option<String>,
        /// Page size; omit to list all accounts
        #[arg(long, value_name = "n")]
        limit: Option<u32>,
        /// 1-based page number (requires --limit)
        #[arg(long, default_value = "1", value_name = "n")]
        page: u32,
    },
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
        /// Role: admin, user, or global (global requires ROOT)
        #[arg(long, default_value = "user", value_name = "role")]
        role: String,
        /// Deterministic API key seed
        #[arg(long, value_name = "seed")]
        seed: Option<String>,
        /// Initial config for the new user as JSON
        #[arg(long = "user-config-json", value_name = "json")]
        user_config_json: Option<String>,
    },
    /// List all users in an account
    ListUsers {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// Page size; omit to list all users
        #[arg(long, value_name = "n")]
        limit: Option<u32>,
        /// Filter users by name (supports wildcard * and ?)
        #[arg(long, value_name = "pattern")]
        name: Option<String>,
        /// Filter users by role
        #[arg(long, value_name = "role")]
        role: Option<String>,
        /// 1-based page number (requires --limit)
        #[arg(long, default_value = "1", value_name = "n")]
        page: u32,
        /// Show plaintext API keys instead of masked values (ROOT sees all keys; ADMIN/USER only its own)
        #[arg(long)]
        reveal_api_keys: bool,
    },
    /// Create an empty account-scoped group
    CreateGroup {
        #[arg(value_name = "account-id")]
        account_id: String,
        #[arg(value_name = "group-id")]
        group_id: String,
    },
    /// List groups in an account
    ListGroups {
        #[arg(value_name = "account-id")]
        account_id: String,
    },
    /// List the users in a group
    ListGroupMembers {
        #[arg(value_name = "account-id")]
        account_id: String,
        #[arg(value_name = "group-id")]
        group_id: String,
    },
    /// Add an existing account user to a group
    AddGroupMember {
        #[arg(value_name = "account-id")]
        account_id: String,
        #[arg(value_name = "group-id")]
        group_id: String,
        #[arg(value_name = "user-id")]
        user_id: String,
    },
    /// Remove a user from a group
    RemoveGroupMember {
        #[arg(value_name = "account-id")]
        account_id: String,
        #[arg(value_name = "group-id")]
        group_id: String,
        #[arg(value_name = "user-id")]
        user_id: String,
    },
    /// Delete an empty group
    DeleteGroup {
        #[arg(value_name = "account-id")]
        account_id: String,
        #[arg(value_name = "group-id")]
        group_id: String,
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
    /// Change a user's role for identity management (ROOT or own-account ADMIN)
    SetRole {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// User ID
        #[arg(value_name = "user-id")]
        user_id: String,
        /// New role: admin or user (case-insensitive)
        #[arg(value_name = "role")]
        role: String,
    },
    /// Regenerate a user's API key; self-rotation updates the active ovcli.conf
    RegenerateKey {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// User ID
        #[arg(value_name = "user-id")]
        user_id: String,
        /// Deterministic API key seed
        #[arg(long, value_name = "seed")]
        seed: Option<String>,
    },
    /// Update allowlisted settings for an account
    SetAccountSettings {
        /// Account ID
        #[arg(value_name = "account-id")]
        account_id: String,
        /// Enable ACL authorization for shared resources
        #[arg(
            long,
            required = true,
            action = ArgAction::Set,
            value_name = "true|false"
        )]
        acl_enabled: bool,
    },
}
