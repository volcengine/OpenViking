use std::ffi::OsString;

use colored::Colorize;
use unicode_width::UnicodeWidthStr;

use crate::{
    i18n::{Language, copy},
    theme,
};

const BOX_WIDTH: usize = 74;
const COMMAND_WIDTH: usize = 16;
const DESCRIPTION_WIDTH: usize = BOX_WIDTH - COMMAND_WIDTH - 5;
const COMMAND_HELP_LEFT_WIDTH: usize = 34;

#[derive(Debug, Clone, Copy)]
struct HelpCommand {
    name: &'static str,
    description: &'static str,
    badge: Option<&'static str>,
}

#[derive(Debug, Clone, Copy)]
struct HelpSection {
    title: &'static str,
    commands: &'static [HelpCommand],
}

#[derive(Debug, Clone, Copy)]
struct HelpItem {
    label: &'static str,
    description: &'static str,
}

#[derive(Debug, Clone, Copy)]
struct CommandHelpSpec {
    path: &'static [&'static str],
    purpose: &'static str,
    usage: &'static str,
    examples: &'static [HelpItem],
    arguments: &'static [HelpItem],
    common_options: &'static [HelpItem],
    advanced_options: &'static [HelpItem],
    subcommands: &'static [HelpItem],
    next_steps: &'static [HelpItem],
}

const CORE_WORKFLOW: &[HelpCommand] = &[
    HelpCommand {
        name: "add-resource",
        description: "Add files, folders, URLs, or repos into OpenViking",
        badge: None,
    },
    HelpCommand {
        name: "add-skill",
        description: "Add a skill into OpenViking",
        badge: None,
    },
    HelpCommand {
        name: "find",
        description: "Retrieve relevant context semantically",
        badge: None,
    },
    HelpCommand {
        name: "read",
        description: "Read exact resource content",
        badge: None,
    },
    HelpCommand {
        name: "write",
        description: "Update an existing resource",
        badge: None,
    },
    HelpCommand {
        name: "add-memory",
        description: "Add a memory directly",
        badge: None,
    },
];

const FILESYSTEM: &[HelpCommand] = &[
    HelpCommand {
        name: "ls",
        description: "List directory contents",
        badge: None,
    },
    HelpCommand {
        name: "tree",
        description: "Show a scoped resource tree",
        badge: None,
    },
    HelpCommand {
        name: "mkdir",
        description: "Create a directory",
        badge: None,
    },
    HelpCommand {
        name: "rm",
        description: "Remove a resource",
        badge: None,
    },
    HelpCommand {
        name: "mv",
        description: "Move or rename a resource",
        badge: None,
    },
    HelpCommand {
        name: "stat",
        description: "Show resource metadata",
        badge: None,
    },
    HelpCommand {
        name: "get",
        description: "Download a file",
        badge: None,
    },
];

const SEARCH_CONTEXT: &[HelpCommand] = &[
    HelpCommand {
        name: "find",
        description: "Semantic retrieval",
        badge: None,
    },
    HelpCommand {
        name: "search",
        description: "Context-aware retrieval",
        badge: Some("experimental"),
    },
    HelpCommand {
        name: "grep",
        description: "Pattern search",
        badge: None,
    },
    HelpCommand {
        name: "glob",
        description: "Glob search",
        badge: None,
    },
    HelpCommand {
        name: "abstract",
        description: "Read Level 0 abstract",
        badge: None,
    },
    HelpCommand {
        name: "overview",
        description: "Read Level 1 overview",
        badge: None,
    },
    HelpCommand {
        name: "read",
        description: "Read Level 2 content",
        badge: None,
    },
];

const CONFIG_STATUS: &[HelpCommand] = &[
    HelpCommand {
        name: "config",
        description: "Manage configs",
        badge: None,
    },
    HelpCommand {
        name: "config show",
        description: "Show active config",
        badge: None,
    },
    HelpCommand {
        name: "config validate",
        description: "Validate active config",
        badge: None,
    },
    HelpCommand {
        name: "config switch",
        description: "Switch active config",
        badge: None,
    },
    HelpCommand {
        name: "config add",
        description: "Add a config non-interactively",
        badge: None,
    },
    HelpCommand {
        name: "config list",
        description: "List saved configs",
        badge: None,
    },
    HelpCommand {
        name: "config delete",
        description: "Delete a saved config",
        badge: None,
    },
    HelpCommand {
        name: "language",
        description: "Choose CLI display language (alias: lang)",
        badge: None,
    },
    HelpCommand {
        name: "health",
        description: "Quick health check",
        badge: None,
    },
    HelpCommand {
        name: "status",
        description: "Full server status",
        badge: None,
    },
    HelpCommand {
        name: "observer",
        description: "Inspect server subsystems",
        badge: None,
    },
    HelpCommand {
        name: "wait",
        description: "Wait for async work",
        badge: None,
    },
    HelpCommand {
        name: "task",
        description: "Track async tasks",
        badge: None,
    },
    HelpCommand {
        name: "version",
        description: "Show CLI version",
        badge: None,
    },
];

const IMPORT_EXPORT_SESSIONS: &[HelpCommand] = &[
    HelpCommand {
        name: "import",
        description: "Import .ovpack",
        badge: None,
    },
    HelpCommand {
        name: "export",
        description: "Export context as .ovpack",
        badge: None,
    },
    HelpCommand {
        name: "backup",
        description: "Create restore-only backup",
        badge: None,
    },
    HelpCommand {
        name: "restore",
        description: "Restore backup",
        badge: None,
    },
    HelpCommand {
        name: "session",
        description: "Manage sessions",
        badge: None,
    },
    HelpCommand {
        name: "privacy",
        description: "Manage privacy config",
        badge: None,
    },
];

const INTERACTIVE_ADMIN: &[HelpCommand] = &[
    HelpCommand {
        name: "tui",
        description: "Interactive file explorer",
        badge: None,
    },
    HelpCommand {
        name: "chat",
        description: "Chat with vikingbot",
        badge: None,
    },
    HelpCommand {
        name: "admin",
        description: "Account and user management",
        badge: None,
    },
    HelpCommand {
        name: "system",
        description: "System utilities",
        badge: None,
    },
    HelpCommand {
        name: "reindex",
        description: "Reindex semantic/vector artifacts",
        badge: None,
    },
    HelpCommand {
        name: "relations",
        description: "List resource relations",
        badge: Some("experimental"),
    },
    HelpCommand {
        name: "link",
        description: "Create relation links",
        badge: Some("experimental"),
    },
    HelpCommand {
        name: "unlink",
        description: "Remove relation links",
        badge: Some("experimental"),
    },
];

const HELP_SECTIONS: &[HelpSection] = &[
    HelpSection {
        title: "Core Workflow",
        commands: CORE_WORKFLOW,
    },
    HelpSection {
        title: "Filesystem",
        commands: FILESYSTEM,
    },
    HelpSection {
        title: "Search & Context",
        commands: SEARCH_CONTEXT,
    },
    HelpSection {
        title: "Config & Status",
        commands: CONFIG_STATUS,
    },
    HelpSection {
        title: "Import, Export & Sessions",
        commands: IMPORT_EXPORT_SESSIONS,
    },
    HelpSection {
        title: "Interactive & Admin",
        commands: INTERACTIVE_ADMIN,
    },
];

const GLOBAL_OPTIONS: &[HelpItem] = &[
    HelpItem {
        label: "-o, --output <table|json>",
        description: "Choose human table output or machine-readable JSON.",
    },
    HelpItem {
        label: "-c, --compact <bool>",
        description: "Use compact table/JSON rendering.",
    },
    HelpItem {
        label: "--account <account>",
        description: "Override X-OpenViking-Account for this command.",
    },
    HelpItem {
        label: "--user <user>",
        description: "Override X-OpenViking-User for this command.",
    },
];

const COMMAND_HELP_SPECS: &[CommandHelpSpec] = &[
    CommandHelpSpec {
        path: &["add-resource"],
        purpose: "Import a local file, folder, URL, or repository into OpenViking.",
        usage: "ov add-resource <path-or-url> [--parent <uri>|--to <uri>] [--wait]",
        examples: &[
            HelpItem {
                label: "ov add-resource ./docs --parent viking://projects/acme --wait",
                description: "Import a folder and wait for processing.",
            },
            HelpItem {
                label: "ov add-resource https://example.com/spec.md --to viking://specs/api.md",
                description: "Import a URL to an exact target URI.",
            },
        ],
        arguments: &[HelpItem {
            label: "<path-or-url>",
            description: "Local path, URL, or repository to import.",
        }],
        common_options: &[
            HelpItem {
                label: "--parent <uri>",
                description: "Import under an existing directory URI.",
            },
            HelpItem {
                label: "-p, --parent-auto-create <uri>",
                description: "Create the parent directory if missing.",
            },
            HelpItem {
                label: "--to <uri>",
                description: "Import to an exact new resource URI.",
            },
            HelpItem {
                label: "--wait",
                description: "Wait until indexing/processing completes.",
            },
            HelpItem {
                label: "--include / --exclude",
                description: "Filter files during folder import.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--reason <text>",
                description: "Attach an import reason.",
            },
            HelpItem {
                label: "--instruction <text>",
                description: "Attach processing instructions.",
            },
            HelpItem {
                label: "--watch-interval <minutes>",
                description: "Set automatic refresh cadence.",
            },
            HelpItem {
                label: "--progress / --no-progress",
                description: "Override local upload progress display.",
            },
            HelpItem {
                label: "-v, --verbose",
                description: "Print upload diagnostics.",
            },
        ],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov task list",
                description: "Inspect async processing tasks.",
            },
            HelpItem {
                label: "ov find \"query\"",
                description: "Retrieve the imported context.",
            },
            HelpItem {
                label: "ov tree <uri>",
                description: "Browse where the resource landed.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["add-skill"],
        purpose: "Import a skill directory, SKILL.md file, or raw skill content.",
        usage: "ov add-skill <skill-path-or-content> [--wait]",
        examples: &[
            HelpItem {
                label: "ov add-skill ./skills/my-skill --wait",
                description: "Import a local skill folder.",
            },
            HelpItem {
                label: "ov add-skill ./skills/my-skill/SKILL.md",
                description: "Import a single skill definition.",
            },
        ],
        arguments: &[HelpItem {
            label: "<skill-path-or-content>",
            description: "Skill folder, SKILL.md path, or raw content.",
        }],
        common_options: &[
            HelpItem {
                label: "--wait",
                description: "Wait until skill processing completes.",
            },
            HelpItem {
                label: "--timeout <seconds>",
                description: "Maximum wait time when using --wait.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--progress / --no-progress",
                description: "Override local upload progress display.",
            },
            HelpItem {
                label: "-v, --verbose",
                description: "Print upload diagnostics.",
            },
        ],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov find \"skill topic\"",
                description: "Search imported skill context.",
            },
            HelpItem {
                label: "ov task list",
                description: "Check processing status.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["ls"],
        purpose: "List resources under a Viking URI.",
        usage: "ov ls [uri] [--recursive] [--all]",
        examples: &[
            HelpItem {
                label: "ov ls",
                description: "List the root scope.",
            },
            HelpItem {
                label: "ov ls viking://projects/acme --recursive",
                description: "List a subtree recursively.",
            },
        ],
        arguments: &[HelpItem {
            label: "[uri]",
            description: "Directory URI to list. Defaults to viking://.",
        }],
        common_options: &[
            HelpItem {
                label: "-r, --recursive",
                description: "List nested directories recursively.",
            },
            HelpItem {
                label: "-s, --simple",
                description: "Print only paths.",
            },
            HelpItem {
                label: "-a, --all",
                description: "Include hidden files.",
            },
            HelpItem {
                label: "-n, --node-limit <n>",
                description: "Limit number of listed nodes.",
            },
        ],
        advanced_options: &[HelpItem {
            label: "-l, --abs-limit <n>",
            description: "Limit abstract text in agent-oriented output.",
        }],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov tree <uri>",
                description: "See a hierarchy view.",
            },
            HelpItem {
                label: "ov read <uri>",
                description: "Read a file resource.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["tree"],
        purpose: "Show a hierarchical view of resources under a URI.",
        usage: "ov tree <uri> [--level-limit <n>] [--node-limit <n>]",
        examples: &[HelpItem {
            label: "ov tree viking://projects/acme -L 4",
            description: "Show a project tree up to depth 4.",
        }],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Directory URI to inspect.",
        }],
        common_options: &[
            HelpItem {
                label: "-L, --level-limit <n>",
                description: "Maximum traversal depth.",
            },
            HelpItem {
                label: "-n, --node-limit <n>",
                description: "Maximum number of nodes.",
            },
            HelpItem {
                label: "-a, --all",
                description: "Include hidden files.",
            },
        ],
        advanced_options: &[HelpItem {
            label: "-l, --abs-limit <n>",
            description: "Limit abstract text in agent-oriented output.",
        }],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov read <uri>",
                description: "Open an exact resource.",
            },
            HelpItem {
                label: "ov find \"query\" -u <uri>",
                description: "Search inside this subtree.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["mkdir"],
        purpose: "Create a directory in OpenViking.",
        usage: "ov mkdir <uri> [--description <text>]",
        examples: &[HelpItem {
            label: "ov mkdir viking://projects/acme --description \"ACME project context\"",
            description: "Create a project folder with a description.",
        }],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Directory URI to create.",
        }],
        common_options: &[HelpItem {
            label: "--description <text>",
            description: "Initial directory description.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov add-resource ./docs --parent <uri>",
            description: "Import content into the new directory.",
        }],
    },
    CommandHelpSpec {
        path: &["rm"],
        purpose: "Remove a resource from OpenViking.",
        usage: "ov rm <uri> [--recursive]",
        examples: &[
            HelpItem {
                label: "ov rm viking://scratch/old-note.md",
                description: "Remove one file resource.",
            },
            HelpItem {
                label: "ov rm viking://scratch --recursive",
                description: "Remove a directory subtree.",
            },
        ],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Resource URI to remove.",
        }],
        common_options: &[HelpItem {
            label: "-r, --recursive",
            description: "Required for directory/subtree removal.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov ls <parent-uri>",
                description: "Confirm the resource is gone.",
            },
            HelpItem {
                label: "ov tree <parent-uri>",
                description: "Review remaining resources.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["mv"],
        purpose: "Move or rename a resource.",
        usage: "ov mv <from-uri> <to-uri>",
        examples: &[HelpItem {
            label: "ov mv viking://notes/draft.md viking://notes/final.md",
            description: "Rename a file resource.",
        }],
        arguments: &[
            HelpItem {
                label: "<from-uri>",
                description: "Existing resource URI.",
            },
            HelpItem {
                label: "<to-uri>",
                description: "Destination URI.",
            },
        ],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov stat <to-uri>",
                description: "Confirm the resource metadata.",
            },
            HelpItem {
                label: "ov read <to-uri>",
                description: "Read the moved resource.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["stat"],
        purpose: "Show metadata for one resource.",
        usage: "ov stat <uri>",
        examples: &[HelpItem {
            label: "ov stat viking://projects/acme/spec.md",
            description: "Inspect resource metadata.",
        }],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Resource URI to inspect.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov read <uri>",
                description: "Read the resource content.",
            },
            HelpItem {
                label: "ov relations <uri>",
                description: "Inspect related resources.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["read"],
        purpose: "Read exact Level 2 file content from a Viking URI.",
        usage: "ov read <uri>",
        examples: &[HelpItem {
            label: "ov read viking://projects/acme/spec.md",
            description: "Print exact file content.",
        }],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "File resource URI.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov write <uri> --content \"...\"",
                description: "Update this resource.",
            },
            HelpItem {
                label: "ov find \"query\" -u <parent-uri>",
                description: "Find related context nearby.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["abstract"],
        purpose: "Read Level 0 abstract content for a directory.",
        usage: "ov abstract <directory-uri>",
        examples: &[HelpItem {
            label: "ov abstract viking://projects/acme",
            description: "Read the compact directory abstract.",
        }],
        arguments: &[HelpItem {
            label: "<directory-uri>",
            description: "Directory URI.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov overview <directory-uri>",
            description: "Read a richer Level 1 overview.",
        }],
    },
    CommandHelpSpec {
        path: &["overview"],
        purpose: "Read Level 1 overview content for a directory.",
        usage: "ov overview <directory-uri>",
        examples: &[HelpItem {
            label: "ov overview viking://projects/acme",
            description: "Read the directory overview.",
        }],
        arguments: &[HelpItem {
            label: "<directory-uri>",
            description: "Directory URI.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov read <file-uri>",
            description: "Open exact Level 2 content.",
        }],
    },
    CommandHelpSpec {
        path: &["write"],
        purpose: "Update text content in an existing resource.",
        usage: "ov write <uri> (--content <text>|--from-file <path>) [--append|--mode <mode>]",
        examples: &[
            HelpItem {
                label: "ov write viking://notes/todo.md --content \"Ship config UX\"",
                description: "Replace a file with inline text.",
            },
            HelpItem {
                label: "ov write viking://notes/todo.md --from-file ./todo.md --wait",
                description: "Write from disk and wait for processing.",
            },
        ],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Existing resource URI.",
        }],
        common_options: &[
            HelpItem {
                label: "--content <text>",
                description: "Inline replacement content.",
            },
            HelpItem {
                label: "--from-file <path>",
                description: "Read replacement content from disk.",
            },
            HelpItem {
                label: "--append",
                description: "Append instead of replacing.",
            },
            HelpItem {
                label: "--wait",
                description: "Wait for async processing.",
            },
        ],
        advanced_options: &[HelpItem {
            label: "--mode <replace|append|create>",
            description: "Explicit write mode.",
        }],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov read <uri>",
                description: "Confirm the updated content.",
            },
            HelpItem {
                label: "ov task list",
                description: "Inspect processing if not using --wait.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["get"],
        purpose: "Download a file resource to a local path.",
        usage: "ov get <uri> <local-path>",
        examples: &[HelpItem {
            label: "ov get viking://assets/logo.png ./logo.png",
            description: "Download a binary or text file.",
        }],
        arguments: &[
            HelpItem {
                label: "<uri>",
                description: "File resource URI.",
            },
            HelpItem {
                label: "<local-path>",
                description: "Destination path that does not already exist.",
            },
        ],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov stat <uri>",
            description: "Inspect source metadata.",
        }],
    },
    CommandHelpSpec {
        path: &["find"],
        purpose: "Retrieve relevant OpenViking context semantically.",
        usage: "ov find <query> [--uri <uri>] [--node-limit <n>]",
        examples: &[
            HelpItem {
                label: "ov find \"deployment rollback steps\"",
                description: "Search all accessible context.",
            },
            HelpItem {
                label: "ov find \"auth flow\" -u viking://projects/acme -L 1,2",
                description: "Search a subtree and include overview/file results.",
            },
        ],
        arguments: &[HelpItem {
            label: "<query>",
            description: "Natural-language search query.",
        }],
        common_options: &[
            HelpItem {
                label: "-u, --uri <uri>",
                description: "Limit search to a subtree.",
            },
            HelpItem {
                label: "-n, --node-limit <n>",
                description: "Maximum final results returned.",
            },
            HelpItem {
                label: "-t, --threshold <score>",
                description: "Minimum relevance score.",
            },
            HelpItem {
                label: "-L, --level <0,1,2>",
                description: "Filter abstract, overview, or file results.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--after <time>",
                description: "Only include newer results, e.g. 48h or 2026-03-10.",
            },
            HelpItem {
                label: "--before <time>",
                description: "Only include older results, e.g. 24h or ISO-8601.",
            },
        ],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov read <uri>",
                description: "Open an exact result.",
            },
            HelpItem {
                label: "ov tree <uri>",
                description: "Explore the result's neighborhood.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["search"],
        purpose: "Run experimental context-aware retrieval, optionally scoped to a session.",
        usage: "ov search <query> [--session-id <id>] [--uri <uri>]",
        examples: &[HelpItem {
            label: "ov search \"what changed last time?\" --session-id abc123",
            description: "Search with session context.",
        }],
        arguments: &[HelpItem {
            label: "<query>",
            description: "Natural-language search query.",
        }],
        common_options: &[
            HelpItem {
                label: "--session-id <id>",
                description: "Session context for retrieval.",
            },
            HelpItem {
                label: "-u, --uri <uri>",
                description: "Limit search to a subtree.",
            },
            HelpItem {
                label: "-n, --node-limit <n>",
                description: "Maximum results per search pass. Search may merge multiple passes.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "-t, --threshold <score>",
                description: "Minimum relevance score.",
            },
            HelpItem {
                label: "--after / --before",
                description: "Time-bound results.",
            },
            HelpItem {
                label: "-L, --level <0,1,2>",
                description: "Filter by context level.",
            },
        ],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov session get-session-context <id>",
            description: "Inspect the session context directly.",
        }],
    },
    CommandHelpSpec {
        path: &["grep"],
        purpose: "Search resource content with a text pattern.",
        usage: "ov grep <pattern> [--uri <uri>] [--ignore-case]",
        examples: &[HelpItem {
            label: "ov grep \"TODO\" -u viking://projects/acme -i",
            description: "Find case-insensitive matches in a subtree.",
        }],
        arguments: &[HelpItem {
            label: "<pattern>",
            description: "Text pattern to search for.",
        }],
        common_options: &[
            HelpItem {
                label: "-u, --uri <uri>",
                description: "Search root. Defaults to viking://.",
            },
            HelpItem {
                label: "-i, --ignore-case",
                description: "Match case-insensitively.",
            },
            HelpItem {
                label: "-n, --node-limit <n>",
                description: "Maximum number of results.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "-x, --exclude-uri <uri>",
                description: "Skip matches under a URI prefix.",
            },
            HelpItem {
                label: "-L, --level-limit <n>",
                description: "Maximum traversal depth.",
            },
        ],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov read <uri>",
            description: "Open a matching resource.",
        }],
    },
    CommandHelpSpec {
        path: &["glob"],
        purpose: "Find resources by glob pattern.",
        usage: "ov glob <pattern> [--uri <uri>]",
        examples: &[HelpItem {
            label: "ov glob \"**/*.md\" -u viking://projects/acme",
            description: "Find Markdown files in a project.",
        }],
        arguments: &[HelpItem {
            label: "<pattern>",
            description: "Glob pattern to match resource paths.",
        }],
        common_options: &[
            HelpItem {
                label: "-u, --uri <uri>",
                description: "Search root. Defaults to viking://.",
            },
            HelpItem {
                label: "-n, --node-limit <n>",
                description: "Maximum number of results.",
            },
        ],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov read <uri>",
            description: "Read one matched file.",
        }],
    },
    CommandHelpSpec {
        path: &["session"],
        purpose: "Manage sessions, messages, archives, and committed session context.",
        usage: "ov session <subcommand>",
        examples: &[
            HelpItem {
                label: "ov session new",
                description: "Create a new session.",
            },
            HelpItem {
                label: "ov session add-message <id> --role user --content \"...\"",
                description: "Append a message.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "new",
                description: "Create a session.",
            },
            HelpItem {
                label: "list",
                description: "List sessions.",
            },
            HelpItem {
                label: "get <id>",
                description: "Show session details.",
            },
            HelpItem {
                label: "get-session-context <id>",
                description: "Read merged session context.",
            },
            HelpItem {
                label: "add-message <id>",
                description: "Append one message.",
            },
            HelpItem {
                label: "commit <id>",
                description: "Archive messages and extract memories.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov session <subcommand> --help",
            description: "Show exact arguments for a session operation.",
        }],
    },
    CommandHelpSpec {
        path: &["add-memory"],
        purpose: "Add a memory directly from text or JSON messages.",
        usage: "ov add-memory <content>",
        examples: &[
            HelpItem {
                label: "ov add-memory \"The deployment owner is Alice\"",
                description: "Add one plain user memory.",
            },
            HelpItem {
                label: "ov add-memory '{\"role\":\"user\",\"content\":\"remember this\"}'",
                description: "Add one structured message.",
            },
        ],
        arguments: &[HelpItem {
            label: "<content>",
            description: "Plain text, one JSON message, or a JSON message array.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov find \"memory topic\"",
            description: "Verify the memory is retrievable.",
        }],
    },
    CommandHelpSpec {
        path: &["privacy"],
        purpose: "Manage privacy config categories, targets, versions, and activation.",
        usage: "ov privacy <subcommand>",
        examples: &[
            HelpItem {
                label: "ov privacy categories",
                description: "List privacy categories.",
            },
            HelpItem {
                label: "ov privacy get <category> <target>",
                description: "Show active values for one target.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "categories",
                description: "List categories.",
            },
            HelpItem {
                label: "list <category>",
                description: "List targets.",
            },
            HelpItem {
                label: "get <category> <target>",
                description: "Show active config.",
            },
            HelpItem {
                label: "upsert <category> <target>",
                description: "Update values.",
            },
            HelpItem {
                label: "versions / version / activate",
                description: "Inspect or activate versions.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov privacy <subcommand> --help",
            description: "Show exact arguments for a privacy operation.",
        }],
    },
    CommandHelpSpec {
        path: &["relations"],
        purpose: "List relation links for one resource. Experimental.",
        usage: "ov relations <uri>",
        examples: &[HelpItem {
            label: "ov relations viking://projects/acme/spec.md",
            description: "Inspect linked resources.",
        }],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Source resource URI.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov link <from-uri> <to-uri>",
                description: "Create a relation.",
            },
            HelpItem {
                label: "ov unlink <from-uri> <to-uri>",
                description: "Remove a relation.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["link"],
        purpose: "Create one or more relation links between resources. Experimental.",
        usage: "ov link <from-uri> <to-uri>... [--reason <text>]",
        examples: &[HelpItem {
            label: "ov link viking://a.md viking://b.md --reason \"related design\"",
            description: "Link two resources with a reason.",
        }],
        arguments: &[
            HelpItem {
                label: "<from-uri>",
                description: "Source resource URI.",
            },
            HelpItem {
                label: "<to-uri>...",
                description: "One or more target URIs.",
            },
        ],
        common_options: &[HelpItem {
            label: "--reason <text>",
            description: "Why these resources are linked.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov relations <from-uri>",
            description: "Confirm the relation.",
        }],
    },
    CommandHelpSpec {
        path: &["unlink"],
        purpose: "Remove one relation link between resources. Experimental.",
        usage: "ov unlink <from-uri> <to-uri>",
        examples: &[HelpItem {
            label: "ov unlink viking://a.md viking://b.md",
            description: "Remove a relation.",
        }],
        arguments: &[
            HelpItem {
                label: "<from-uri>",
                description: "Source resource URI.",
            },
            HelpItem {
                label: "<to-uri>",
                description: "Target URI to unlink.",
            },
        ],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov relations <from-uri>",
            description: "Confirm the relation is gone.",
        }],
    },
    CommandHelpSpec {
        path: &["export"],
        purpose: "Export context from a URI as an .ovpack file.",
        usage: "ov export <uri> <output.ovpack> [--include-vectors]",
        examples: &[HelpItem {
            label: "ov export viking://projects/acme ./acme.ovpack",
            description: "Export a project subtree.",
        }],
        arguments: &[
            HelpItem {
                label: "<uri>",
                description: "Source URI to export.",
            },
            HelpItem {
                label: "<output.ovpack>",
                description: "Output file path.",
            },
        ],
        common_options: &[HelpItem {
            label: "--include-vectors",
            description: "Include compatible dense vector snapshots.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov import ./file.ovpack <target-uri>",
            description: "Import the exported pack elsewhere.",
        }],
    },
    CommandHelpSpec {
        path: &["backup"],
        purpose: "Create a restore-only backup .ovpack for public OpenViking scopes.",
        usage: "ov backup <output.ovpack> [--include-vectors]",
        examples: &[HelpItem {
            label: "ov backup ./openviking-backup.ovpack --include-vectors",
            description: "Create a backup with vectors when compatible.",
        }],
        arguments: &[HelpItem {
            label: "<output.ovpack>",
            description: "Backup file path.",
        }],
        common_options: &[HelpItem {
            label: "--include-vectors",
            description: "Include compatible dense vector snapshots.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov restore ./openviking-backup.ovpack",
            description: "Restore this backup later.",
        }],
    },
    CommandHelpSpec {
        path: &["import"],
        purpose: "Import an .ovpack into a target URI.",
        usage: "ov import <file.ovpack> <target-uri> [--on-conflict <policy>]",
        examples: &[HelpItem {
            label: "ov import ./acme.ovpack viking://imports/acme --on-conflict skip",
            description: "Import while keeping existing resources.",
        }],
        arguments: &[
            HelpItem {
                label: "<file.ovpack>",
                description: "Input pack file.",
            },
            HelpItem {
                label: "<target-uri>",
                description: "Target parent URI.",
            },
        ],
        common_options: &[
            HelpItem {
                label: "--on-conflict <fail|overwrite|skip>",
                description: "Choose how to handle existing resources.",
            },
            HelpItem {
                label: "--vector-mode <auto|recompute|require>",
                description: "Choose vector snapshot handling.",
            },
        ],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov tree <target-uri>",
                description: "Inspect imported resources.",
            },
            HelpItem {
                label: "ov find \"query\" -u <target-uri>",
                description: "Search imported content.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["restore"],
        purpose: "Restore a backup .ovpack to its original public scope roots.",
        usage: "ov restore <backup.ovpack> [--on-conflict <policy>]",
        examples: &[HelpItem {
            label: "ov restore ./openviking-backup.ovpack --on-conflict fail",
            description: "Restore only if there are no conflicts.",
        }],
        arguments: &[HelpItem {
            label: "<backup.ovpack>",
            description: "Backup pack file.",
        }],
        common_options: &[
            HelpItem {
                label: "--on-conflict <fail|overwrite|skip>",
                description: "Choose how to handle existing resources.",
            },
            HelpItem {
                label: "--vector-mode <auto|recompute|require>",
                description: "Choose vector snapshot handling.",
            },
        ],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov status",
                description: "Check service health after restore.",
            },
            HelpItem {
                label: "ov tree viking://",
                description: "Inspect restored resources.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["tui"],
        purpose: "Open the interactive file explorer.",
        usage: "ov tui [uri]",
        examples: &[HelpItem {
            label: "ov tui viking://projects/acme",
            description: "Browse a project subtree interactively.",
        }],
        arguments: &[HelpItem {
            label: "[uri]",
            description: "Start URI. Defaults to /.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov tree <uri>",
            description: "Use a non-interactive tree view instead.",
        }],
    },
    CommandHelpSpec {
        path: &["chat"],
        purpose: "Chat with the vikingbot agent.",
        usage: "ov chat [--message <text>] [--session <id>]",
        examples: &[
            HelpItem {
                label: "ov chat",
                description: "Start interactive chat.",
            },
            HelpItem {
                label: "ov chat --message \"summarize project ACME\"",
                description: "Send one message.",
            },
        ],
        arguments: &[],
        common_options: &[
            HelpItem {
                label: "-m, --message <text>",
                description: "Send one message instead of interactive input.",
            },
            HelpItem {
                label: "-s, --session <id>",
                description: "Use a specific chat session.",
            },
            HelpItem {
                label: "--no-format",
                description: "Disable rich formatting.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--sender <id>",
                description: "Set sender ID.",
            },
            HelpItem {
                label: "--stream <bool>",
                description: "Enable or disable streaming.",
            },
            HelpItem {
                label: "--no-history",
                description: "Disable command history.",
            },
        ],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov find \"topic\"",
            description: "Search context directly.",
        }],
    },
    CommandHelpSpec {
        path: &["wait"],
        purpose: "Wait for queued async processing to complete.",
        usage: "ov wait [--timeout <seconds>]",
        examples: &[HelpItem {
            label: "ov wait --timeout 120",
            description: "Wait up to two minutes.",
        }],
        arguments: &[],
        common_options: &[HelpItem {
            label: "--timeout <seconds>",
            description: "Maximum wait time.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov task list",
                description: "Inspect remaining work.",
            },
            HelpItem {
                label: "ov status",
                description: "Check backend health.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["task"],
        purpose: "Inspect and manage async processing tasks.",
        usage: "ov task <subcommand>",
        examples: &[
            HelpItem {
                label: "ov task list --status failed",
                description: "List failed tasks.",
            },
            HelpItem {
                label: "ov task status <task-id>",
                description: "Inspect one task.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "status <task-id>",
                description: "Show one task.",
            },
            HelpItem {
                label: "list",
                description: "List tracked tasks.",
            },
            HelpItem {
                label: "watch <subcommand>",
                description: "Manage auto-refresh subscriptions.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov wait",
            description: "Wait for queued work.",
        }],
    },
    CommandHelpSpec {
        path: &["status"],
        purpose: "Show OpenViking server readiness and component status.",
        usage: "ov status [--verbose]",
        examples: &[
            HelpItem {
                label: "ov status",
                description: "Check config, connection, models, queue, and component health.",
            },
            HelpItem {
                label: "ov status --verbose",
                description: "Show full component tables.",
            },
        ],
        arguments: &[],
        common_options: &[HelpItem {
            label: "--verbose",
            description: "Show full component tables instead of the curated diagnostic view.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov health",
                description: "Run a lightweight connectivity check.",
            },
            HelpItem {
                label: "ov config validate",
                description: "Validate active CLI config.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["observer"],
        purpose: "Inspect specific OpenViking server subsystems.",
        usage: "ov observer <subcommand>",
        examples: &[
            HelpItem {
                label: "ov observer models",
                description: "Inspect VLM, embedding, and rerank model status.",
            },
            HelpItem {
                label: "ov observer queue",
                description: "Inspect queue status.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "queue",
                description: "Queue status.",
            },
            HelpItem {
                label: "models",
                description: "Model status.",
            },
            HelpItem {
                label: "transaction",
                description: "Transaction system status.",
            },
            HelpItem {
                label: "filesystem / retrieval / system",
                description: "Operational metrics.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov status",
            description: "Return to the full status view.",
        }],
    },
    CommandHelpSpec {
        path: &["health"],
        purpose: "Run a quick server reachability check.",
        usage: "ov health",
        examples: &[HelpItem {
            label: "ov health",
            description: "Check whether the active server is reachable.",
        }],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config validate",
                description: "Probe the active config if health fails.",
            },
            HelpItem {
                label: "ov status",
                description: "Inspect detailed backend status.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config"],
        purpose: "Add, edit, delete, show, validate, or switch OpenViking CLI configs.",
        usage: "ov config [show|validate|switch|list|add|edit|delete]",
        examples: &[
            HelpItem {
                label: "ov config",
                description: "Open the interactive config manager.",
            },
            HelpItem {
                label: "ov config add cloud --api-key-stdin --activate",
                description: "Create and activate a cloud config from stdin.",
            },
            HelpItem {
                label: "ov config list -o json",
                description: "List saved configs for automation.",
            },
            HelpItem {
                label: "ov config validate",
                description: "Probe the active config.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "show",
                description: "Print active config with secrets redacted.",
            },
            HelpItem {
                label: "validate",
                description: "Probe the active server/auth config.",
            },
            HelpItem {
                label: "switch",
                description: "Switch the active saved config interactively or by name.",
            },
            HelpItem {
                label: "list",
                description: "List saved configs.",
            },
            HelpItem {
                label: "add",
                description: "Add a cloud or self-managed config without prompts.",
            },
            HelpItem {
                label: "edit",
                description: "Edit a saved config without prompts.",
            },
            HelpItem {
                label: "delete",
                description: "Delete a saved config without prompts.",
            },
        ],
        next_steps: &[
            HelpItem {
                label: "ov config validate",
                description: "Confirm the active config works.",
            },
            HelpItem {
                label: "ov --help",
                description: "See all commands.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "show"],
        purpose: "Print the active CLI config with secrets redacted.",
        usage: "ov config show",
        examples: &[HelpItem {
            label: "ov config show",
            description: "Show the active server URL, config name, and safe fields.",
        }],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config",
                description: "Edit saved configs.",
            },
            HelpItem {
                label: "ov config validate",
                description: "Probe the active config.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "validate"],
        purpose: "Parse the active config and probe the configured OpenViking server.",
        usage: "ov config validate",
        examples: &[HelpItem {
            label: "ov config validate",
            description: "Check active URL, auth, and server reachability.",
        }],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config",
                description: "Fix or replace a failing config.",
            },
            HelpItem {
                label: "ov health",
                description: "Run a quick health check.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "switch"],
        purpose: "Switch the active CLI config to a saved config.",
        usage: "ov config switch [name]",
        examples: &[
            HelpItem {
                label: "ov config switch",
                description: "Choose a saved config interactively.",
            },
            HelpItem {
                label: "ov config switch prod",
                description: "Activate a saved config without prompts.",
            },
        ],
        arguments: &[HelpItem {
            label: "name",
            description: "Optional saved config name. Omit it for the interactive picker.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config show",
                description: "Confirm the new active config.",
            },
            HelpItem {
                label: "ov config validate",
                description: "Probe the switched config.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "list"],
        purpose: "List saved CLI configs and mark which one is active.",
        usage: "ov config list",
        examples: &[
            HelpItem {
                label: "ov config list",
                description: "Show saved configs in a readable table.",
            },
            HelpItem {
                label: "ov config list -o json",
                description: "Return saved configs as JSON for automation.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config switch <name>",
                description: "Activate a saved config.",
            },
            HelpItem {
                label: "ov config add --help",
                description: "Create a new saved config.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "add"],
        purpose: "Create a saved CLI config without opening the interactive wizard.",
        usage: "ov config add <cloud|self-managed> [options]",
        examples: &[
            HelpItem {
                label: "printf '%s' \"$OV_KEY\" | ov config add cloud --api-key-stdin --activate",
                description: "Create and activate a Volcengine Cloud config.",
            },
            HelpItem {
                label: "ov config add self-managed --name local --url http://127.0.0.1:1933 --activate",
                description: "Create and activate a local self-managed config.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "cloud",
                description: "Use the fixed Volcengine Cloud endpoint.",
            },
            HelpItem {
                label: "self-managed",
                description: "Use a local or hosted self-managed endpoint.",
            },
        ],
        next_steps: &[
            HelpItem {
                label: "ov config add cloud --help",
                description: "See cloud-specific flags.",
            },
            HelpItem {
                label: "ov config add self-managed --help",
                description: "See self-managed flags.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "add", "cloud"],
        purpose: "Create a Volcengine Cloud config without prompts.",
        usage: "ov config add cloud [--name <name>] (--api-key-stdin|--api-key-env <env>) [--account <account> --user <user>] [--activate] [--force]",
        examples: &[
            HelpItem {
                label: "printf '%s' \"$OV_KEY\" | ov config add cloud --name prod --api-key-stdin --activate",
                description: "Read the API key from stdin and make the config active.",
            },
            HelpItem {
                label: "ov config add cloud --api-key-env OV_KEY -o json",
                description: "Read the API key from an environment variable and print JSON.",
            },
        ],
        arguments: &[],
        common_options: &[
            HelpItem {
                label: "--name <name>",
                description: "Saved config name. Generated if omitted.",
            },
            HelpItem {
                label: "--api-key-stdin",
                description: "Read the API key from stdin.",
            },
            HelpItem {
                label: "--api-key-env <env>",
                description: "Read the API key from an environment variable.",
            },
            HelpItem {
                label: "--activate",
                description: "Also write the active ovcli.conf.",
            },
            HelpItem {
                label: "--force",
                description: "Replace an existing saved config.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--account <account>",
                description: "Optional account identity override.",
            },
            HelpItem {
                label: "--user <user>",
                description: "Optional user identity override.",
            },
        ],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config validate",
                description: "Validate the active config.",
            },
            HelpItem {
                label: "ov config list",
                description: "Inspect saved configs.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "add", "self-managed"],
        purpose: "Create a self-managed config without prompts.",
        usage: "ov config add self-managed [--name <name>] [--url <url>] [--api-key-stdin|--api-key-env <env>] [--root-api-key-stdin|--root-api-key-env <env>] [--account <account>] [--user <user>] [--activate] [--force]",
        examples: &[
            HelpItem {
                label: "ov config add self-managed --name local --url http://127.0.0.1:1933 --activate",
                description: "Create a local no-key config.",
            },
            HelpItem {
                label: "ov config add self-managed --url https://ov.example.com --api-key-env OV_KEY --activate",
                description: "Create a hosted self-managed config with an API key.",
            },
        ],
        arguments: &[],
        common_options: &[
            HelpItem {
                label: "--name <name>",
                description: "Saved config name. Generated if omitted.",
            },
            HelpItem {
                label: "--url <url>",
                description: "Server URL. Defaults to http://127.0.0.1:1933.",
            },
            HelpItem {
                label: "--api-key-stdin / --api-key-env <env>",
                description: "Read a normal API key from stdin or an environment variable.",
            },
            HelpItem {
                label: "--root-api-key-stdin / --root-api-key-env <env>",
                description: "Read a root API key from stdin or an environment variable.",
            },
            HelpItem {
                label: "--activate",
                description: "Also write the active ovcli.conf.",
            },
            HelpItem {
                label: "--force",
                description: "Replace an existing saved config.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--account <account>",
                description: "Account identity. Required when only a root key is supplied.",
            },
            HelpItem {
                label: "--user <user>",
                description: "User identity. Required when only a root key is supplied.",
            },
        ],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config validate",
                description: "Validate the active config.",
            },
            HelpItem {
                label: "ov config list",
                description: "Inspect saved configs.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "edit"],
        purpose: "Edit a saved CLI config without prompts.",
        usage: "ov config edit <name> [--new-name <name>] [--url <url>] [key options] [identity options] [--activate] [--force]",
        examples: &[
            HelpItem {
                label: "ov config edit prod --new-name production --activate",
                description: "Rename a saved config and make it active.",
            },
            HelpItem {
                label: "printf '%s' \"$OV_KEY\" | ov config edit prod --api-key-stdin --activate",
                description: "Replace the API key, validate, then activate.",
            },
            HelpItem {
                label: "ov config edit local --clear-api-key --activate",
                description: "Remove a normal API key from a saved config.",
            },
        ],
        arguments: &[HelpItem {
            label: "name",
            description: "Existing saved config name.",
        }],
        common_options: &[
            HelpItem {
                label: "--new-name <name>",
                description: "Rename the saved config.",
            },
            HelpItem {
                label: "--url <url>",
                description: "Replace the self-managed server URL.",
            },
            HelpItem {
                label: "--api-key-stdin / --api-key-env <env> / --clear-api-key",
                description: "Replace or clear the normal API key.",
            },
            HelpItem {
                label: "--root-api-key-stdin / --root-api-key-env <env> / --clear-root-api-key",
                description: "Replace or clear the root API key.",
            },
            HelpItem {
                label: "--activate",
                description: "Also make the edited config active.",
            },
            HelpItem {
                label: "--force",
                description: "Replace an existing target name when renaming.",
            },
        ],
        advanced_options: &[
            HelpItem {
                label: "--account <account>",
                description: "Replace account identity.",
            },
            HelpItem {
                label: "--user <user>",
                description: "Replace user identity.",
            },
        ],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config validate",
                description: "Validate the active config.",
            },
            HelpItem {
                label: "ov config list",
                description: "Inspect saved configs.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "delete"],
        purpose: "Delete a saved CLI config without prompts.",
        usage: "ov config delete <name> [--force]",
        examples: &[
            HelpItem {
                label: "ov config delete old-local",
                description: "Delete a non-active saved config.",
            },
            HelpItem {
                label: "ov config delete missing -o json",
                description: "Return a JSON no-op if the config is already absent.",
            },
        ],
        arguments: &[HelpItem {
            label: "name",
            description: "Saved config name to delete.",
        }],
        common_options: &[HelpItem {
            label: "--force",
            description: "Reserved for future destructive delete behavior.",
        }],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov config list",
                description: "Inspect remaining configs.",
            },
            HelpItem {
                label: "ov config switch <name>",
                description: "Switch away from an active config before deleting it.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["language"],
        purpose: "Choose the OpenViking CLI display language.",
        usage: "ov language [en|zh-CN]",
        examples: &[
            HelpItem {
                label: "ov language",
                description: "Open the language selector.",
            },
            HelpItem {
                label: "ov language zh-CN",
                description: "Switch display text to Simplified Chinese.",
            },
            HelpItem {
                label: "ov lang en",
                description: "Use the short alias to switch display text to English.",
            },
        ],
        arguments: &[HelpItem {
            label: "language",
            description: "Optional language code: en or zh-CN.",
        }],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov config",
            description: "Open the config manager.",
        }],
    },
    CommandHelpSpec {
        path: &["version"],
        purpose: "Print the OpenViking CLI version.",
        usage: "ov version",
        examples: &[HelpItem {
            label: "ov version",
            description: "Show the installed CLI version.",
        }],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[HelpItem {
            label: "ov --help",
            description: "See all commands.",
        }],
    },
    CommandHelpSpec {
        path: &["admin"],
        purpose: "Manage accounts, users, roles, and API keys. Admin/root access required.",
        usage: "ov admin <subcommand> [--sudo]",
        examples: &[
            HelpItem {
                label: "ov admin list-accounts --sudo",
                description: "List accounts with the root API key.",
            },
            HelpItem {
                label: "ov admin register-user <account> <user>",
                description: "Register a user in an account.",
            },
        ],
        arguments: &[],
        common_options: &[HelpItem {
            label: "--sudo",
            description: "Use root_api_key for root-only admin commands.",
        }],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "create-account / delete-account",
                description: "Create or remove an account.",
            },
            HelpItem {
                label: "list-accounts",
                description: "List accounts.",
            },
            HelpItem {
                label: "register-user / remove-user",
                description: "Manage account users.",
            },
            HelpItem {
                label: "set-role / regenerate-key",
                description: "Manage roles and API keys.",
            },
        ],
        next_steps: &[
            HelpItem {
                label: "ov config show",
                description: "Check whether root_api_key is configured.",
            },
            HelpItem {
                label: "ov admin <subcommand> --help",
                description: "Show exact arguments for an admin operation.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["system"],
        purpose: "Run server utility, health, consistency, and crypto commands.",
        usage: "ov system <subcommand>",
        examples: &[
            HelpItem {
                label: "ov system health",
                description: "Run server health through the system namespace.",
            },
            HelpItem {
                label: "ov system consistency viking://projects/acme",
                description: "Check filesystem/vector consistency.",
            },
        ],
        arguments: &[],
        common_options: &[],
        advanced_options: &[],
        subcommands: &[
            HelpItem {
                label: "wait / status / health",
                description: "Operational checks.",
            },
            HelpItem {
                label: "consistency <uri>",
                description: "Check subtree consistency.",
            },
            HelpItem {
                label: "crypto <subcommand>",
                description: "Key management commands.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov status",
            description: "Use the standard status view.",
        }],
    },
    CommandHelpSpec {
        path: &["reindex"],
        purpose: "Reindex semantic/vector artifacts for a URI.",
        usage: "ov reindex <uri> [--mode <mode>] [--wait <bool>] [--sudo]",
        examples: &[HelpItem {
            label: "ov reindex viking://projects/acme --mode vectors_only --wait true",
            description: "Rebuild vector artifacts and wait.",
        }],
        arguments: &[HelpItem {
            label: "<uri>",
            description: "Subtree URI to reindex.",
        }],
        common_options: &[
            HelpItem {
                label: "--mode <mode>",
                description: "Reindex mode. Defaults to vectors_only.",
            },
            HelpItem {
                label: "--wait <bool>",
                description: "Wait for completion. Defaults to true.",
            },
            HelpItem {
                label: "--sudo",
                description: "Use root API key when required.",
            },
        ],
        advanced_options: &[],
        subcommands: &[],
        next_steps: &[
            HelpItem {
                label: "ov task list",
                description: "Inspect reindex work.",
            },
            HelpItem {
                label: "ov find \"query\" -u <uri>",
                description: "Verify retrieval after reindexing.",
            },
        ],
    },
];

pub(crate) fn is_top_level_help_request(args: &[OsString]) -> bool {
    if args.len() != 2 {
        return false;
    }

    matches!(
        args[1].to_string_lossy().as_ref(),
        "--help" | "-h" | "-help"
    )
}

pub(crate) fn render_command_help_request(args: &[OsString]) -> Option<String> {
    let path = command_help_path(args)?;
    let spec = command_spec(&path)?;
    Some(render_command_help(spec))
}

pub(crate) fn render_top_level_help() -> String {
    render_top_level_help_with_language(Language::current())
}

pub(crate) fn render_top_level_help_with_language(language: Language) -> String {
    let mut lines = Vec::new();

    lines.push(format!(
        "{} {}",
        theme::brand_title("OpenViking").bold(),
        theme::version(version())
    ));
    lines.push(
        theme::heading(copy(
            language,
            "Context Database for AI Agents",
            "AI Agent 上下文数据库",
        ))
        .bold()
        .to_string(),
    );
    lines.push(String::new());
    lines.push(format!(
        "{}",
        theme::warning(copy(language, "Usage:", "用法：")).bold()
    ));
    lines.push(format!("  {}", theme::strong("ov <command> [options]")));
    lines.push(String::new());
    lines.push(format!(
        "{}",
        theme::strong(copy(language, "Start here:", "从这里开始："))
    ));
    lines.push(start_here_line(
        "ov config",
        copy(
            language,
            "Add, edit, or delete configs",
            "添加、编辑或删除配置",
        ),
    ));
    lines.push(start_here_line(
        "ov health",
        copy(language, "Check server reachability", "检查服务器连接"),
    ));
    lines.push(start_here_line(
        "ov status",
        copy(language, "Inspect server status", "查看服务器状态"),
    ));
    lines.push(start_here_line(
        "ov tui",
        copy(
            language,
            "Browse OpenViking interactively",
            "交互式浏览 OpenViking",
        ),
    ));
    lines.push(String::new());

    for section in HELP_SECTIONS {
        lines.extend(section_lines(section));
        lines.push(String::new());
    }

    lines.push(format!(
        "{}",
        theme::strong(copy(language, "Global options:", "全局选项："))
    ));
    lines.push(option_line(
        "-o, --output <table|json>",
        copy(language, "Output format", "输出格式"),
    ));
    lines.push(option_line(
        "-c, --compact",
        copy(language, "Compact output", "紧凑输出"),
    ));
    lines.push(option_line(
        "--account <account>",
        copy(language, "Override account", "覆盖账户"),
    ));
    lines.push(option_line(
        "--user <user>",
        copy(language, "Override user", "覆盖用户"),
    ));
    lines.push(option_line(
        "--sudo",
        copy(
            language,
            "Use root API key for admin commands",
            "管理命令使用 root API Key",
        ),
    ));
    lines.push(option_line(
        "-h, --help",
        copy(language, "Show help", "显示帮助"),
    ));
    lines.push(option_line(
        "-V, --version",
        copy(language, "Show version", "显示版本"),
    ));
    lines.push(String::new());
    lines.push(format!(
        "{}",
        theme::strong(copy(language, "More:", "更多："))
    ));
    lines.push(start_here_line(
        "ov <command> --help",
        copy(language, "Show command details", "查看命令详情"),
    ));
    lines.push(start_here_line(
        "ov config",
        copy(language, "Configure the CLI", "配置 CLI"),
    ));

    format!("{}\n", lines.join("\n"))
}

fn render_command_help(spec: &CommandHelpSpec) -> String {
    let mut lines = Vec::new();
    let language = Language::current();
    let command = command_display(spec.path);

    lines.push(format!(
        "{} {} {}",
        theme::brand_title("OpenViking").bold(),
        theme::version(version()),
        theme::muted(format!("· {command}"))
    ));
    lines.push(theme::body(localized_command_purpose(spec, language)).to_string());
    lines.push(String::new());
    lines.push(format!(
        "{}",
        theme::warning(copy(language, "Usage:", "用法：")).bold()
    ));
    lines.push(format!("  {}", theme::strong(spec.usage)));
    push_section(
        &mut lines,
        copy(language, "Examples", "示例"),
        spec.examples,
    );
    push_section(
        &mut lines,
        copy(language, "Arguments", "参数"),
        spec.arguments,
    );
    push_section(
        &mut lines,
        copy(language, "Subcommands", "子命令"),
        spec.subcommands,
    );
    push_section(
        &mut lines,
        copy(language, "Common options", "常用选项"),
        spec.common_options,
    );
    push_section(
        &mut lines,
        copy(language, "Advanced options", "高级选项"),
        spec.advanced_options,
    );
    push_section(
        &mut lines,
        copy(language, "Global options", "全局选项"),
        GLOBAL_OPTIONS,
    );
    push_section(
        &mut lines,
        copy(language, "Next", "下一步"),
        spec.next_steps,
    );

    format!("{}\n", lines.join("\n"))
}

fn push_section(lines: &mut Vec<String>, title: &str, items: &[HelpItem]) {
    if items.is_empty() {
        return;
    }

    lines.push(String::new());
    lines.push(format!("{}", theme::heading(title).bold()));
    for item in items {
        lines.push(help_item_line(item));
    }
}

fn help_item_line(item: &HelpItem) -> String {
    let language = Language::current();
    let description = localized_help_item_description(item.label, item.description, language);
    if display_width(item.label) > COMMAND_HELP_LEFT_WIDTH {
        return format!(
            "  {}\n      {}",
            theme::command(item.label),
            theme::body(description)
        );
    }

    format!(
        "  {} {}",
        theme::command(pad_to_display_width(item.label, COMMAND_HELP_LEFT_WIDTH)),
        theme::body(description)
    )
}

fn localized_command_purpose(spec: &CommandHelpSpec, language: Language) -> &str {
    if language == Language::En {
        return spec.purpose;
    }
    match spec.path {
        ["config"] => "添加、编辑、删除、显示、验证或切换 OpenViking CLI 配置。",
        ["config", "show"] => "显示当前 CLI 配置，并隐藏敏感信息。",
        ["config", "validate"] => "解析当前配置，并探测 OpenViking 服务器。",
        ["config", "switch"] => "切换到已保存的 CLI 配置。",
        ["config", "list"] => "列出已保存的 CLI 配置，并标记当前配置。",
        ["config", "add"] => "不打开交互式向导，创建已保存的 CLI 配置。",
        ["config", "add", "cloud"] => "不打开交互式向导，创建火山引擎云配置。",
        ["config", "add", "self-managed"] => "不打开交互式向导，创建自托管配置。",
        ["config", "edit"] => "不打开交互式向导，编辑已保存的 CLI 配置。",
        ["config", "delete"] => "不打开交互式向导，删除已保存的 CLI 配置。",
        ["health"] => "快速检查服务器是否可连接。",
        ["status"] => "查看 OpenViking 服务器诊断状态。",
        ["language"] => "选择 OpenViking CLI 显示语言。",
        _ => spec.purpose,
    }
}

fn localized_help_item_description<'a>(
    label: &str,
    description: &'a str,
    language: Language,
) -> &'a str {
    if language == Language::En {
        return description;
    }
    match label {
        "ov config" => "打开交互式配置管理。",
        "ov config validate" => "验证当前配置。",
        "show" => "显示当前配置，并隐藏敏感信息。",
        "validate" => "探测当前服务器和认证配置。",
        "switch" => "切换当前已保存配置。",
        "list" => "列出已保存的配置。",
        "add" => "不打开提示，添加云端或自托管配置。",
        "edit" => "不打开提示，编辑已保存配置。",
        "delete" => "不打开提示，删除已保存配置。",
        "cloud" => "使用固定的火山引擎云端地址。",
        "self-managed" => "使用本地或远程自托管地址。",
        "ov --help" => "查看所有命令。",
        "ov health" => "快速健康检查。",
        "ov status" => "查看详细后端状态。",
        "ov config show" => "确认新的当前配置。",
        "ov config switch" => "选择一个已保存配置并设为当前配置。",
        "ov config list" => "查看已保存配置。",
        "ov config list -o json" => "以 JSON 返回已保存配置，便于自动化。",
        "ov config add --help" => "创建新的已保存配置。",
        "ov config add cloud --help" => "查看云端配置专用参数。",
        "ov config add self-managed --help" => "查看自托管配置专用参数。",
        "ov config switch <name>" => "激活已保存的配置。",
        "ov language" => "打开语言选择器。",
        "ov language zh-CN" => "将显示语言切换为简体中文。",
        "ov lang en" => "使用短别名切换为英文显示。",
        "language" => "可选语言代码：en 或 zh-CN。",
        "name" => "已保存的配置名称。",
        "--name <name>" => "已保存配置名称。不提供则自动生成。",
        "--new-name <name>" => "重命名已保存配置。",
        "--url <url>" => "服务器地址。默认是 http://127.0.0.1:1933。",
        "--api-key-stdin" => "从 stdin 读取 API Key。",
        "--api-key-env <env>" => "从环境变量读取 API Key。",
        "--api-key-stdin / --api-key-env <env>" => "从 stdin 或环境变量读取普通 API Key。",
        "--api-key-stdin / --api-key-env <env> / --clear-api-key" => "替换或清除普通 API Key。",
        "--root-api-key-stdin / --root-api-key-env <env>" => {
            "从 stdin 或环境变量读取 root API Key。"
        }
        "--root-api-key-stdin / --root-api-key-env <env> / --clear-root-api-key" => {
            "替换或清除 root API Key。"
        }
        "--activate" => "同时写入当前 ovcli.conf。",
        "--force" => "替换已有的已保存配置。",
        "-o, --output <table|json>" => "选择表格输出或机器可读 JSON。",
        "-c, --compact <bool>" => "使用紧凑的表格或 JSON 输出。",
        "--account <account>" => "覆盖本次命令的 X-OpenViking-Account。",
        "--user <user>" => "覆盖本次命令的 X-OpenViking-User。",
        "--sudo" => "使用 root API Key 执行管理命令。",
        _ => description,
    }
}

fn start_here_line(command: &str, description: &str) -> String {
    format!(
        "  {} {}",
        theme::command(pad_to_display_width(command, 22)).bold(),
        theme::body(description)
    )
}

fn option_line(option: &str, description: &str) -> String {
    format!(
        "  {} {}",
        theme::command(pad_to_display_width(option, 26)),
        theme::body(description)
    )
}

fn section_lines(section: &HelpSection) -> Vec<String> {
    let mut lines = Vec::new();
    let language = Language::current();
    let title_text = localized_section_title(section.title, language);
    let title = format!("─ {title_text} ");
    let fill = BOX_WIDTH.saturating_sub(2 + display_width(&title));
    lines.push(format!(
        "{}{}{}{}",
        theme::border("╭"),
        theme::border(title).bold(),
        theme::border("─".repeat(fill)),
        theme::border("╮")
    ));

    for command in section.commands {
        lines.push(command_line(command));
    }

    lines.push(format!(
        "{}{}{}",
        theme::border("╰"),
        theme::border("─".repeat(BOX_WIDTH.saturating_sub(2))),
        theme::border("╯")
    ));
    lines
}

fn command_line(command: &HelpCommand) -> String {
    let language = Language::current();
    let command_description =
        localized_command_description(command.name, command.description, language);
    let description = match command.badge {
        Some(badge) => {
            let used = display_width(command_description)
                + display_width(localized_badge(badge, language));
            let spacer = DESCRIPTION_WIDTH.saturating_sub(used).max(1);
            format!(
                "{}{}{}",
                command_description,
                " ".repeat(spacer),
                theme::muted(localized_badge(badge, language))
            )
        }
        None => format!(
            "{}{}",
            command_description,
            " ".repeat(DESCRIPTION_WIDTH.saturating_sub(display_width(command_description)))
        ),
    };

    format!(
        "{} {} {} {}",
        theme::border("│"),
        theme::command(pad_to_display_width(command.name, COMMAND_WIDTH)).bold(),
        theme::body(description),
        theme::border("│")
    )
}

fn display_width(value: &str) -> usize {
    UnicodeWidthStr::width(value)
}

fn pad_to_display_width(value: &str, width: usize) -> String {
    format!(
        "{}{}",
        value,
        " ".repeat(width.saturating_sub(display_width(value)))
    )
}

fn localized_section_title(title: &str, language: Language) -> &str {
    if language == Language::En {
        return title;
    }
    match title {
        "Core Workflow" => "核心流程",
        "Filesystem" => "文件系统",
        "Search & Context" => "搜索与上下文",
        "Config & Status" => "配置与状态",
        "Import, Export & Sessions" => "导入、导出与会话",
        "Interactive & Admin" => "交互与管理",
        _ => title,
    }
}

fn localized_badge(badge: &str, language: Language) -> &str {
    match (language, badge) {
        (Language::ZhCn, "experimental") => "实验性",
        _ => badge,
    }
}

fn localized_command_description<'a>(
    name: &str,
    description: &'a str,
    language: Language,
) -> &'a str {
    if language == Language::En {
        return description;
    }
    match name {
        "add-resource" => "添加文件、文件夹、URL 或仓库",
        "add-skill" => "添加技能到 OpenViking",
        "find" => "语义检索相关上下文",
        "read" => "读取精确资源内容",
        "write" => "更新已有资源",
        "add-memory" => "直接添加记忆",
        "ls" => "列出目录内容",
        "tree" => "查看范围内的资源树",
        "mkdir" => "创建目录",
        "rm" => "删除资源",
        "mv" => "移动或重命名资源",
        "stat" => "查看资源元数据",
        "get" => "下载文件",
        "search" => "上下文感知检索",
        "grep" => "模式搜索",
        "glob" => "Glob 路径搜索",
        "overview" => "生成资源概览",
        "abstract" => "生成资源摘要",
        "relations" => "列出资源关系",
        "link" => "创建关系链接",
        "unlink" => "删除关系链接",
        "config" => "添加、编辑、删除或切换配置",
        "config show" => "显示当前配置",
        "config validate" => "验证当前配置",
        "config switch" => "切换当前配置",
        "config add" => "非交互式添加配置",
        "config list" => "列出已保存配置",
        "config delete" => "删除已保存配置",
        "health" => "快速检查服务器连接",
        "status" => "查看系统状态",
        "wait" => "等待异步任务完成",
        "task" => "查看异步任务",
        "observer" => "观察服务器组件",
        "session" => "管理会话",
        "import" => "导入 .ovpack",
        "export" => "导出为 .ovpack",
        "backup" => "创建仅恢复备份",
        "restore" => "恢复备份",
        "tui" => "打开交互式浏览器",
        "chat" => "与 VikingBot 对话",
        "admin" => "管理账户、用户和 API Key",
        "system" => "系统维护命令",
        "privacy" => "管理隐私策略",
        "reindex" => "重建语义和向量索引",
        "version" => "显示版本信息",
        "language" => "选择 CLI 显示语言（别名：lang）",
        _ => description,
    }
}

fn command_help_path(args: &[OsString]) -> Option<Vec<String>> {
    let tokens: Vec<String> = args
        .iter()
        .map(|arg| arg.to_string_lossy().to_string())
        .collect();
    if tokens.len() < 2 {
        return None;
    }

    let has_help_flag = tokens.iter().skip(1).any(|token| is_help_flag(token));
    if has_help_flag && let Some(path) = config_help_path(&tokens) {
        return Some(path);
    }

    let mut path = Vec::new();
    let mut i = 1;
    while i < tokens.len() {
        let token = &tokens[i];
        if is_help_flag(token) {
            break;
        }
        if token == "--sudo" || token == "--progress" || token == "--no-progress" || token == "-v" {
            i += 1;
            continue;
        }
        if consumes_value(token) {
            i += if token.contains('=') { 1 } else { 2 };
            continue;
        }
        if token.starts_with('-') {
            i += 1;
            continue;
        }

        path.push(canonical_command_token(token));
        if let Some(next) = tokens.get(i + 1) {
            if is_help_flag(next) {
                // Explicit help for this top-level command.
            } else if !next.starts_with('-') {
                if has_help_flag && path.len() == 1 && allows_curated_nested(&path[0]) {
                    path.push(canonical_command_token(next));
                }
                return if has_help_flag { Some(path) } else { None };
            } else {
                return None;
            }
        }
        break;
    }

    if path.is_empty() {
        return None;
    }

    if has_help_flag || (path.len() == 1 && is_bare_group_help_command(&path[0])) {
        Some(path)
    } else {
        None
    }
}

fn config_help_path(tokens: &[String]) -> Option<Vec<String>> {
    let mut i = 1;
    while i < tokens.len() {
        let token = &tokens[i];
        if is_help_flag(token) {
            return None;
        }
        if token == "--sudo" || token == "--progress" || token == "--no-progress" || token == "-v" {
            i += 1;
            continue;
        }
        if consumes_value(token) {
            i += if token.contains('=') { 1 } else { 2 };
            continue;
        }
        if token.starts_with('-') {
            i += 1;
            continue;
        }

        if canonical_command_token(token) != "config" {
            return None;
        }

        let mut path = vec!["config".to_string()];
        i += 1;
        while i < tokens.len() {
            let token = &tokens[i];
            if is_help_flag(token) {
                return Some(path);
            }
            if consumes_value(token)
                || matches!(
                    token.as_str(),
                    "--name"
                        | "--new-name"
                        | "--url"
                        | "--api-key-env"
                        | "--root-api-key-env"
                        | "--account"
                        | "--user"
                )
            {
                i += if token.contains('=') { 1 } else { 2 };
                continue;
            }
            if token.starts_with('-') {
                i += 1;
                continue;
            }

            match path.as_slice() {
                [base] if base == "config" => match token.as_str() {
                    "show" | "validate" | "switch" | "list" | "delete" | "edit" | "add" => {
                        path.push(token.clone());
                    }
                    _ => return Some(path),
                },
                [base, add] if base == "config" && add == "add" => match token.as_str() {
                    "cloud" | "self-managed" => path.push(token.clone()),
                    _ => return Some(path),
                },
                _ => return Some(path),
            }
            i += 1;
        }
        return Some(path);
    }

    None
}

fn command_spec(path: &[String]) -> Option<&'static CommandHelpSpec> {
    COMMAND_HELP_SPECS.iter().find(|spec| {
        spec.path.len() == path.len()
            && spec
                .path
                .iter()
                .zip(path.iter())
                .all(|(left, right)| left == right)
    })
}

fn canonical_command_token(token: &str) -> String {
    match token {
        "list" => "ls",
        "del" | "delete" => "rm",
        "rename" => "mv",
        "lang" => "language",
        other => other,
    }
    .to_string()
}

fn command_display(path: &[&str]) -> String {
    format!("ov {}", path.join(" "))
}

fn version() -> String {
    format!("v{}", env!("OPENVIKING_CLI_VERSION"))
}

fn allows_curated_nested(command: &str) -> bool {
    matches!(command, "config")
}

fn is_bare_group_help_command(command: &str) -> bool {
    matches!(
        command,
        "task" | "session" | "privacy" | "admin" | "system" | "observer"
    )
}

fn is_help_flag(token: &str) -> bool {
    matches!(token, "--help" | "-h" | "-help")
}

fn consumes_value(token: &str) -> bool {
    matches!(
        token,
        "-o" | "--output" | "-c" | "--compact" | "--account" | "--user"
    ) || token.starts_with("--output=")
        || token.starts_with("--compact=")
        || token.starts_with("--account=")
        || token.starts_with("--user=")
}

#[cfg(test)]
mod tests {
    use super::{
        COMMAND_HELP_SPECS, HELP_SECTIONS, command_help_path, display_width,
        render_command_help_request, render_top_level_help,
    };
    use super::{command_spec, is_top_level_help_request};
    use std::ffi::OsString;

    fn os_args(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::new();
        let mut chars = input.chars().peekable();

        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
            } else {
                output.push(ch);
            }
        }

        output
    }

    #[test]
    fn detects_only_top_level_help_requests() {
        assert!(is_top_level_help_request(&os_args(&["ov", "--help"])));
        assert!(is_top_level_help_request(&os_args(&["ov", "-h"])));
        assert!(is_top_level_help_request(&os_args(&["ov", "-help"])));
        assert!(!is_top_level_help_request(&os_args(&["ov", "help"])));

        assert!(!is_top_level_help_request(&os_args(&[
            "ov", "config", "--help"
        ])));
        assert!(!is_top_level_help_request(&os_args(&[
            "ov", "help", "config"
        ])));
        assert!(!is_top_level_help_request(&os_args(&["ov", "--version"])));
    }

    #[test]
    fn top_level_help_is_grouped_and_promotes_start_here() {
        let rendered = strip_ansi(&render_top_level_help());

        assert!(rendered.contains("OpenViking v"));
        assert!(rendered.contains("Context Database for AI Agents"));
        assert!(rendered.contains("Usage:"));
        assert!(rendered.contains("ov <command> [options]"));
        assert!(rendered.contains("Start here:"));
        assert!(rendered.contains("ov config"));
        assert!(rendered.contains("ov health"));
        assert!(rendered.contains("ov status"));
        assert!(rendered.contains("ov tui"));
    }

    #[test]
    fn top_level_help_contains_command_groups_without_flat_commands_heading() {
        let rendered = strip_ansi(&render_top_level_help());

        for section in [
            "Core Workflow",
            "Filesystem",
            "Search & Context",
            "Config & Status",
            "Import, Export & Sessions",
            "Interactive & Admin",
        ] {
            assert!(rendered.contains(section), "missing section: {section}");
        }

        assert!(rendered.contains("search"));
        assert!(rendered.contains("experimental"));
        assert!(rendered.contains("ov <command> --help"));
        assert!(!rendered.contains("Commands:\n  add-resource"));
    }

    #[test]
    fn boxed_sections_have_stable_width_after_ansi_is_removed() {
        let rendered = strip_ansi(&render_top_level_help());
        for line in rendered
            .lines()
            .filter(|line| line.starts_with(['╭', '│', '╰']))
        {
            assert_eq!(display_width(line), 74, "bad line width: {line}");
        }
    }

    #[test]
    fn every_top_level_command_in_help_map_has_command_help() {
        for command in HELP_SECTIONS
            .iter()
            .flat_map(|section| section.commands.iter().map(|command| command.name))
            .filter(|name| !name.contains(' '))
        {
            assert!(
                command_spec(&[command.to_string()]).is_some(),
                "missing command help for {command}"
            );
        }
    }

    #[test]
    fn top_level_help_exposes_all_curated_top_level_commands() {
        let top_level_names: Vec<&str> = HELP_SECTIONS
            .iter()
            .flat_map(|section| section.commands.iter().map(|command| command.name))
            .collect();

        for spec in COMMAND_HELP_SPECS
            .iter()
            .filter(|spec| spec.path.len() == 1)
        {
            let command = spec.path[0];
            assert!(
                top_level_names.contains(&command),
                "top-level help is missing curated command {command}"
            );
        }

        let rendered = strip_ansi(&render_top_level_help());
        for expected in ["add-skill", "observer", "version", "alias: lang"] {
            assert!(rendered.contains(expected), "missing {expected}");
        }
    }

    #[test]
    fn renders_curated_find_help() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "find", "--help"]))
                .expect("find help should render"),
        );

        assert!(rendered.contains("OpenViking v"));
        assert!(rendered.contains("ov find <query>"));
        assert!(rendered.contains("Examples"));
        assert!(rendered.contains("Common options"));
        assert!(rendered.contains("Next"));
        assert!(rendered.contains("ov read <uri>"));
    }

    #[test]
    fn find_and_search_help_explain_node_limit_semantics() {
        let find_help = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "find", "--help"]))
                .expect("find help should render"),
        );
        let search_help = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "search", "--help"]))
                .expect("search help should render"),
        );

        assert!(find_help.contains("Maximum final results returned."));
        assert!(search_help.contains("Maximum results per search pass."));
        assert!(search_help.contains("Search may merge multiple passes."));
    }

    #[test]
    fn renders_curated_command_help_for_single_dash_help_alias() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "find", "-help"]))
                .expect("find -help should render"),
        );

        assert!(rendered.contains("OpenViking v"));
        assert!(rendered.contains("ov find <query>"));
        assert!(rendered.contains("Usage:"));
    }

    #[test]
    fn renders_curated_status_help_with_verbose_option() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "status", "--help"]))
                .expect("status help should render"),
        );

        assert!(rendered.contains("ov status [--verbose]"));
        assert!(rendered.contains("ov status --verbose"));
        assert!(rendered.contains("Show full component tables"));
    }

    #[test]
    fn renders_curated_config_switch_help_from_both_help_forms() {
        for args in [
            os_args(&["ov", "config", "switch", "--help"]),
            os_args(&["ov", "config", "switch", "-h"]),
            os_args(&["ov", "config", "switch", "-help"]),
            os_args(&["ov", "config", "switch", "prod", "--help"]),
        ] {
            let rendered = strip_ansi(
                &render_command_help_request(&args).expect("config switch help should render"),
            );
            assert!(rendered.contains("ov config switch [name]"));
            assert!(rendered.contains("Switch the active CLI config"));
            assert!(!rendered.contains("profile"));
        }
    }

    #[test]
    fn renders_curated_config_agent_command_help() {
        let config = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "add", "--help"]))
                .expect("config add help should render"),
        );
        assert!(config.contains("ov config add <cloud|self-managed>"));
        assert!(config.contains("cloud"));
        assert!(config.contains("self-managed"));

        let cloud = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "add", "cloud", "--help"]))
                .expect("config add cloud help should render"),
        );
        assert!(cloud.contains("ov config add cloud"));
        assert!(cloud.contains("--api-key-stdin"));
        assert!(cloud.contains("--api-key-env <env>"));

        let self_managed = strip_ansi(
            &render_command_help_request(&os_args(&[
                "ov",
                "config",
                "add",
                "self-managed",
                "--help",
            ]))
            .expect("config add self-managed help should render"),
        );
        assert!(self_managed.contains("ov config add self-managed"));
        assert!(self_managed.contains("--root-api-key-stdin"));
        assert!(!self_managed.contains("--use-root-key-for-normal-commands"));

        let edit = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "edit", "prod", "--help"]))
                .expect("config edit help should render"),
        );
        assert!(edit.contains("ov config edit <name>"));
        assert!(edit.contains("--clear-api-key"));
        assert!(!edit.contains("--use-root-key-for-normal-commands"));

        let delete = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "delete", "prod", "--help"]))
                .expect("config delete help should render"),
        );
        assert!(delete.contains("ov config delete <name>"));
    }

    #[test]
    fn renders_curated_group_help_for_config_and_task() {
        let config = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "--help"]))
                .expect("config help should render"),
        );
        assert!(config.contains("Subcommands"));
        assert!(config.contains("show"));
        assert!(config.contains("validate"));
        assert!(config.contains("switch"));
        assert!(config.contains("add"));
        assert!(config.contains("list"));
        assert!(config.contains("delete"));

        let task = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "task", "--help"]))
                .expect("task help should render"),
        );
        assert!(task.contains("status <task-id>"));
        assert!(task.contains("list"));
    }

    #[test]
    fn command_help_detection_allows_global_flags_before_command() {
        let path = command_help_path(&os_args(&[
            "ov",
            "--account",
            "acme",
            "--user",
            "u1",
            "find",
            "--help",
        ]))
        .expect("path should be detected");

        assert_eq!(path, vec!["find"]);
    }

    #[test]
    fn bare_command_groups_render_curated_help() {
        for (command, expected) in [
            ("task", "Inspect and manage async processing tasks."),
            (
                "session",
                "Manage sessions, messages, archives, and committed session context.",
            ),
            (
                "privacy",
                "Manage privacy config categories, targets, versions, and activation.",
            ),
            ("admin", "Manage accounts, users, roles, and API keys."),
            (
                "system",
                "Run server utility, health, consistency, and crypto commands.",
            ),
            ("observer", "Inspect specific OpenViking server subsystems."),
        ] {
            let rendered = strip_ansi(
                &render_command_help_request(&os_args(&["ov", command]))
                    .unwrap_or_else(|| panic!("{command} should render curated help")),
            );

            assert!(rendered.contains(expected), "missing purpose for {command}");
            assert!(
                rendered.contains("Subcommands"),
                "missing subcommands for {command}"
            );
        }
    }

    #[test]
    fn bare_command_group_detection_allows_global_flags_before_command() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&[
                "ov",
                "--account",
                "acme",
                "--user",
                "u1",
                "task",
            ]))
            .expect("bare task help should render with global flags before command"),
        );

        assert!(rendered.contains("ov task <subcommand>"));
    }

    #[test]
    fn bare_config_is_not_intercepted_by_group_help() {
        assert!(render_command_help_request(&os_args(&["ov", "config"])).is_none());
    }

    #[test]
    fn unsupported_nested_prefixed_help_renders_parent_group_help() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "task", "list", "--help"]))
                .expect("task list help should render parent task help"),
        );

        assert!(rendered.contains("ov task <subcommand>"));
        assert!(rendered.contains("Inspect and manage async processing tasks."));
        assert!(rendered.contains("Subcommands"));
    }

    #[test]
    fn prefixed_help_with_positional_value_renders_curated_command_help() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "ls", "viking://projects", "--help"]))
                .expect("ls help with positional value should render curated ls help"),
        );

        assert!(rendered.contains("ov ls [uri]"));
        assert!(rendered.contains("List resources under a Viking URI."));
    }
}
