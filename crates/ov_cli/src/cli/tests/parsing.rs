use crate::cli::{Cli, Commands, CompileCommands, ObserverCommands, SnapshotCmd};
use crate::handlers;
use clap::{CommandFactory, Parser};

#[test]
fn cli_parses_global_identity_override_flags() {
    let cli = Cli::try_parse_from([
        "ov",
        "--account",
        "acme",
        "--user",
        "alice",
        "--actor-peer-id",
        "peer-a",
        "ls",
    ])
    .expect("cli should parse");

    assert_eq!(cli.account.as_deref(), Some("acme"));
    assert_eq!(cli.user.as_deref(), Some("alice"));
    assert_eq!(cli.actor_peer_id.as_deref(), Some("peer-a"));
}

#[test]
fn cli_parses_copy_recursive_flag() {
    let file = Cli::try_parse_from([
        "ov",
        "cp",
        "viking://resources/a.md",
        "viking://resources/b.md",
    ])
    .expect("file copy should parse");
    match file.command {
        Commands::Cp {
            from_uri,
            to_uri,
            recursive,
        } => {
            assert_eq!(from_uri, "viking://resources/a.md");
            assert_eq!(to_uri, "viking://resources/b.md");
            assert!(!recursive);
        }
        _ => panic!("expected cp command"),
    }

    let directory = Cli::try_parse_from([
        "ov",
        "cp",
        "-r",
        "viking://resources/src",
        "viking://resources/dst",
    ])
    .expect("recursive directory copy should parse");
    match directory.command {
        Commands::Cp { recursive, .. } => assert!(recursive),
        _ => panic!("expected cp command"),
    }
}

#[test]
fn cli_parses_snapshot_diff_refs() {
    let cli = Cli::try_parse_from([
        "ov",
        "snapshot",
        "diff",
        "viking://resources/a.md",
        "--from",
        "old",
        "--to",
        "new",
    ])
    .expect("snapshot diff should parse");

    match cli.command {
        Commands::Snapshot {
            cmd:
                SnapshotCmd::Diff {
                    path,
                    from_ref,
                    to_ref,
                },
        } => {
            assert_eq!(path, "viking://resources/a.md");
            assert_eq!(from_ref.as_deref(), Some("old"));
            assert_eq!(to_ref, "new");
        }
        _ => panic!("expected snapshot diff"),
    }
}

#[test]
fn cli_parses_find_context_type() {
    let cli = Cli::try_parse_from(["ov", "find", "invoice", "--context-type", "memory,resource"])
        .expect("find context type should parse");

    match cli.command {
        Commands::Find { context_type, .. } => {
            assert_eq!(
                context_type,
                Some(vec!["memory".to_string(), "resource".to_string()])
            );
        }
        _ => panic!("expected find command"),
    }
}

#[test]
fn cli_parses_find_image_without_query() {
    let cli =
        Cli::try_parse_from(["ov", "find", "--image", "cat.png"]).expect("find image should parse");

    match cli.command {
        Commands::Find { query, image, .. } => {
            assert_eq!(query, None);
            assert_eq!(image.as_deref(), Some("cat.png"));
        }
        _ => panic!("expected find command"),
    }
}

#[test]
fn cli_parses_search_context_mode_and_type() {
    let cli = Cli::try_parse_from([
        "ov",
        "search",
        "invoice",
        "--mode",
        "context",
        "--context-type",
        "skill",
    ])
    .expect("search context mode and type should parse");

    match cli.command {
        Commands::Search {
            mode, context_type, ..
        } => {
            assert_eq!(mode, "context");
            assert_eq!(context_type, Some(vec!["skill".to_string()]));
        }
        _ => panic!("expected search command"),
    }
}

#[test]
fn cli_parses_search_image_with_query() {
    let cli = Cli::try_parse_from(["ov", "search", "poster", "--image", "viking://x.png"])
        .expect("search image should parse");

    match cli.command {
        Commands::Search {
            query, image, mode, ..
        } => {
            assert_eq!(query.as_deref(), Some("poster"));
            assert_eq!(image.as_deref(), Some("viking://x.png"));
            assert_eq!(mode, "list");
        }
        _ => panic!("expected search command"),
    }
}

#[test]
fn cli_parses_grep_context_lines() {
    let cli = Cli::try_parse_from([
        "ov",
        "grep",
        "--uri",
        "viking://resources",
        "-a",
        "2",
        "-b",
        "3",
        "needle",
    ])
    .expect("grep context should parse");

    match cli.command {
        Commands::Grep {
            after_context,
            before_context,
            ..
        } => {
            assert_eq!(after_context, 2);
            assert_eq!(before_context, 3);
        }
        _ => panic!("expected grep command"),
    }
}

#[test]
fn cli_find_and_search_reject_removed_peer_id_flag() {
    assert!(Cli::try_parse_from(["ov", "find", "invoice", "--peer-id", "peer-a"]).is_err());
    assert!(Cli::try_parse_from(["ov", "search", "invoice", "--peer-id", "peer-a"]).is_err());
}

#[test]
fn cli_chat_sender_uses_long_flag_and_session_keeps_short_s() {
    Cli::command().debug_assert();

    let cli = Cli::try_parse_from([
        "ov",
        "chat",
        "-s",
        "session-1",
        "--sender",
        "agent-1",
        "--message",
        "hello",
    ])
    .expect("chat flags should parse without short alias conflicts");

    match cli.command {
        Commands::Chat {
            session, sender, ..
        } => {
            assert_eq!(session.as_deref(), Some("session-1"));
            assert_eq!(sender, "agent-1");
        }
        _ => panic!("expected chat command"),
    }
}

#[test]
fn cli_compile_requires_skill_and_expands_source_flags() {
    let cli = Cli::try_parse_from([
        "ov",
        "compile",
        "--from",
        "viking://resources/a,viking://resources/b",
        "--from",
        "viking://resources/c",
        "--to",
        "viking://resources/wiki",
        "--skill",
        "viking://agent/skills/wiki",
        "--instruction",
        "Keep supporting evidence.",
        "--args",
        r#"{"model_name":"endpoint-1"}"#,
    ])
    .expect("compile flags should parse");
    match cli.command {
        Commands::Compile {
            from_uris,
            to,
            skill,
            instruction,
            args,
            ..
        } => {
            assert_eq!(from_uris.len(), 3);
            assert_eq!(to.as_deref(), Some("viking://resources/wiki"));
            assert_eq!(skill.as_deref(), Some("viking://agent/skills/wiki"));
            assert_eq!(instruction.as_deref(), Some("Keep supporting evidence."));
            assert_eq!(args.as_deref(), Some(r#"{"model_name":"endpoint-1"}"#));
        }
        _ => panic!("expected compile command"),
    }

    assert!(
        Cli::try_parse_from([
            "ov",
            "compile",
            "--from",
            "viking://resources/a",
            "--to",
            "viking://resources/wiki",
        ])
        .is_err()
    );
}

#[test]
fn cli_read_line_ranges() {
    for (args, expected_offset, expected_limit) in [
        (vec![], 0, -1),
        (vec!["--offset", "10", "--limit", "5"], 10, 5),
        (vec!["--offset", "10", "--limit", "-1"], 10, -1),
        (vec!["--limit", "0"], 0, 0),
        (vec!["--offset", "10"], 10, -1),
    ] {
        let mut argv = vec!["ov", "read", "viking://resources/example.md"];
        argv.extend(args);
        let cli = Cli::try_parse_from(argv).expect("valid read range should parse");
        match cli.command {
            Commands::Read { offset, limit, .. } => {
                assert_eq!((offset, limit), (expected_offset, expected_limit));
            }
            _ => panic!("expected read command"),
        }
    }
    for args in [["--offset", "-1"], ["--limit", "-2"], ["--limit", "abc"]] {
        assert!(
            Cli::try_parse_from(
                ["ov", "read", "viking://resources/example.md"]
                    .into_iter()
                    .chain(args)
            )
            .is_err()
        );
    }
}

#[test]
fn cli_compile_memory_modes_accept_optional_target_and_reject_conflicts() {
    for mode in [vec!["memory"], vec!["--skill", "memory"]] {
        for target in [None, Some("viking://~/memories/entities")] {
            let cli = Cli::try_parse_from(
                ["ov", "compile"]
                    .into_iter()
                    .chain(mode.clone())
                    .chain(target.into_iter().flat_map(|uri| ["--to", uri])),
            )
            .unwrap();
            match cli.command {
                Commands::Compile {
                    command: Some(CompileCommands::Memory { to, .. }),
                    skill,
                    ..
                } => {
                    assert!(skill.is_none());
                    assert_eq!(to, target.unwrap_or("viking://~/memories"));
                }
                Commands::Compile {
                    from_uris,
                    to,
                    skill,
                    ..
                } => {
                    assert!(from_uris.is_empty());
                    assert_eq!(to.as_deref(), target);
                    assert_eq!(skill.as_deref(), Some("memory"));
                }
                _ => panic!("expected compile command"),
            }
        }
    }
    for options in [
        vec![],
        vec!["unknown"],
        vec!["memory", "--skill", "memory"],
        vec!["--skill", "memory", "memory"],
        vec!["memory", "--from", "viking://resources/a"],
        vec!["--from", "viking://resources/a", "memory"],
    ] {
        assert!(Cli::try_parse_from(["ov", "compile"].into_iter().chain(options)).is_err());
    }
}

#[test]
fn cli_add_resource_rejects_top_level_parse_mode() {
    assert!(
        Cli::try_parse_from([
            "ov",
            "add-resource",
            "./README.md",
            "--parse-mode",
            "no_split",
        ])
        .is_err()
    );
}

#[test]
fn cli_add_resource_add_type_requires_exact_to() {
    assert!(
        Cli::try_parse_from(["ov", "add-resource", "space:home", "--add-type", "feishu"]).is_err()
    );
    assert!(
        Cli::try_parse_from([
            "ov",
            "add-resource",
            "space:home",
            "--add-type",
            "feishu",
            "--to",
            "viking://resources/feishu",
            "--parent",
            "viking://resources/imports",
        ])
        .is_err()
    );
    assert!(
        Cli::try_parse_from([
            "ov",
            "add-resource",
            "space:home",
            "--add-type",
            "feishu",
            "--to",
            "viking://resources/feishu",
            "--parent-auto-create",
            "viking://resources/imports",
        ])
        .is_err()
    );

    let cli = Cli::try_parse_from([
        "ov",
        "add-resource",
        "space:home",
        "--add-type",
        "feishu",
        "--to",
        "viking://resources/feishu",
    ])
    .expect("declared add type with an exact target should parse");

    match cli.command {
        Commands::AddResource { add_type, to, .. } => {
            assert_eq!(add_type.as_deref(), Some("feishu"));
            assert_eq!(to.as_deref(), Some("viking://resources/feishu"));
        }
        _ => panic!("expected add-resource command"),
    }
}

#[test]
fn cli_parses_add_resource_tags() {
    let cli = Cli::try_parse_from([
        "ov",
        "add-resource",
        "./README.md",
        "--tags",
        "team=search,env=test",
        "--tag-mode",
        "append",
    ])
    .expect("add-resource tag flags should parse");

    match cli.command {
        Commands::AddResource { tags, tag_mode, .. } => {
            assert_eq!(tags, vec!["team=search", "env=test"]);
            assert_eq!(tag_mode, "append");
        }
        _ => panic!("expected add-resource command"),
    }
}

#[test]
fn cli_parses_clear_tag_mode_without_tags() {
    let add = Cli::try_parse_from(["ov", "add-resource", "./README.md", "--tag-mode", "clear"])
        .expect("add-resource clear mode should parse");
    match add.command {
        Commands::AddResource { tags, tag_mode, .. } => {
            assert!(tags.is_empty());
            assert_eq!(tag_mode, "clear");
        }
        _ => panic!("expected add-resource command"),
    }

    let write = Cli::try_parse_from([
        "ov",
        "write",
        "viking://resources/demo.md",
        "--content",
        "content",
        "--tag-mode",
        "clear",
        "--edit-source",
        "compile",
    ])
    .expect("write clear mode with Compile source should parse");
    assert!(matches!(
        write.command,
        Commands::Write { tag_mode, tags, edit_source, .. }
            if tag_mode == "clear" && tags.is_empty() && edit_source == "compile"
    ));

    let reindex = Cli::try_parse_from([
        "ov",
        "reindex",
        "viking://resources/demo",
        "--tag-mode",
        "clear",
    ])
    .expect("reindex clear mode should parse");
    assert!(matches!(
        reindex.command,
        Commands::Reindex { tag_mode, tags, .. } if tag_mode == "clear" && tags.is_empty()
    ));
}

#[test]
fn cli_parses_sudo_before_admin_command() {
    let cli = Cli::try_parse_from(["ov", "--sudo", "admin", "list-accounts"])
        .expect("pre-command sudo should parse");

    assert!(cli.sudo);
}

#[test]
fn cli_parses_sudo_after_admin_command() {
    let cli = Cli::try_parse_from(["ov", "admin", "list-accounts", "--sudo"])
        .expect("post-command sudo should parse");

    assert!(cli.sudo);
}

#[test]
fn sudo_supports_task_status_and_list_only() {
    let status = Cli::try_parse_from(["ov", "--sudo", "task", "status", "task-123"])
        .expect("sudo task status should parse");
    assert!(status.sudo);
    assert!(status.command.supports_sudo());

    let list = Cli::try_parse_from([
        "ov",
        "--sudo",
        "task",
        "list",
        "--task-type",
        "admin_reindex",
    ])
    .expect("sudo task list should parse");
    assert!(list.sudo);
    assert!(list.command.supports_sudo());

    let watch = Cli::try_parse_from(["ov", "--sudo", "task", "watch", "ls"])
        .expect("sudo task watch should still parse before runtime validation");
    assert!(watch.sudo);
    assert!(!watch.command.supports_sudo());
}

#[test]
fn all_node_limit_options_accept_zero_and_reject_negative_values() {
    let command_prefixes = [
        vec!["ov", "ls", "--node-limit"],
        vec!["ov", "tree", "viking://resources", "--node-limit"],
        vec!["ov", "find", "query", "--node-limit"],
        vec!["ov", "search", "query", "--node-limit"],
        vec!["ov", "grep", "query", "--node-limit"],
        vec!["ov", "glob", "**/*", "--node-limit"],
        vec!["ov", "skills", "list", "--node-limit"],
        vec!["ov", "skills", "find", "query", "--node-limit"],
    ];

    for prefix in command_prefixes {
        let mut negative_args = prefix.clone();
        negative_args.push("-1");
        assert!(
            Cli::try_parse_from(&negative_args).is_err(),
            "{negative_args:?} should reject a negative node limit"
        );

        let mut zero_args = prefix.clone();
        zero_args.push("0");
        assert!(
            Cli::try_parse_from(&zero_args).is_ok(),
            "{zero_args:?} should preserve the established zero-limit semantics"
        );

        let mut args = prefix;
        args.push("1");
        assert!(
            Cli::try_parse_from(&args).is_ok(),
            "{args:?} should accept a positive node limit"
        );
    }

    for prefix in [
        vec!["ov", "ls", "--limit"],
        vec!["ov", "tree", "viking://resources", "--limit"],
    ] {
        let mut zero_args = prefix.clone();
        zero_args.push("0");
        assert!(Cli::try_parse_from(&zero_args).is_err());

        let mut positive_args = prefix;
        positive_args.push("1");
        assert!(Cli::try_parse_from(&positive_args).is_ok());
    }

    for prefix in [
        vec!["ov", "ls", "--offset"],
        vec!["ov", "tree", "viking://resources", "--offset"],
    ] {
        let mut negative_args = prefix.clone();
        negative_args.push("-1");
        assert!(Cli::try_parse_from(&negative_args).is_err());

        let mut zero_args = prefix;
        zero_args.push("0");
        assert!(Cli::try_parse_from(&zero_args).is_ok());
    }
}

#[test]
fn cli_status_parses_verbose_flag() {
    let cli = Cli::try_parse_from(["ov", "status", "--verbose"]).expect("status verbose parses");

    match cli.command {
        Commands::Status { verbose } => assert!(verbose),
        _ => panic!("expected status command"),
    }
}

#[test]
fn observer_alias_and_snapshot_restore_examples_parse() {
    let observer = Cli::try_parse_from(["ov", "observer", "fs"]).unwrap();
    assert!(matches!(
        observer.command,
        Commands::Observer {
            action: ObserverCommands::Filesystem
        }
    ));

    let snapshot = Cli::try_parse_from([
        "ov",
        "snapshot",
        "restore",
        "abc123",
        "viking://projects/acme",
    ])
    .unwrap();
    assert!(matches!(
        snapshot.command,
        Commands::Snapshot {
            cmd: SnapshotCmd::Restore {
                source_commit,
                project_dir,
                ..
            }
        } if source_commit == "abc123" && project_dir.as_deref() == Some("viking://projects/acme")
    ));
}

#[test]
fn cli_write_rejects_removed_semantic_flags() {
    let result = Cli::try_parse_from([
        "ov",
        "write",
        "viking://resources/demo.md",
        "--content",
        "updated",
        "--no-semantics",
        "--no-vectorize",
    ]);

    assert!(result.is_err(), "removed write flags should not parse");
}

#[test]
fn cli_import_rejects_removed_vectorize_flag() {
    let result = Cli::try_parse_from([
        "ov",
        "import",
        "./exports/demo.ovpack",
        "viking://resources/imported/",
        "--no-vectorize",
    ]);

    assert!(
        result.is_err(),
        "removed import vectorize flag should not parse"
    );
}

#[test]
fn cli_import_rejects_removed_force_flag() {
    let result = Cli::try_parse_from([
        "ov",
        "import",
        "./exports/demo.ovpack",
        "viking://resources/imported/",
        "--force",
    ]);

    assert!(
        result.is_err(),
        "removed import force flag should not parse"
    );
}

#[test]
fn cli_manifest_mode_takes_run_options_via_args() {
    let result = Cli::try_parse_from([
        "ov",
        "add-resource",
        "--manifest",
        "manifests/code-qa.yaml",
        "--args",
        "catalog:catalog.yaml,dry_run:true,skip_failed:true",
    ]);

    assert!(result.is_ok(), "manifest mode with --args should parse");
}

#[test]
fn cli_manifest_mode_dropped_dedicated_run_flags() {
    for flag in [
        "--catalog=catalog.yaml",
        "--dry-run",
        "--skip-failed",
        "--external-connector",
    ] {
        let result =
            Cli::try_parse_from(["ov", "add-resource", "--manifest", "code-qa.yaml", flag]);
        assert!(
            result.is_err(),
            "removed manifest flag {flag} should not parse"
        );
    }
}

#[test]
fn cli_manifest_mode_rejects_silently_ignored_single_resource_options() {
    for args in [
        ["--reason", "why"],
        ["--instruction", "how"],
        ["--no-directly-upload-media", "--wait"],
    ] {
        let result = Cli::try_parse_from(
            ["ov", "add-resource", "--manifest", "code-qa.yaml"]
                .into_iter()
                .chain(args),
        );
        assert!(
            result.is_err(),
            "{} must conflict with --manifest instead of being ignored",
            args[0]
        );
    }
}

#[test]
fn cli_parses_reindex_command() {
    let result = Cli::try_parse_from([
        "ov",
        "reindex",
        "viking://resources/demo",
        "--mode",
        "semantic_and_vectors",
        "--wait=false",
        "--force",
        "--tags",
        "team=search",
        "--tag-mode",
        "append",
        "--recursive=false",
    ]);

    let cli = result.expect("reindex command should parse");
    match cli.command {
        Commands::Reindex {
            force,
            tags,
            tag_mode,
            recursive,
            ..
        } => {
            assert_eq!(tags, vec!["team=search"]);
            assert!(force);
            assert_eq!(tag_mode, "append");
            assert!(!recursive);
        }
        _ => panic!("expected reindex command"),
    }
}

#[test]
fn cli_rejects_unknown_reindex_mode() {
    let result = Cli::try_parse_from([
        "ov",
        "reindex",
        "viking://resources/demo",
        "--mode",
        "semantic",
    ]);

    assert!(result.is_err(), "unknown reindex mode should not parse");
}

#[test]
fn append_time_filter_params_only_emits_after_and_before() {
    let mut params = Vec::new();
    let after = Some("7d".to_string());
    let before = Some("2026-03-12".to_string());

    handlers::append_time_filter_params(&mut params, after.as_deref(), before.as_deref());

    assert_eq!(params, vec!["--after 7d", "--before 2026-03-12"]);
}
