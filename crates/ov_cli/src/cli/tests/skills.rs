use crate::cli::{Cli, Commands, SkillCommands};
use crate::output::OutputFormat;
use clap::Parser;

#[test]
fn cli_parses_skills_command_group() {
    let list = Cli::try_parse_from(["ov", "skills", "list", "--limit", "25"])
        .expect("skills list should parse");
    match list.command {
        Commands::Skills {
            action: SkillCommands::List { node_limit, .. },
        } => assert_eq!(node_limit, 25),
        _ => panic!("expected skills list"),
    }

    let find = Cli::try_parse_from([
        "ov",
        "skills",
        "find",
        "review code",
        "--threshold",
        "0.4",
        "--level",
        "0,1",
    ])
    .expect("skills find should parse");
    match find.command {
        Commands::Skills {
            action:
                SkillCommands::Find {
                    query,
                    threshold,
                    level,
                    ..
                },
        } => {
            assert_eq!(query, "review code");
            assert_eq!(threshold, Some(0.4));
            assert_eq!(level, Some(vec![0, 1]));
        }
        _ => panic!("expected skills find"),
    }

    let update = Cli::try_parse_from(["ov", "skills", "update", "code-review", "--wait", "--yes"])
        .expect("skills update should parse");
    match update.command {
        Commands::Skills {
            action: SkillCommands::Update {
                skills, wait, yes, ..
            },
        } => {
            assert_eq!(skills, vec!["code-review"]);
            assert!(wait);
            assert!(yes);
        }
        _ => panic!("expected skills update"),
    }

    for mut argv in [vec!["ov", "add-skill"], vec!["ov", "skills", "add"]] {
        argv.extend([
            "https://github.com/acme/skills.git",
            "--skill",
            "foo",
            "bar",
            "--list",
            "--yes",
            "--wait",
            "--parent-auto-create",
            "viking://agent/skills",
            "--no-progress",
        ]);
        let add_selected = Cli::try_parse_from(argv)
            .expect("both skill add commands should accept the same options");
        let args = match add_selected.command {
            Commands::AddSkill(args)
            | Commands::Skills {
                action: SkillCommands::Add(args),
            } => args,
            _ => panic!("expected skill add command"),
        };
        assert_eq!(args.source, "https://github.com/acme/skills.git");
        assert_eq!(args.skills, vec!["foo", "bar"]);
        assert!(args.list);
        assert!(args.yes);
        assert!(args.wait);
        assert_eq!(args.parent.as_deref(), Some("viking://agent/skills"));
        assert!(args.upload_options.no_progress);
    }

    let show = Cli::try_parse_from([
        "ov",
        "skills",
        "show",
        "code-review",
        "--level",
        "2",
        "--files",
        "--source",
        "--format",
        "json",
    ])
    .expect("skills show RFC flags should parse");
    match show.command {
        Commands::Skills {
            action:
                SkillCommands::Show {
                    level,
                    files,
                    source,
                    format,
                    ..
                },
        } => {
            assert_eq!(level, Some(2));
            assert!(files);
            assert!(source);
            assert_eq!(format.as_deref(), Some("json"));
        }
        _ => panic!("expected skills show"),
    }

    let show_global_output =
        Cli::try_parse_from(["ov", "skills", "show", "code-review", "-o", "json"])
            .expect("skills show should accept global -o after the subcommand");
    assert_eq!(show_global_output.output, Some(OutputFormat::Json));

    let remove = Cli::try_parse_from(["ov", "skills", "remove", "foo", "bar", "--yes"])
        .expect("skills remove --yes should parse");
    match remove.command {
        Commands::Skills {
            action: SkillCommands::Remove {
                skills, yes, all, ..
            },
        } => {
            assert_eq!(skills, vec!["foo", "bar"]);
            assert!(yes);
            assert!(!all);
        }
        _ => panic!("expected skills remove"),
    }

    let validate = Cli::try_parse_from(["ov", "skills", "validate", "./skills/foo", "--strict"])
        .expect("skills validate --strict should parse");
    match validate.command {
        Commands::Skills {
            action: SkillCommands::Validate { path, strict },
        } => {
            assert_eq!(path, "./skills/foo");
            assert!(strict);
        }
        _ => panic!("expected skills validate"),
    }
}
