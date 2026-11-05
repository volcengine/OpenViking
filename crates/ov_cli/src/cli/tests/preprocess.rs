use crate::cli::preprocess::{
    find_command_index, first_command_token, plain_help_misuse, pre_parse_output_options,
    pre_parse_requires_cli_config_file, preprocess_privacy_args,
};
use crate::cli::tests::os_args;
use crate::output::OutputFormat;

#[test]
fn pre_parse_output_options_preserve_json_config_errors() {
    for args in [
        os_args(&["ov", "ls", "--output", "json", "--compact=false"]),
        os_args(&["ov", "ls", "-ojson"]),
        os_args(&["ov", "ls", "-o=json"]),
    ] {
        assert_eq!(pre_parse_output_options(&args).0, OutputFormat::Json);
    }
}

#[test]
fn pre_parse_config_gate_catches_incomplete_known_server_commands() {
    for args in [
        &["ov", "find"][..],
        &["ov", "task", "status"],
        &["ov", "task", "cancel"],
        &["ov", "task", "watch", "show"],
        &["ov", "config", "validate"],
        &["ov", "config", "show"],
        &["ov", "system", "consistency"],
        &["ov", "admin", "list-accounts"],
    ] {
        assert!(
            pre_parse_requires_cli_config_file(&os_args(args)),
            "{args:?} should require ovcli.conf before Clap positional validation"
        );
    }
}

#[test]
fn pre_parse_config_gate_covers_known_top_level_server_commands() {
    for command in [
        "add-resource",
        "add-skill",
        "ls",
        "tree",
        "mkdir",
        "rm",
        "mv",
        "stat",
        "read",
        "abstract",
        "overview",
        "write",
        "get",
        "find",
        "search",
        "grep",
        "glob",
        "add-memory",
        "export",
        "backup",
        "import",
        "restore",
        "tui",
        "chat",
        "wait",
        "status",
        "health",
        "reindex",
    ] {
        assert!(
            pre_parse_requires_cli_config_file(&os_args(&["ov", command])),
            "{command} should require ovcli.conf before Clap validation"
        );
    }
}

#[test]
fn pre_parse_config_gate_covers_known_group_server_commands() {
    let cases: &[&[&str]] = &[
        &["ov", "config", "show"],
        &["ov", "config", "validate"],
        &["ov", "task", "status"],
        &["ov", "task", "cancel"],
        &["ov", "task", "list"],
        &["ov", "task", "watch"],
        &["ov", "task", "watch", "ls"],
        &["ov", "task", "watch", "show"],
        &["ov", "task", "watch", "rm"],
        &["ov", "task", "watch", "pause"],
        &["ov", "task", "watch", "resume"],
        &["ov", "task", "watch", "update"],
        &["ov", "task", "watch", "trigger"],
        &["ov", "admin", "create-account"],
        &["ov", "admin", "list-accounts"],
        &["ov", "admin", "delete-account"],
        &["ov", "admin", "register-user"],
        &["ov", "admin", "list-users"],
        &["ov", "admin", "create-group"],
        &["ov", "admin", "list-groups"],
        &["ov", "admin", "list-group-members"],
        &["ov", "admin", "add-group-member"],
        &["ov", "admin", "remove-group-member"],
        &["ov", "admin", "delete-group"],
        &["ov", "admin", "remove-user"],
        &["ov", "admin", "set-role"],
        &["ov", "admin", "regenerate-key"],
        &["ov", "admin", "set-account-settings"],
        &["ov", "system", "wait"],
        &["ov", "system", "status"],
        &["ov", "system", "health"],
        &["ov", "system", "consistency"],
        &["ov", "system", "crypto"],
        &["ov", "system", "crypto", "init-key"],
        &["ov", "session", "new"],
        &["ov", "session", "list"],
        &["ov", "session", "get"],
        &["ov", "session", "get-session-context"],
        &["ov", "session", "get-session-archive"],
        &["ov", "session", "delete"],
        &["ov", "session", "add-message"],
        &["ov", "session", "add-messages"],
        &["ov", "session", "commit"],
        &["ov", "privacy", "categories"],
        &["ov", "privacy", "tree"],
        &["ov", "privacy", "list"],
        &["ov", "privacy", "get"],
        &["ov", "privacy", "reference"],
        &["ov", "privacy", "resolve"],
        &["ov", "privacy", "upsert"],
        &["ov", "privacy", "versions"],
        &["ov", "privacy", "version"],
        &["ov", "privacy", "activate"],
        &["ov", "privacy", "delete"],
        &["ov", "privacy", "scan"],
        &["ov", "observer", "queue"],
        &["ov", "observer", "vikingdb"],
        &["ov", "observer", "models"],
        &["ov", "observer", "retrieval"],
        &["ov", "observer", "filesystem"],
        &["ov", "observer", "system"],
        &["ov", "skills", "add"],
        &["ov", "skills", "list"],
        &["ov", "skills", "ls"],
        &["ov", "skills", "find"],
        &["ov", "skills", "show"],
        &["ov", "skills", "update"],
        &["ov", "skills", "remove"],
        &["ov", "skills", "rm"],
        &["ov", "skills", "delete"],
    ];

    for args in cases {
        assert!(
            pre_parse_requires_cli_config_file(&os_args(args)),
            "{args:?} should require ovcli.conf before Clap validation"
        );
    }

    assert!(
        pre_parse_requires_cli_config_file(&preprocess_privacy_args(os_args(&[
            "ov", "privacy", "skill", "demo",
        ]))),
        "privacy shortcut should require ovcli.conf after preprocessing"
    );
}

#[test]
fn pre_parse_config_gate_respects_aliases_and_valid_help_values() {
    for args in [
        &["ov", "list"][..],
        &["ov", "delete"],
        &["ov", "rename"],
        &["ov", "lang", "en"],
    ] {
        let expected = args[1] != "lang";
        assert_eq!(
            pre_parse_requires_cli_config_file(&os_args(args)),
            expected,
            "{args:?} pre-parse gate mismatch"
        );
    }
}

#[test]
fn pre_parse_config_gate_allows_non_server_and_unknown_commands() {
    for args in [
        &["ov", "con"][..],
        &["ov", "task", "nope"],
        &["ov", "config"],
        &["ov", "config", "switch"],
        &["ov", "config", "switch", "prod"],
        &["ov", "config", "list"],
        &["ov", "config", "delete", "prod"],
        &["ov", "config", "add", "ov-service", "--api-key-stdin"],
        &["ov", "config", "edit", "prod", "--activate"],
        &["ov", "config", "setup-cli"],
        &["ov", "skills", "validate", "./skills/foo"],
        &["ov", "skills", "validate", "./skills/foo", "--strict"],
        &["ov", "version"],
        &["ov", "language", "en"],
        &["ov", "lang", "en"],
    ] {
        assert!(
            !pre_parse_requires_cli_config_file(&os_args(args)),
            "{args:?} should not be gated before Clap parsing"
        );
    }
}

#[test]
fn first_command_token_skips_global_flags() {
    assert_eq!(
        first_command_token(&os_args(&[
            "ov",
            "--output",
            "json",
            "--account=acme",
            "--actor-peer-id",
            "peer-a",
            "--sudo",
            "admin",
        ]))
        .as_deref(),
        Some("admin")
    );
    assert_eq!(
        first_command_token(&os_args(&["ov", "--help"])).as_deref(),
        None
    );
}

#[test]
fn find_command_index_skips_root_value_options() {
    assert_eq!(
        find_command_index(&os_args(&[
            "ov",
            "--output",
            "json",
            "--account=acme",
            "--actor-peer-id",
            "peer-a",
            "privacy",
            "sample-policy",
        ])),
        Some(6)
    );
    assert_eq!(
        find_command_index(&os_args(&["ov", "--compact", "privacy", "sample-policy"])),
        Some(2)
    );
    assert_eq!(
        find_command_index(&os_args(&["ov", "-c", "false", "privacy", "sample-policy"])),
        Some(3)
    );
}

#[test]
fn plain_help_is_rejected_for_top_level_group_and_structured_leaf_commands() {
    for (args, expected_help) in [
        (vec!["ov", "help"], "ov --help"),
        (vec!["ov", "config", "help"], "ov config --help"),
        (vec!["ov", "task", "help"], "ov task --help"),
        (vec!["ov", "admin", "help"], "ov admin --help"),
        (vec!["ov", "ls", "help"], "ov ls --help"),
        (vec!["ov", "tree", "help"], "ov tree --help"),
        (vec!["ov", "read", "help"], "ov read --help"),
        (
            vec!["ov", "task", "status", "help"],
            "ov task status --help",
        ),
        (
            vec!["ov", "system", "consistency", "help"],
            "ov system consistency --help",
        ),
    ] {
        let misuse = plain_help_misuse(&os_args(&args))
            .unwrap_or_else(|| panic!("{args:?} should be plain-help misuse"));
        assert_eq!(misuse.help_command, expected_help);
    }
}

#[test]
fn plain_help_is_allowed_as_free_form_user_input() {
    for args in [
        vec!["ov", "find", "help"],
        vec!["ov", "search", "help"],
        vec!["ov", "grep", "help"],
        vec!["ov", "glob", "help"],
        vec!["ov", "add-memory", "help"],
        vec!["ov", "add-resource", "help"],
        vec!["ov", "add-skill", "help"],
    ] {
        assert!(
            plain_help_misuse(&os_args(&args)).is_none(),
            "{args:?} should remain normal user input"
        );
    }
}

#[test]
fn plain_help_ignores_option_values() {
    assert!(
        plain_help_misuse(&os_args(&[
            "ov",
            "read",
            "--account",
            "help",
            "viking://resource",
        ]))
        .is_none()
    );
    assert!(plain_help_misuse(&os_args(&["ov", "grep", "--uri", "help", "TODO",])).is_none());

    for args in [
        &["ov", "mkdir", "--description", "help", "viking://notes"][..],
        &[
            "ov",
            "write",
            "viking://notes/todo.md",
            "--from-file",
            "help",
        ],
        &[
            "ov",
            "session",
            "add-message",
            "abc",
            "--peer-id",
            "help",
            "--role",
            "user",
            "--content",
            "hi",
        ],
        &[
            "ov",
            "privacy",
            "upsert",
            "cat",
            "target",
            "--values-json",
            "help",
        ],
        &["ov", "admin", "create-account", "acct", "--admin", "help"],
        &["ov", "config", "add", "custom", "--url", "help"],
        &["ov", "task", "list", "--status", "help"],
        &[
            "ov",
            "system",
            "crypto",
            "init-key",
            "--output-file",
            "help",
        ],
    ] {
        assert!(
            plain_help_misuse(&os_args(args)).is_none(),
            "{args:?} should treat help as an option value"
        );
    }
}
