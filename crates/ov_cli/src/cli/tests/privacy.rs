use crate::cli::preprocess::{preprocess_cli_args, preprocess_privacy_args};
use crate::cli::{Cli, Commands, PrivacyCommands};
use clap::Parser;
use std::ffi::OsString;

#[test]
fn preprocess_field_dynamic_flags_to_static_form() {
    for dynamic_flag in ["--field-api_key", "--key-api_key"] {
        let args = vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("upsert"),
            OsString::from("skill"),
            OsString::from("demo"),
            OsString::from(dynamic_flag),
            OsString::from("secret-v1"),
        ];

        let converted = preprocess_privacy_args(args);
        let converted_strs: Vec<String> = converted
            .into_iter()
            .map(|s| s.to_string_lossy().to_string())
            .collect();

        assert_eq!(
            converted_strs,
            vec![
                "ov",
                "privacy",
                "upsert",
                "skill",
                "demo",
                "--field",
                "api_key=secret-v1",
            ]
        );
    }
}

fn on_cli_parser_stack(test: impl FnOnce() + Send + 'static) {
    std::thread::Builder::new()
        .stack_size(16 * 1024 * 1024)
        .spawn(test)
        .expect("spawn CLI parser test")
        .join()
        .expect("CLI parser test thread");
}

#[test]
fn cli_parses_privacy_tree_options_without_rewriting_to_get() {
    on_cli_parser_stack(|| {
        let args = [
            "ov",
            "privacy",
            "tree",
            "--category",
            "credential",
            "--references",
            "--output",
            "json",
        ]
        .into_iter()
        .map(OsString::from)
        .collect();
        let cli = Cli::parse_from(preprocess_privacy_args(args));
        let Commands::Privacy { action } = cli.command else {
            panic!("expected privacy command")
        };
        let PrivacyCommands::Tree {
            category,
            references,
        } = *action
        else {
            panic!("expected tree command")
        };
        assert_eq!(category.as_deref(), Some("credential"));
        assert!(references);
    });
}

#[test]
fn cli_parses_privacy_upsert_with_field_dynamic_flag() {
    on_cli_parser_stack(|| {
        let cli = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("upsert"),
            OsString::from("skill"),
            OsString::from("demo"),
            OsString::from("--field-api_key"),
            OsString::from("secret-v2"),
        ]));

        match cli.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Upsert {
                    category,
                    target_key,
                    field,
                    ..
                } => {
                    assert_eq!(category, "skill");
                    assert_eq!(target_key, "demo");
                    assert_eq!(field, vec!["api_key=secret-v2"]);
                }
                _ => panic!("expected privacy upsert"),
            },
            _ => panic!("expected privacy command"),
        }

        for flag in ["--field", "--key"] {
            let cli = Cli::parse_from(preprocess_privacy_args(vec![
                OsString::from("ov"),
                OsString::from("privacy"),
                OsString::from("upsert"),
                OsString::from("skill"),
                OsString::from("demo"),
                OsString::from(flag),
                OsString::from("api_key=secret-v3"),
            ]));
            match cli.command {
                Commands::Privacy { action } => match *action {
                    PrivacyCommands::Upsert { field, .. } => {
                        assert_eq!(field, vec!["api_key=secret-v3"]);
                    }
                    _ => panic!("expected privacy upsert"),
                },
                _ => panic!("expected privacy command"),
            }
        }
    });
}

#[test]
fn cli_parses_privacy_reference_resolve_and_delete() {
    on_cli_parser_stack(|| {
        for (subcommand, expected_field) in [("reference", "api_key"), ("resolve", "token")] {
            let cli = Cli::parse_from(preprocess_privacy_args(vec![
                OsString::from("ov"),
                OsString::from("privacy"),
                OsString::from(subcommand),
                OsString::from("credential"),
                OsString::from("github-prod"),
                OsString::from(expected_field),
            ]));

            match cli.command {
                Commands::Privacy { action } => match *action {
                    PrivacyCommands::Reference {
                        category,
                        target_key,
                        field,
                    } => {
                        assert_eq!(subcommand, "reference");
                        assert_eq!(category, "credential");
                        assert_eq!(target_key, "github-prod");
                        assert_eq!(field.as_deref(), Some(expected_field));
                    }
                    PrivacyCommands::Resolve {
                        selector_or_category,
                        target_key,
                        field,
                    } => {
                        assert_eq!(subcommand, "resolve");
                        assert_eq!(selector_or_category, "credential");
                        assert_eq!(target_key.as_deref(), Some("github-prod"));
                        assert_eq!(field.as_deref(), Some(expected_field));
                    }
                    _ => panic!("expected privacy field command"),
                },
                _ => panic!("expected privacy command"),
            }
        }

        for subcommand in ["reference", "resolve"] {
            let cli = Cli::parse_from(preprocess_privacy_args(vec![
                OsString::from("ov"),
                OsString::from("privacy"),
                OsString::from(subcommand),
                OsString::from("credential"),
                OsString::from("github-prod"),
            ]));

            match cli.command {
                Commands::Privacy { action } => match *action {
                    PrivacyCommands::Reference { field, .. } => {
                        assert_eq!(subcommand, "reference");
                        assert!(field.is_none());
                    }
                    PrivacyCommands::Resolve {
                        target_key, field, ..
                    } => {
                        assert_eq!(subcommand, "resolve");
                        assert_eq!(target_key.as_deref(), Some("github-prod"));
                        assert!(field.is_none());
                    }
                    _ => panic!("expected privacy field command"),
                },
                _ => panic!("expected privacy command"),
            }
        }

        let scan = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("scan"),
            OsString::from("--uri"),
            OsString::from("viking://~/resources/project-a"),
            OsString::from("--fix"),
        ]));
        match scan.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Scan {
                    uri,
                    fix,
                    node_limit,
                } => {
                    assert_eq!(uri, "viking://~/resources/project-a");
                    assert!(fix);
                    assert_eq!(node_limit, 1000);
                }
                _ => panic!("expected privacy scan"),
            },
            _ => panic!("expected privacy command"),
        }

        for invalid_args in [
            vec!["ov", "privacy", "scan"],
            vec![
                "ov",
                "privacy",
                "scan",
                "--uri",
                "viking://~/resources/project-a",
                "--node-limit",
                "1001",
            ],
        ] {
            assert!(Cli::try_parse_from(invalid_args).is_err());
        }

        let reference =
            "{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}";
        let resolved = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("resolve"),
            OsString::from(reference),
        ]));
        match resolved.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Resolve {
                    selector_or_category,
                    target_key,
                    field,
                } => {
                    assert_eq!(selector_or_category, reference);
                    assert!(target_key.is_none() && field.is_none());
                }
                _ => panic!("expected privacy resolve"),
            },
            _ => panic!("expected privacy command"),
        }

        let cli = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("delete"),
            OsString::from("credential"),
            OsString::from("github-prod"),
            OsString::from("--yes"),
        ]));
        match cli.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Delete {
                    selector_or_category,
                    target_key,
                    field,
                    yes,
                } => {
                    assert_eq!(selector_or_category, "credential");
                    assert_eq!(target_key.as_deref(), Some("github-prod"));
                    assert!(field.is_none());
                    assert!(yes);
                }
                _ => panic!("expected privacy delete"),
            },
            _ => panic!("expected privacy command"),
        }

        let field_delete = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("delete"),
            OsString::from("credential"),
            OsString::from("github-prod"),
            OsString::from("api_key"),
            OsString::from("--yes"),
        ]));
        match field_delete.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Delete {
                    selector_or_category,
                    target_key,
                    field,
                    yes,
                } => {
                    assert_eq!(selector_or_category, "credential");
                    assert_eq!(target_key.as_deref(), Some("github-prod"));
                    assert_eq!(field.as_deref(), Some("api_key"));
                    assert!(yes);
                }
                _ => panic!("expected privacy field delete"),
            },
            _ => panic!("expected privacy command"),
        }
    });
}

#[test]
fn cli_parses_privacy_shortcut_as_get() {
    on_cli_parser_stack(|| {
        let cli = Cli::parse_from(preprocess_privacy_args(vec![
            OsString::from("ov"),
            OsString::from("privacy"),
            OsString::from("skill"),
            OsString::from("demo"),
        ]));

        match cli.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Get {
                    category,
                    target_key,
                } => {
                    assert_eq!(category, "skill");
                    assert_eq!(target_key, "demo");
                }
                _ => panic!("expected privacy get"),
            },
            _ => panic!("expected privacy command"),
        }
    });
}

#[test]
fn cli_parses_privacy_shortcut_after_compact_without_value() {
    on_cli_parser_stack(|| {
        let cli = Cli::try_parse_from(preprocess_cli_args(vec![
            OsString::from("ov"),
            OsString::from("--compact"),
            OsString::from("privacy"),
            OsString::from("skill"),
            OsString::from("demo"),
        ]))
        .expect("--compact without a value should not consume the privacy command");

        assert!(cli.compact);
        match cli.command {
            Commands::Privacy { action } => match *action {
                PrivacyCommands::Get {
                    category,
                    target_key,
                } => {
                    assert_eq!(category, "skill");
                    assert_eq!(target_key, "demo");
                }
                _ => panic!("expected privacy get"),
            },
            _ => panic!("expected privacy command"),
        }

        let explicit_false = Cli::try_parse_from(preprocess_cli_args(vec![
            OsString::from("ov"),
            OsString::from("-c"),
            OsString::from("false"),
            OsString::from("privacy"),
            OsString::from("skill"),
            OsString::from("demo"),
        ]))
        .expect("-c false should keep its explicit bool value");
        assert!(!explicit_false.compact);
    });
}
