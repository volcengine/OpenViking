use crate::cli::upload::legacy_upload_option_error;
use crate::cli::{Cli, Commands, UploadCliOptions};
use clap::{CommandFactory, Parser};

#[test]
fn cli_tree_help_hides_upload_and_admin_only_flags() {
    let err = Cli::command()
        .try_get_matches_from(["ov", "tree", "--help"])
        .expect_err("help should exit through clap error");
    let help = err.to_string();

    assert!(!help.contains("--progress"));
    assert!(!help.contains("--no-progress"));
    assert!(!help.contains("--verbose"));
    assert!(!help.contains("--sudo"));
}

#[test]
fn cli_add_resource_help_shows_upload_flags() {
    let err = Cli::command()
        .try_get_matches_from(["ov", "add-resource", "--help"])
        .expect_err("help should exit through clap error");
    let help = err.to_string();

    assert!(help.contains("--progress"));
    assert!(help.contains("--no-progress"));
    assert!(help.contains("--verbose"));
    assert!(!help.contains("--parse-mode"));
    assert!(help.contains("--args"));
}

#[test]
fn cli_add_resource_help_shows_tag_flags() {
    let err = Cli::command()
        .try_get_matches_from(["ov", "add-resource", "--help"])
        .expect_err("help should exit through clap error");
    let help = err.to_string();

    assert!(help.contains("--tag"));
    assert!(help.contains("--tag-mode"));
}

#[test]
fn cli_add_skill_help_shows_upload_flags() {
    let err = Cli::command()
        .try_get_matches_from(["ov", "add-skill", "--help"])
        .expect_err("help should exit through clap error");
    let help = err.to_string();

    assert!(help.contains("--progress"));
    assert!(help.contains("--no-progress"));
    assert!(help.contains("--verbose"));
}

#[test]
fn cli_tree_rejects_upload_flags_after_subcommand() {
    assert!(Cli::try_parse_from(["ov", "tree", "viking://", "--progress"]).is_err());
    assert!(Cli::try_parse_from(["ov", "tree", "viking://", "--no-progress"]).is_err());
    assert!(Cli::try_parse_from(["ov", "tree", "viking://", "--verbose"]).is_err());
}

#[test]
fn cli_parses_sudo_as_hidden_global_flag_after_subcommand() {
    let cli = Cli::try_parse_from(["ov", "tree", "viking://", "--sudo"])
        .expect("sudo should parse anywhere so runtime can show a precise command error");

    assert!(cli.sudo);
}

#[test]
fn cli_parses_upload_flags_on_upload_commands() {
    let add_resource = Cli::try_parse_from([
        "ov",
        "add-resource",
        "./README.md",
        "--progress",
        "--verbose",
    ])
    .expect("add-resource upload flags should parse");
    match add_resource.command {
        Commands::AddResource { upload_options, .. } => {
            assert!(upload_options.progress);
            assert!(upload_options.verbose);
        }
        _ => panic!("expected add-resource command"),
    }

    let add_skill = Cli::try_parse_from(["ov", "add-skill", "./skill", "--no-progress"])
        .expect("add-skill upload flags should parse");
    match add_skill.command {
        Commands::AddSkill(args) => {
            assert!(args.upload_options.no_progress);
        }
        _ => panic!("expected add-skill command"),
    }

    assert!(Cli::try_parse_from(["ov", "skills", "add", "./skill", "--progress"]).is_ok());
    assert!(Cli::try_parse_from(["ov", "skills", "update", "--progress"]).is_err());
}

#[test]
fn cli_keeps_legacy_pre_command_upload_flags() {
    let cli = Cli::try_parse_from([
        "ov",
        "--progress",
        "--verbose",
        "add-resource",
        "./README.md",
    ])
    .expect("legacy pre-command upload flags should still parse");

    assert!(cli.progress);
    assert!(cli.verbose);
}

#[test]
fn legacy_pre_command_upload_flags_only_allow_upload_commands() {
    let upload_options = UploadCliOptions {
        progress: true,
        no_progress: false,
        verbose: false,
    };

    let tree = Cli::try_parse_from(["ov", "--progress", "tree", "viking://"])
        .expect("hidden legacy flag still parses before runtime validation");
    assert_eq!(
        legacy_upload_option_error(upload_options, &tree.command),
        Some(
            "--progress, --no-progress, and --verbose are only supported for add-resource, add-skill, and skills add."
        )
    );

    let add_resource = Cli::try_parse_from(["ov", "--progress", "add-resource", "./README.md"])
        .expect("legacy pre-command upload flags should parse for add-resource");
    assert!(legacy_upload_option_error(upload_options, &add_resource.command).is_none());

    let skills_add = Cli::try_parse_from(["ov", "--progress", "skills", "add", "./skill"])
        .expect("hidden legacy flag still parses before runtime validation");
    assert!(legacy_upload_option_error(upload_options, &skills_add.command).is_none());
}
