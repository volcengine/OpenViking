//! Skill installation and management arguments.

use crate::cli::UploadCliOptions;
use clap::{Args, Subcommand};

#[derive(Args)]
pub(crate) struct SkillAddArgs {
    /// Local skill file/directory, Git repository or GitHub tree URL, or raw content
    #[arg(value_name = "source")]
    pub(crate) source: String,
    /// Install only the named skill(s); use '*' to install all skills in a source
    #[arg(short = 's', long = "skill", value_name = "NAME", num_args = 1.., value_delimiter = ',')]
    pub(crate) skills: Vec<String>,
    /// List available skills in the source without installing
    #[arg(short = 'l', long = "list")]
    pub(crate) list: bool,
    /// Wait until processing is complete
    #[arg(short = 'w', long)]
    pub(crate) wait: bool,
    /// Skip confirmation prompt
    #[arg(short = 'y', long = "yes")]
    pub(crate) yes: bool,
    /// Parent skill root URI (e.g. viking://agent/skills); defaults to user-private skills
    #[arg(short = 'p', long = "parent-auto-create", value_name = "uri")]
    pub(crate) parent: Option<String>,
    #[command(flatten)]
    pub(crate) upload_options: UploadCliOptions,
}

#[derive(Subcommand)]
pub(crate) enum SkillCommands {
    /// Add skills from a source
    Add(SkillAddArgs),
    /// List installed agent skills
    #[command(alias = "ls")]
    List {
        /// Maximum number of skills to list
        #[arg(
            short = 'n',
            long = "node-limit",
            alias = "limit",
            default_value = "1000",
            value_parser = clap::value_parser!(i32).range(0..),
            value_name = "n"
        )]
        node_limit: i32,
        /// Restrict listing to a specific skill root URI (defaults to merged user + agent)
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        parent: Option<String>,
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
            value_parser = clap::value_parser!(i32).range(0..),
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
        /// Restrict search to a specific skill root URI (defaults to merged user + agent)
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        parent: Option<String>,
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
        /// Resolve the skill under a specific root URI (e.g. viking://agent/skills)
        #[arg(short = 'p', long = "uri", value_name = "uri")]
        parent: Option<String>,
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
        /// Resolve the skill under a specific root URI (e.g. viking://agent/skills)
        #[arg(short = 'p', long = "parent-auto-create", value_name = "uri")]
        parent: Option<String>,
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
        /// Resolve the skill under a specific root URI (e.g. viking://agent/skills)
        #[arg(short = 'p', long = "parent-auto-create", value_name = "uri")]
        parent: Option<String>,
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
