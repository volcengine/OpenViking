use std::ffi::OsString;

use clap::{Arg, ArgAction, Command, CommandFactory};
use colored::Colorize;
use unicode_width::UnicodeWidthStr;

use crate::{
    Cli,
    i18n::{Language, copy},
    theme,
};

const BOX_WIDTH: usize = 74;
const COMMAND_WIDTH: usize = 16;
const DESCRIPTION_WIDTH: usize = BOX_WIDTH - COMMAND_WIDTH - 5;
const COMMAND_HELP_LEFT_WIDTH: usize = 34;
const START_HERE_DESCRIPTION_WIDTH: usize = 56;

#[derive(Debug, Clone, Copy)]
struct HelpCommand {
    name: &'static str,
}

macro_rules! help_commands {
    ($($name:literal),+ $(,)?) => {
        &[$(HelpCommand { name: $name }),+]
    };
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

#[derive(Debug, Clone)]
struct RenderedHelpItem {
    label: String,
    description: String,
}

#[derive(Debug, Clone)]
struct RenderedHelpSection {
    title: String,
    items: Vec<RenderedHelpItem>,
}

#[derive(Debug, Clone, Copy)]
struct CommandHelpSpec {
    path: &'static [&'static str],
    purpose: &'static str,
    examples: &'static [HelpItem],
    next_steps: &'static [HelpItem],
}

const CORE_WORKFLOW: &[HelpCommand] = help_commands![
    "add-resource",
    "add-skill",
    "skills",
    "find",
    "read",
    "write",
    "add-memory",
];

const FILESYSTEM: &[HelpCommand] = help_commands!["ls", "tree", "mkdir", "rm", "mv", "stat", "get"];

const SEARCH_CONTEXT: &[HelpCommand] = help_commands![
    "find", "search", "grep", "glob", "abstract", "overview", "read"
];

const CONFIG_STATUS: &[HelpCommand] = help_commands![
    "config", "language", "health", "status", "observer", "wait", "task", "version",
];

const IMPORT_EXPORT_SESSIONS: &[HelpCommand] = help_commands![
    "import", "export", "backup", "restore", "session", "privacy"
];

const INTERACTIVE_ADMIN: &[HelpCommand] = help_commands![
    "tui",
    "chat",
    "admin",
    "system",
    "reindex",
    "relations",
    "link",
    "unlink"
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

const COMMAND_HELP_SPECS: &[CommandHelpSpec] = &[
    CommandHelpSpec {
        path: &["add-resource"],
        purpose: "Import a local file, folder, URL, or repository into OpenViking.",
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
        path: &["skills"],
        purpose: "Manage installed agent skills.",
        examples: &[
            HelpItem {
                label: "ov skills list",
                description: "List installed skills.",
            },
            HelpItem {
                label: "ov skills find \"code review\"",
                description: "Search installed skills semantically.",
            },
        ],
        next_steps: &[
            HelpItem {
                label: "ov skills <subcommand> --help",
                description: "Show exact arguments for a skill operation.",
            },
            HelpItem {
                label: "ov add-skill ./skills/my-skill",
                description: "Import a new skill.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["ls"],
        purpose: "List resources under a Viking URI.",
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
        examples: &[HelpItem {
            label: "ov tree viking://projects/acme -L 4",
            description: "Show a project tree up to depth 4.",
        }],
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
        examples: &[HelpItem {
            label: "ov mkdir viking://projects/acme --description \"ACME project context\"",
            description: "Create a project folder with a description.",
        }],
        next_steps: &[HelpItem {
            label: "ov add-resource ./docs --parent <uri>",
            description: "Import content into the new directory.",
        }],
    },
    CommandHelpSpec {
        path: &["rm"],
        purpose: "Remove a resource from OpenViking.",
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
        examples: &[HelpItem {
            label: "ov mv viking://notes/draft.md viking://notes/final.md",
            description: "Rename a file resource.",
        }],
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
        examples: &[HelpItem {
            label: "ov stat viking://projects/acme/spec.md",
            description: "Inspect resource metadata.",
        }],
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
        examples: &[HelpItem {
            label: "ov read viking://projects/acme/spec.md",
            description: "Print exact file content.",
        }],
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
        examples: &[HelpItem {
            label: "ov abstract viking://projects/acme",
            description: "Read the compact directory abstract.",
        }],
        next_steps: &[HelpItem {
            label: "ov overview <directory-uri>",
            description: "Read a richer Level 1 overview.",
        }],
    },
    CommandHelpSpec {
        path: &["overview"],
        purpose: "Read Level 1 overview content for a directory.",
        examples: &[HelpItem {
            label: "ov overview viking://projects/acme",
            description: "Read the directory overview.",
        }],
        next_steps: &[HelpItem {
            label: "ov read <file-uri>",
            description: "Open exact Level 2 content.",
        }],
    },
    CommandHelpSpec {
        path: &["write"],
        purpose: "Update text content in an existing resource.",
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
        examples: &[HelpItem {
            label: "ov get viking://assets/logo.png ./logo.png",
            description: "Download a binary or text file.",
        }],
        next_steps: &[HelpItem {
            label: "ov stat <uri>",
            description: "Inspect source metadata.",
        }],
    },
    CommandHelpSpec {
        path: &["find"],
        purpose: "Retrieve relevant OpenViking context semantically.",
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
        examples: &[HelpItem {
            label: "ov search \"what changed last time?\" --session-id abc123",
            description: "Search with session context.",
        }],
        next_steps: &[HelpItem {
            label: "ov session get-session-context <id>",
            description: "Inspect the session context directly.",
        }],
    },
    CommandHelpSpec {
        path: &["grep"],
        purpose: "Search resource content with a text pattern.",
        examples: &[HelpItem {
            label: "ov grep \"TODO\" -u viking://projects/acme -i",
            description: "Find case-insensitive matches in a subtree.",
        }],
        next_steps: &[HelpItem {
            label: "ov read <uri>",
            description: "Open a matching resource.",
        }],
    },
    CommandHelpSpec {
        path: &["glob"],
        purpose: "Find resources by glob pattern.",
        examples: &[HelpItem {
            label: "ov glob \"**/*.md\" -u viking://projects/acme",
            description: "Find Markdown files in a project.",
        }],
        next_steps: &[HelpItem {
            label: "ov read <uri>",
            description: "Read one matched file.",
        }],
    },
    CommandHelpSpec {
        path: &["session"],
        purpose: "Manage sessions, messages, archives, and committed session context.",
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
        next_steps: &[HelpItem {
            label: "ov session <subcommand> --help",
            description: "Show exact arguments for a session operation.",
        }],
    },
    CommandHelpSpec {
        path: &["add-memory"],
        purpose: "Add a memory directly from text or JSON messages.",
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
        next_steps: &[HelpItem {
            label: "ov find \"memory topic\"",
            description: "Verify the memory is retrievable.",
        }],
    },
    CommandHelpSpec {
        path: &["privacy"],
        purpose: "Manage privacy config categories, targets, versions, and activation.",
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
        next_steps: &[HelpItem {
            label: "ov privacy <subcommand> --help",
            description: "Show exact arguments for a privacy operation.",
        }],
    },
    CommandHelpSpec {
        path: &["relations"],
        purpose: "List relation links for one resource. Experimental.",
        examples: &[HelpItem {
            label: "ov relations viking://projects/acme/spec.md",
            description: "Inspect linked resources.",
        }],
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
        examples: &[HelpItem {
            label: "ov link viking://a.md viking://b.md --reason \"related design\"",
            description: "Link two resources with a reason.",
        }],
        next_steps: &[HelpItem {
            label: "ov relations <from-uri>",
            description: "Confirm the relation.",
        }],
    },
    CommandHelpSpec {
        path: &["unlink"],
        purpose: "Remove one relation link between resources. Experimental.",
        examples: &[HelpItem {
            label: "ov unlink viking://a.md viking://b.md",
            description: "Remove a relation.",
        }],
        next_steps: &[HelpItem {
            label: "ov relations <from-uri>",
            description: "Confirm the relation is gone.",
        }],
    },
    CommandHelpSpec {
        path: &["export"],
        purpose: "Export context from a URI as an .ovpack file.",
        examples: &[HelpItem {
            label: "ov export viking://projects/acme ./acme.ovpack",
            description: "Export a project subtree.",
        }],
        next_steps: &[HelpItem {
            label: "ov import ./file.ovpack <target-uri>",
            description: "Import the exported pack elsewhere.",
        }],
    },
    CommandHelpSpec {
        path: &["backup"],
        purpose: "Create a restore-only backup .ovpack for public OpenViking scopes.",
        examples: &[HelpItem {
            label: "ov backup ./openviking-backup.ovpack --include-vectors",
            description: "Create a backup with vectors when compatible.",
        }],
        next_steps: &[HelpItem {
            label: "ov restore ./openviking-backup.ovpack",
            description: "Restore this backup later.",
        }],
    },
    CommandHelpSpec {
        path: &["import"],
        purpose: "Import an .ovpack into a target URI.",
        examples: &[HelpItem {
            label: "ov import ./acme.ovpack viking://imports/acme --on-conflict skip",
            description: "Import while keeping existing resources.",
        }],
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
        examples: &[HelpItem {
            label: "ov restore ./openviking-backup.ovpack --on-conflict fail",
            description: "Restore only if there are no conflicts.",
        }],
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
        examples: &[HelpItem {
            label: "ov tui viking://projects/acme",
            description: "Browse a project subtree interactively.",
        }],
        next_steps: &[HelpItem {
            label: "ov tree <uri>",
            description: "Use a non-interactive tree view instead.",
        }],
    },
    CommandHelpSpec {
        path: &["chat"],
        purpose: "Chat with the vikingbot agent.",
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
        next_steps: &[HelpItem {
            label: "ov find \"topic\"",
            description: "Search context directly.",
        }],
    },
    CommandHelpSpec {
        path: &["wait"],
        purpose: "Wait for queued async processing to complete.",
        examples: &[HelpItem {
            label: "ov wait --timeout 120",
            description: "Wait up to two minutes.",
        }],
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
        next_steps: &[HelpItem {
            label: "ov wait",
            description: "Wait for queued work.",
        }],
    },
    CommandHelpSpec {
        path: &["task", "watch"],
        purpose: "Inspect and manage automatic resource refresh subscriptions.",
        examples: &[
            HelpItem {
                label: "ov task watch ls",
                description: "List watch subscriptions.",
            },
            HelpItem {
                label: "ov task watch update <task-or-uri> --interval 30",
                description: "Change a watch refresh interval.",
            },
        ],
        next_steps: &[
            HelpItem {
                label: "ov task watch <subcommand> --help",
                description: "Show exact arguments for a watch operation.",
            },
            HelpItem {
                label: "ov task list",
                description: "Inspect async processing tasks.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["status"],
        purpose: "Show OpenViking server readiness and component status.",
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
        next_steps: &[HelpItem {
            label: "ov status",
            description: "Return to the full status view.",
        }],
    },
    CommandHelpSpec {
        path: &["health"],
        purpose: "Run a quick server reachability check.",
        examples: &[HelpItem {
            label: "ov health",
            description: "Check whether the active server is reachable.",
        }],
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
        examples: &[
            HelpItem {
                label: "ov config",
                description: "Open the interactive config manager.",
            },
            HelpItem {
                label: "ov config add ov-service --api-key-stdin --activate",
                description: "Create and activate an OpenViking Service config from stdin.",
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
        examples: &[HelpItem {
            label: "ov config show",
            description: "Show the active server URL, config name, and safe fields.",
        }],
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
        examples: &[HelpItem {
            label: "ov config validate",
            description: "Check active URL, auth, and server reachability.",
        }],
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
        examples: &[
            HelpItem {
                label: "printf '%s' \"$OV_KEY\" | ov config add ov-service --api-key-stdin --activate",
                description: "Create and activate an OpenViking Service config.",
            },
            HelpItem {
                label: "ov config add custom --name local --url http://127.0.0.1:1933 --activate",
                description: "Create and activate a local custom config.",
            },
        ],
        next_steps: &[
            HelpItem {
                label: "ov config add ov-service --help",
                description: "See OpenViking Service flags.",
            },
            HelpItem {
                label: "ov config add custom --help",
                description: "See custom flags.",
            },
        ],
    },
    CommandHelpSpec {
        path: &["config", "add", "ov-service"],
        purpose: "Create an OpenViking Service config without prompts.",
        examples: &[
            HelpItem {
                label: "printf '%s' \"$OV_KEY\" | ov config add ov-service --name prod --api-key-stdin --activate",
                description: "Read the API key from stdin and make the config active.",
            },
            HelpItem {
                label: "ov config add ov-service --api-key-env OV_KEY -o json",
                description: "Read the API key from an environment variable and print JSON.",
            },
        ],
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
        path: &["config", "add", "custom"],
        purpose: "Create a custom config without prompts.",
        examples: &[
            HelpItem {
                label: "ov config add custom --name local --url http://127.0.0.1:1933 --activate",
                description: "Create a local no-key config.",
            },
            HelpItem {
                label: "ov config add custom --url https://ov.example.com --api-key-env OV_KEY --activate",
                description: "Create a hosted custom config with an API key.",
            },
        ],
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
        next_steps: &[HelpItem {
            label: "ov config",
            description: "Open the config manager.",
        }],
    },
    CommandHelpSpec {
        path: &["version"],
        purpose: "Print the OpenViking CLI version.",
        examples: &[HelpItem {
            label: "ov version",
            description: "Show the installed CLI version.",
        }],
        next_steps: &[HelpItem {
            label: "ov --help",
            description: "See all commands.",
        }],
    },
    CommandHelpSpec {
        path: &["admin"],
        purpose: "Manage accounts, users, roles, and API keys. Admin/root access required.",
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
        purpose: "Run server utility, health, consistency, backend sync, and crypto commands.",
        examples: &[
            HelpItem {
                label: "ov system health",
                description: "Run server health through the system namespace.",
            },
            HelpItem {
                label: "ov system consistency viking://projects/acme",
                description: "Check filesystem/vector consistency.",
            },
            HelpItem {
                label: "ov system backend sync-status viking://projects/acme",
                description: "Inspect multi-write backend sync lag.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov status",
            description: "Use the standard status view.",
        }],
    },
    CommandHelpSpec {
        path: &["system", "backend"],
        purpose: "Inspect and repair multi-write backend sync state.",
        examples: &[
            HelpItem {
                label: "ov system backend sync-status viking://resources",
                description: "Show pending and acknowledged backend sync state.",
            },
            HelpItem {
                label: "ov system backend sync-retry viking://resources",
                description: "Retry lagging backend sync targets.",
            },
        ],
        next_steps: &[HelpItem {
            label: "ov system backend sync-status <uri>",
            description: "Inspect the subtree again after retry.",
        }],
    },
    CommandHelpSpec {
        path: &["reindex"],
        purpose: "Reindex semantic/vector artifacts for a URI.",
        examples: &[HelpItem {
            label: "ov reindex viking://projects/acme --mode vectors_only --wait true",
            description: "Rebuild vector artifacts and wait.",
        }],
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
    let mut root = Cli::command();
    root.build();

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
    for command in ["config", "health", "status", "tui"] {
        if let Some(line) = top_level_start_here_line(&root, command, language) {
            lines.push(line);
        }
    }
    lines.push(String::new());

    for section in HELP_SECTIONS {
        lines.extend(section_lines(section, &root, language));
        lines.push(String::new());
    }

    lines.push(format!(
        "{}",
        theme::strong(copy(language, "Global options:", "全局选项："))
    ));
    for item in top_level_global_options(&root, language) {
        lines.push(option_line(&item.label, &item.description));
    }
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
    let clap_command = clap_command_for_path(spec.path)
        .unwrap_or_else(|| panic!("curated help path missing from clap: {command}"));

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
    let usage = usage_for_command(clap_command.clone(), spec.path);
    lines.push(format!("  {}", theme::strong(usage)));
    push_section(
        &mut lines,
        copy(language, "Examples", "示例"),
        spec.examples,
    );
    let argument_items = arguments_from_command(&clap_command);
    push_dynamic_section(
        &mut lines,
        copy(language, "Arguments", "参数"),
        &argument_items,
    );
    let subcommand_items = subcommands_from_command(&clap_command);
    push_dynamic_section(
        &mut lines,
        copy(language, "Subcommands", "子命令"),
        &subcommand_items,
    );
    for section in option_sections_from_command(&clap_command) {
        push_dynamic_section(
            &mut lines,
            localized_option_section_title(&section.title, language),
            &section.items,
        );
    }
    let global_items = global_options_for(spec);
    push_dynamic_section(
        &mut lines,
        copy(language, "Global options", "全局选项"),
        &global_items,
    );
    push_section(
        &mut lines,
        copy(language, "Next", "下一步"),
        spec.next_steps,
    );

    format!("{}\n", lines.join("\n"))
}

fn clap_command_for_path(path: &[&str]) -> Option<Command> {
    let mut root = Cli::command();
    root.build();

    let mut current = &root;
    for token in path {
        current = current.find_subcommand(token)?;
    }
    Some(current.clone())
}

fn usage_for_command(mut command: Command, path: &[&str]) -> String {
    let usage = command.render_usage().to_string();
    let usage = usage
        .trim()
        .strip_prefix("Usage:")
        .unwrap_or(usage.trim())
        .trim();

    if let Some(rest) = usage.strip_prefix("openviking") {
        return format!("ov{rest}");
    }

    let command_name = command.get_name();
    if let Some(rest) = usage.strip_prefix(command_name) {
        return format!("{}{}", command_display(path), rest);
    }

    usage.to_string()
}

fn arguments_from_command(command: &Command) -> Vec<RenderedHelpItem> {
    command
        .get_positionals()
        .filter(|arg| is_visible_help_arg(arg))
        .map(|arg| RenderedHelpItem {
            label: positional_label(arg),
            description: arg_help(arg),
        })
        .collect()
}

fn subcommands_from_command(command: &Command) -> Vec<RenderedHelpItem> {
    command
        .get_subcommands()
        .filter(|subcommand| !subcommand.is_hide_set() && subcommand.get_name() != "help")
        .map(|subcommand| RenderedHelpItem {
            label: subcommand_label(subcommand),
            description: command_about(subcommand),
        })
        .collect()
}

fn option_sections_from_command(command: &Command) -> Vec<RenderedHelpSection> {
    let mut sections = Vec::<RenderedHelpSection>::new();

    for arg in command
        .get_arguments()
        .filter(|arg| is_visible_help_arg(arg) && !arg.is_positional() && !arg.is_global_set())
    {
        let title = arg.get_help_heading().unwrap_or("Options").to_string();
        let item = RenderedHelpItem {
            label: option_label(arg),
            description: arg_help(arg),
        };

        if let Some(section) = sections.iter_mut().find(|section| section.title == title) {
            section.items.push(item);
        } else {
            sections.push(RenderedHelpSection {
                title,
                items: vec![item],
            });
        }
    }

    sections
}

fn global_options_for(spec: &CommandHelpSpec) -> Vec<RenderedHelpItem> {
    let include_identity = !matches!(
        spec.path,
        ["config", "add", "ov-service"] | ["config", "add", "custom"] | ["config", "edit"]
    );
    let include_sudo = matches!(
        spec.path,
        ["admin"] | ["system"] | ["system", "backend"] | ["reindex"]
    );

    let mut root = Cli::command();
    root.build();

    let mut ids = vec!["output", "compact"];
    if include_identity {
        ids.extend(["account", "user"]);
    }
    if include_sudo {
        ids.push("sudo");
    }

    rendered_global_options(&root, &ids, None)
}

fn is_visible_help_arg(arg: &Arg) -> bool {
    !arg.is_hide_set()
        && !matches!(
            arg.get_action(),
            ArgAction::Help | ArgAction::HelpShort | ArgAction::HelpLong | ArgAction::Version
        )
}

fn positional_label(arg: &Arg) -> String {
    let value = value_label(arg);
    let mut label = if arg.is_required_set() {
        format!("<{value}>")
    } else {
        format!("[{value}]")
    };
    if accepts_multiple_values(arg) {
        label.push_str("...");
    }
    label
}

fn subcommand_label(command: &Command) -> String {
    let mut names = vec![command.get_name().to_string()];
    names.extend(command.get_visible_aliases().map(ToString::to_string));
    let mut label = names.join(" / ");

    let mut arguments: Vec<String> = command
        .get_positionals()
        .filter(|arg| is_visible_help_arg(arg))
        .map(positional_label)
        .collect();

    if arguments.is_empty()
        && command
            .get_subcommands()
            .any(|subcommand| !subcommand.is_hide_set() && subcommand.get_name() != "help")
    {
        arguments.push("<COMMAND>".to_string());
    }

    if !arguments.is_empty() {
        label.push(' ');
        label.push_str(&arguments.join(" "));
    }

    label
}

fn option_label(arg: &Arg) -> String {
    let takes_value = takes_value(arg);
    let value = if takes_value {
        Some(value_label(arg))
    } else {
        None
    };

    let mut parts = Vec::new();
    if let Some(short) = arg.get_short() {
        parts.push(format!("-{short}"));
    }

    if let Some(long) = arg.get_long() {
        parts.push(format!("--{long}{}", option_value_suffix(value.as_deref())));
    }

    let mut label = if parts.is_empty() {
        arg.get_id().to_string()
    } else {
        parts.join(", ")
    };

    if let Some(aliases) = arg.get_all_aliases() {
        for alias in aliases {
            label.push_str(" / ");
            label.push_str(&format!(
                "--{alias}{}",
                option_value_suffix(value.as_deref())
            ));
        }
    }

    label
}

fn option_value_suffix(value: Option<&str>) -> String {
    value.map(|value| format!(" <{value}>")).unwrap_or_default()
}

fn value_label(arg: &Arg) -> String {
    if let Some(possible_values) = possible_value_label(arg) {
        return possible_values;
    }

    if let Some(value_names) = arg.get_value_names()
        && let Some(value_name) = value_names.first()
    {
        return normalize_value_name(value_name.as_str());
    }

    normalize_value_name(arg.get_id().as_str())
}

fn possible_value_label(arg: &Arg) -> Option<String> {
    let values: Vec<_> = arg
        .get_possible_values()
        .into_iter()
        .filter(|value| !value.is_hide_set())
        .map(|value| value.get_name().to_string())
        .collect();

    if values.is_empty() || values.len() > 6 {
        None
    } else {
        Some(values.join("|"))
    }
}

fn normalize_value_name(value: &str) -> String {
    value.to_ascii_lowercase().replace('_', "-")
}

fn takes_value(arg: &Arg) -> bool {
    arg.get_num_args().unwrap_or_default().takes_values()
        && !matches!(arg.get_action(), ArgAction::SetTrue | ArgAction::SetFalse)
}

fn accepts_multiple_values(arg: &Arg) -> bool {
    arg.get_num_args()
        .map(|range| range.max_values() > 1)
        .unwrap_or(false)
}

fn arg_help(arg: &Arg) -> String {
    arg.get_help()
        .or_else(|| arg.get_long_help())
        .map(ToString::to_string)
        .unwrap_or_default()
}

fn command_about(command: &Command) -> String {
    command
        .get_about()
        .or_else(|| command.get_long_about())
        .map(ToString::to_string)
        .unwrap_or_default()
}

fn localized_option_section_title(title: &str, language: Language) -> &str {
    match title {
        "Common options" => copy(language, "Common options", "常用选项"),
        "Advanced options" => copy(language, "Advanced options", "高级选项"),
        "Options" => copy(language, "Options", "选项"),
        _ => title,
    }
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

fn push_dynamic_section(lines: &mut Vec<String>, title: &str, items: &[RenderedHelpItem]) {
    if items.is_empty() {
        return;
    }

    lines.push(String::new());
    lines.push(format!("{}", theme::heading(title).bold()));
    for item in items {
        lines.push(help_item_line_parts(&item.label, &item.description));
    }
}

fn help_item_line(item: &HelpItem) -> String {
    let language = Language::current();
    let description = localized_help_item_description(item.label, item.description, language);
    help_item_line_parts(item.label, description)
}

fn help_item_line_parts(label: &str, description: &str) -> String {
    if display_width(label) > COMMAND_HELP_LEFT_WIDTH {
        return format!(
            "  {}\n      {}",
            theme::command(label),
            theme::body(description)
        );
    }

    format!(
        "  {} {}",
        theme::command(pad_to_display_width(label, COMMAND_HELP_LEFT_WIDTH)),
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
        ["config", "add", "ov-service"] => {
            "不打开交互式向导，创建 OpenViking 服务（火山引擎云）配置。"
        }
        ["config", "add", "custom"] => "不打开交互式向导，创建自定义配置。",
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
        "add" => "不打开提示，添加 OpenViking 服务或自定义配置。",
        "edit" => "不打开提示，编辑已保存配置。",
        "delete" => "不打开提示，删除已保存配置。",
        "ov-service" => "使用固定的 OpenViking 服务（火山引擎云）地址。",
        "custom" => "使用本地或远程自定义地址。",
        "ov --help" => "查看所有命令。",
        "ov health" => "快速健康检查。",
        "ov status" => "查看详细后端状态。",
        "ov config show" => "确认新的当前配置。",
        "ov config switch" => "选择一个已保存配置并设为当前配置。",
        "ov config list" => "查看已保存配置。",
        "ov config list -o json" => "以 JSON 返回已保存配置，便于自动化。",
        "ov config add --help" => "创建新的已保存配置。",
        "ov config add ov-service --help" => "查看 OpenViking 服务配置专用参数。",
        "ov config add custom --help" => "查看自定义配置专用参数。",
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
    let description = truncate_to_display_width(description, START_HERE_DESCRIPTION_WIDTH);
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

fn top_level_start_here_line(
    root: &Command,
    command_name: &str,
    language: Language,
) -> Option<String> {
    let command = root.find_subcommand(command_name)?;
    Some(start_here_line(
        &format!("ov {command_name}"),
        &top_level_command_description(command, language),
    ))
}

fn top_level_global_options(root: &Command, language: Language) -> Vec<RenderedHelpItem> {
    let mut items = rendered_global_options(
        root,
        &["output", "compact", "account", "user", "sudo"],
        Some(language),
    );

    items.push(RenderedHelpItem {
        label: "-h, --help".to_string(),
        description: copy(language, "Show help", "显示帮助").to_string(),
    });
    items.push(RenderedHelpItem {
        label: "-V, --version".to_string(),
        description: copy(language, "Show version", "显示版本").to_string(),
    });

    items
}

fn rendered_global_options(
    root: &Command,
    ids: &[&str],
    language: Option<Language>,
) -> Vec<RenderedHelpItem> {
    ids.iter()
        .filter_map(|id| {
            let id = *id;
            root.get_arguments()
                .find(|arg| arg.get_id().as_str() == id)
                .map(|arg| {
                    let description = arg_help(arg);
                    let description = language
                        .map(|language| {
                            localized_global_option_description(id, &description, language)
                                .to_string()
                        })
                        .unwrap_or(description);

                    RenderedHelpItem {
                        label: option_label(arg),
                        description,
                    }
                })
        })
        .collect()
}

fn section_lines(section: &HelpSection, root: &Command, language: Language) -> Vec<String> {
    let mut lines = Vec::new();
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
        if let Some(clap_command) = root.find_subcommand(command.name) {
            lines.push(command_line(command.name, clap_command, language));
        }
    }

    lines.push(format!(
        "{}{}{}",
        theme::border("╰"),
        theme::border("─".repeat(BOX_WIDTH.saturating_sub(2))),
        theme::border("╯")
    ));
    lines
}

fn command_line(command_name: &str, command: &Command, language: Language) -> String {
    let command_description = top_level_command_description(command, language);
    let badge = top_level_command_badge(command);
    let description = match badge.as_deref() {
        Some(badge) => {
            let badge = localized_badge(badge, language);
            let description_width = DESCRIPTION_WIDTH.saturating_sub(display_width(badge) + 1);
            let command_description =
                truncate_to_display_width(&command_description, description_width);
            let used = display_width(&command_description) + display_width(badge);
            let spacer = DESCRIPTION_WIDTH.saturating_sub(used).max(1);
            format!(
                "{}{}{}",
                command_description,
                " ".repeat(spacer),
                theme::muted(badge)
            )
        }
        None => {
            let command_description =
                truncate_to_display_width(&command_description, DESCRIPTION_WIDTH);
            format!(
                "{}{}",
                command_description,
                " ".repeat(DESCRIPTION_WIDTH.saturating_sub(display_width(&command_description)))
            )
        }
    };

    format!(
        "{} {} {} {}",
        theme::border("│"),
        theme::command(pad_to_display_width(command_name, COMMAND_WIDTH)).bold(),
        theme::body(description),
        theme::border("│")
    )
}

fn top_level_command_description(command: &Command, language: Language) -> String {
    let description = command_about_without_tags(command);
    let mut description =
        localized_command_description(command.get_name(), &description, language).to_string();
    let aliases: Vec<_> = command.get_all_aliases().collect();

    if !aliases.is_empty() {
        description.push_str(&format!(
            " ({}: {})",
            copy(language, "alias", "别名"),
            aliases.join(", ")
        ));
    }

    description
}

fn top_level_command_badge(command: &Command) -> Option<String> {
    let about = command_about(command);
    if command_about_tags(&about)
        .iter()
        .any(|tag| tag.eq_ignore_ascii_case("Experimental"))
    {
        Some("experimental".to_string())
    } else {
        None
    }
}

fn command_about_without_tags(command: &Command) -> String {
    let about = command_about(command);
    strip_command_tags(&about).to_string()
}

fn command_about_tags(about: &str) -> Vec<&str> {
    let mut tags = Vec::new();
    let mut rest = about.trim_start();

    while let Some(after_open) = rest.strip_prefix('[') {
        let Some((tag, after_close)) = after_open.split_once(']') else {
            break;
        };
        tags.push(tag);
        rest = after_close.trim_start();
    }

    tags
}

fn strip_command_tags(about: &str) -> &str {
    let mut rest = about.trim_start();

    while let Some(after_open) = rest.strip_prefix('[') {
        let Some((_, after_close)) = after_open.split_once(']') else {
            break;
        };
        rest = after_close.trim_start();
    }

    rest
}

fn localized_global_option_description<'a>(
    id: &str,
    description: &'a str,
    language: Language,
) -> &'a str {
    if language == Language::En {
        return description;
    }

    match id {
        "output" => "输出格式",
        "compact" => "紧凑输出",
        "account" => "覆盖账户",
        "user" => "覆盖用户",
        "sudo" => "admin、system 和 reindex 使用 root API Key",
        _ => description,
    }
}

fn display_width(value: &str) -> usize {
    UnicodeWidthStr::width(value)
}

fn truncate_to_display_width(value: &str, width: usize) -> String {
    if display_width(value) <= width {
        return value.to_string();
    }

    if width <= 3 {
        return ".".repeat(width);
    }

    let target = width - 3;
    let mut truncated = String::new();
    let mut used = 0;

    for ch in value.chars() {
        let ch_width = display_width(&ch.to_string());
        if used + ch_width > target {
            break;
        }
        truncated.push(ch);
        used += ch_width;
    }

    truncated.push_str("...");
    truncated
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
        "skills" => "管理已安装技能",
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
    if has_help_flag {
        if has_invalid_config_add_provider(&tokens) {
            return None;
        }
        if let Some(path) = config_help_path(&tokens) {
            return Some(path);
        }
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
                if has_help_flag && path.len() == 1 && is_bare_group_help_command(&path[0]) {
                    let nested_path = command_path_until_help_flag(&tokens, i + 1, path);
                    return if command_spec(&nested_path).is_some() {
                        Some(nested_path)
                    } else {
                        None
                    };
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

fn command_path_until_help_flag(
    tokens: &[String],
    mut i: usize,
    mut path: Vec<String>,
) -> Vec<String> {
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
        i += 1;
    }
    path
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
                    "ov-service" | "custom" => path.push(token.clone()),
                    _ => return None,
                },
                _ => return Some(path),
            }
            i += 1;
        }
        return Some(path);
    }

    None
}

fn has_invalid_config_add_provider(tokens: &[String]) -> bool {
    let mut i = 1;
    let mut saw_config = false;
    let mut saw_add = false;

    while i < tokens.len() {
        let token = &tokens[i];
        if is_help_flag(token) {
            return false;
        }
        if token == "--sudo" || token == "--progress" || token == "--no-progress" || token == "-v" {
            i += 1;
            continue;
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

        if !saw_config {
            saw_config = canonical_command_token(token) == "config";
            if !saw_config {
                return false;
            }
        } else if !saw_add {
            saw_add = token == "add";
            if !saw_add {
                return false;
            }
        } else {
            return !matches!(token.as_str(), "ov-service" | "custom");
        }

        i += 1;
    }

    false
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

fn is_bare_group_help_command(command: &str) -> bool {
    matches!(
        command,
        "task" | "skills" | "session" | "privacy" | "admin" | "system" | "observer"
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
    use crate::Cli;
    use clap::CommandFactory;
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
        assert!(rendered.contains("-c, --compact <true|false>"));
        assert!(rendered.contains("Use root API key for admin, system, and reindex"));
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
        assert!(rendered.contains("skills"));
        assert!(rendered.contains("experimental"));
        assert!(rendered.contains("ov <command> --help"));
        assert!(!rendered.contains("Commands:\n  add-resource"));
    }

    #[test]
    fn top_level_help_lists_only_top_level_commands() {
        for command in HELP_SECTIONS
            .iter()
            .flat_map(|section| section.commands.iter().map(|command| command.name))
        {
            assert!(
                !command.contains(' '),
                "top-level help must not list nested command {command}"
            );
        }

        let rendered = strip_ansi(&render_top_level_help());
        for nested in [
            "config show",
            "config validate",
            "config switch",
            "config add",
            "config list",
            "config delete",
        ] {
            assert!(
                !rendered.contains(nested),
                "top-level help leaked nested command {nested}"
            );
        }
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
    fn every_curated_top_level_command_in_help_map_has_command_help() {
        for command in HELP_SECTIONS
            .iter()
            .flat_map(|section| section.commands.iter().map(|command| command.name))
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
        for expected in ["add-skill", "skills", "observer", "version", "alias: lang"] {
            assert!(rendered.contains(expected), "missing {expected}");
        }
    }

    #[test]
    fn top_level_help_exposes_all_visible_clap_commands() {
        let rendered = strip_ansi(&render_top_level_help());
        let mut command = Cli::command();
        command.build();

        for subcommand in command
            .get_subcommands()
            .filter(|subcommand| !subcommand.is_hide_set() && subcommand.get_name() != "help")
        {
            let name = subcommand.get_name();
            assert!(rendered.contains(name), "missing clap command {name}");
        }
    }

    #[test]
    fn renders_curated_find_help() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "find", "--help"]))
                .expect("find help should render"),
        );

        assert!(rendered.contains("OpenViking v"));
        assert!(rendered.contains("ov find [OPTIONS] <query>"));
        assert!(rendered.contains("Examples"));
        assert!(rendered.contains("Common options"));
        assert!(rendered.contains("Next"));
        assert!(rendered.contains("ov read <uri>"));
    }

    #[test]
    fn curated_help_preserves_common_and_advanced_option_sections() {
        for args in [
            &["ov", "add-resource", "--help"][..],
            &["ov", "find", "--help"][..],
            &["ov", "config", "add", "custom", "--help"][..],
        ] {
            let rendered = strip_ansi(
                &render_command_help_request(&os_args(&args)).expect("help should render"),
            );
            assert!(
                rendered.contains("Common options"),
                "missing Common options in:\n{rendered}"
            );
            assert!(
                rendered.contains("Advanced options"),
                "missing Advanced options in:\n{rendered}"
            );
        }
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

        assert!(find_help.contains("Maximum final results returned"));
        assert!(search_help.contains("Maximum results per search pass."));
        assert!(search_help.contains("Search may merge multiple passes"));
    }

    #[test]
    fn renders_curated_command_help_for_single_dash_help_alias() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "find", "-help"]))
                .expect("find -help should render"),
        );

        assert!(rendered.contains("OpenViking v"));
        assert!(rendered.contains("ov find [OPTIONS] <query>"));
        assert!(rendered.contains("Usage:"));
    }

    #[test]
    fn renders_curated_status_help_with_verbose_option() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "status", "--help"]))
                .expect("status help should render"),
        );

        assert!(rendered.contains("ov status [OPTIONS]"));
        assert!(rendered.contains("ov status --verbose"));
        assert!(rendered.contains("Show full component tables"));
    }

    #[test]
    fn curated_help_lists_timeout_for_waiting_commands() {
        for args in [
            ["ov", "add-resource", "--help"],
            ["ov", "add-skill", "--help"],
            ["ov", "write", "--help"],
        ] {
            let rendered = strip_ansi(
                &render_command_help_request(&os_args(&args)).expect("help should render"),
            );
            assert!(
                rendered.contains("--timeout <seconds>"),
                "missing timeout option in:\n{rendered}"
            );
            assert!(
                rendered.contains("seconds"),
                "missing timeout description in:\n{rendered}"
            );
        }
    }

    #[test]
    fn curated_help_lists_upload_filters_peer_id_and_limit_aliases() {
        let add_resource = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "add-resource", "--help"]))
                .expect("add-resource help should render"),
        );
        for needle in [
            "--strict",
            "--ignore-dirs <dirs>",
            "--include <pattern>",
            "--exclude <pattern>",
            "--no-directly-upload-media",
            "--progress",
            "--no-progress",
            "-v, --verbose",
        ] {
            assert!(
                add_resource.contains(needle),
                "missing {needle} in:\n{add_resource}"
            );
        }

        for command in ["ls", "tree", "find", "search", "grep", "glob"] {
            let rendered = strip_ansi(
                &render_command_help_request(&os_args(&["ov", command, "--help"]))
                    .expect("help should render"),
            );
            assert!(
                rendered.contains("--limit <n>"),
                "missing --limit alias for {command} in:\n{rendered}"
            );
        }

        for command in ["find", "search"] {
            let rendered = strip_ansi(
                &render_command_help_request(&os_args(&["ov", command, "--help"]))
                    .expect("help should render"),
            );
            assert!(
                rendered.contains("--peer-id <id>"),
                "missing --peer-id for {command} in:\n{rendered}"
            );
            assert!(
                rendered.contains("--context-type <type>"),
                "missing --context-type for {command} in:\n{rendered}"
            );
        }
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
        assert!(config.contains("ov config add"));
        assert!(config.contains("ov-service"));
        assert!(config.contains("custom"));

        let cloud = strip_ansi(
            &render_command_help_request(&os_args(&[
                "ov",
                "config",
                "add",
                "ov-service",
                "--help",
            ]))
            .expect("config add ov-service help should render"),
        );
        assert!(cloud.contains("ov config add ov-service"));
        assert!(cloud.contains("--api-key-stdin"));
        assert!(cloud.contains("--api-key-env <env>"));

        let custom = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "add", "custom", "--help"]))
                .expect("config add custom help should render"),
        );
        assert!(custom.contains("ov config add custom"));
        assert!(custom.contains("--root-api-key-stdin"));
        assert!(!custom.contains("--use-root-key-for-normal-commands"));
        assert!(custom.contains("--account <account>"));
        assert!(!custom.contains("Override X-OpenViking-Account"));
        assert!(custom.contains("-o, --output <table|json>"));
        assert!(
            render_command_help_request(&os_args(&["ov", "config", "add", "cloud", "--help"]))
                .is_none()
        );
        assert!(
            render_command_help_request(&os_args(&[
                "ov",
                "config",
                "add",
                "self-managed",
                "--help",
            ]))
            .is_none()
        );

        let edit = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "edit", "prod", "--help"]))
                .expect("config edit help should render"),
        );
        assert!(edit.contains("ov config edit [OPTIONS] <name>"));
        assert!(edit.contains("--clear-api-key"));
        assert!(!edit.contains("--use-root-key-for-normal-commands"));

        let delete = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "config", "delete", "prod", "--help"]))
                .expect("config delete help should render"),
        );
        assert!(delete.contains("ov config delete [OPTIONS] <name>"));
        assert!(delete.contains("Delete even when the saved config file cannot be parsed"));
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
    fn command_group_help_shows_subcommand_positional_shapes() {
        let session = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "session", "--help"]))
                .expect("session help should render"),
        );

        assert!(session.contains("get <session-id>"));
        assert!(session.contains("get-session-context <session-id>"));
        assert!(session.contains("get-session-archive <session-id> <archive-id>"));
        assert!(session.contains("add-message <session-id>"));
        assert!(session.contains("add-messages <session-id> <messages-json>"));

        let watch = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "task", "watch", "--help"]))
                .expect("watch help should render"),
        );

        assert!(watch.contains("show <task-or-uri>"));
        assert!(watch.contains("update <task-or-uri>"));
        assert!(watch.contains("trigger <task-or-uri>"));
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
            ("skills", "Manage installed agent skills."),
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
                "Run server utility, health, consistency, backend sync, and crypto commands.",
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

        assert!(rendered.contains("ov task"));
    }

    #[test]
    fn bare_config_is_not_intercepted_by_group_help() {
        assert!(render_command_help_request(&os_args(&["ov", "config"])).is_none());
    }

    #[test]
    fn unsupported_nested_prefixed_help_falls_back_to_clap() {
        for args in [
            ["ov", "task", "list", "--help"],
            ["ov", "session", "add-message", "--help"],
            ["ov", "privacy", "upsert", "--help"],
            ["ov", "admin", "list-users", "--help"],
            ["ov", "system", "wait", "--help"],
        ] {
            assert!(
                render_command_help_request(&os_args(&args)).is_none(),
                "{args:?} should fall back to clap help"
            );
        }
    }

    #[test]
    fn supported_nested_curated_help_still_renders() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "system", "backend", "--help"]))
                .expect("system backend help should render curated nested help"),
        );

        assert!(rendered.contains("ov system backend"));
        assert!(rendered.contains("sync-status"));
        assert!(rendered.contains("sync-retry"));
        assert!(!rendered.contains("help                               Print this message"));
        assert!(rendered.contains("--sudo"));
    }

    #[test]
    fn prefixed_help_with_positional_value_renders_curated_command_help() {
        let rendered = strip_ansi(
            &render_command_help_request(&os_args(&["ov", "ls", "viking://projects", "--help"]))
                .expect("ls help with positional value should render curated ls help"),
        );

        assert!(rendered.contains("ov ls [OPTIONS] [uri]"));
        assert!(rendered.contains("List resources under a Viking URI."));
    }
}
