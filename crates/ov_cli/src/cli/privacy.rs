//! Privacy configuration arguments.

use clap::Subcommand;

#[derive(Subcommand)]
pub(crate) enum PrivacyCommands {
    /// List privacy config categories
    Categories,
    /// List all privacy targets and fields as a tree (current version, no values)
    Tree {
        /// Limit the tree to one category
        #[arg(long, value_name = "category")]
        category: Option<String>,
        /// Append the owner-bound reference for each field
        #[arg(long)]
        references: bool,
    },
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
    /// Show an owner-bound reference for one active field without revealing its value
    Reference {
        /// Privacy config category
        #[arg(value_name = "category")]
        category: String,
        /// Privacy config target key
        #[arg(value_name = "target-key")]
        target_key: String,
        /// Field in the current values object
        #[arg(value_name = "field")]
        field: Option<String>,
    },
    /// Reveal one active field value for immediate use
    Resolve {
        /// Owner-bound reference, field URI, or privacy category
        #[arg(value_name = "reference-or-uri-or-category")]
        selector_or_category: String,
        /// Privacy config target key (category form only)
        #[arg(value_name = "target-key")]
        target_key: Option<String>,
        /// Field in current values (category form only)
        #[arg(value_name = "field")]
        field: Option<String>,
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
        /// Field updates in field=value format (repeatable; --key is a compatibility alias)
        #[arg(long = "field", alias = "key", value_name = "field=value")]
        field: Vec<String>,
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
    /// Delete a target or make one active field unavailable
    Delete {
        /// Privacy reference, URI, or config category
        #[arg(value_name = "reference-or-uri-or-category")]
        selector_or_category: String,
        /// Privacy config target key (category form only)
        #[arg(value_name = "target-key")]
        target_key: Option<String>,
        /// Field to remove from active values (optional)
        #[arg(value_name = "field")]
        field: Option<String>,
        /// Skip the confirmation prompt
        #[arg(long)]
        yes: bool,
    },
    /// Scan persisted content for registered Privacy values
    Scan {
        /// Exact viking:// file or directory to scan
        #[arg(long, value_name = "uri")]
        uri: String,
        /// Rewrite unambiguous matches and refresh derived indexes
        #[arg(long)]
        fix: bool,
        /// Maximum files/nodes enumerated per root
        #[arg(
            long,
            default_value_t = 1000,
            value_parser = clap::value_parser!(u16).range(1..=1000)
        )]
        node_limit: u16,
    },
}
