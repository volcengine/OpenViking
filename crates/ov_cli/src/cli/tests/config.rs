use crate::cli::{Cli, Commands, ConfigAddTarget, ConfigCommands};
use clap::Parser;

#[test]
fn server_commands_require_existing_cli_config() {
    let cli = Cli::try_parse_from(["ov", "ls"]).expect("ls should parse");
    let paged_ls = Cli::try_parse_from([
        "ov",
        "ls",
        "--offset",
        "4",
        "--limit",
        "5",
        "--sort-by",
        "mtime",
        "--sort-order",
        "desc",
        "--include-abstract",
        "--include-overview=false",
        "--overview-limit",
        "512",
    ])
    .expect("paged ls should parse");
    let paged_tree = Cli::try_parse_from([
        "ov",
        "tree",
        "viking://resources",
        "--offset",
        "6",
        "--limit",
        "7",
        "--directories-only",
        "--include-abstract",
        "--include-overview=false",
        "--overview-limit",
        "512",
    ])
    .expect("paged tree should parse");
    let health = Cli::try_parse_from(["ov", "health"]).expect("health should parse");

    match paged_ls.command {
        Commands::Ls {
            offset,
            limit,
            sort_by,
            sort_order,
            node_limit,
            include_abstract,
            include_overview,
            overview_limit,
            ..
        } => {
            assert_eq!(offset, 4);
            assert_eq!(limit, Some(5));
            assert_eq!(sort_by.as_deref(), Some("mtime"));
            assert_eq!(sort_order.as_deref(), Some("desc"));
            assert_eq!(node_limit, 256);
            assert_eq!(include_abstract, Some(true));
            assert_eq!(include_overview, Some(false));
            assert_eq!(overview_limit, 512);
        }
        _ => panic!("expected ls command"),
    }
    match paged_tree.command {
        Commands::Tree {
            offset,
            limit,
            node_limit,
            directories_only,
            include_abstract,
            include_overview,
            overview_limit,
            ..
        } => {
            assert_eq!(offset, 6);
            assert_eq!(limit, Some(7));
            assert_eq!(node_limit, 256);
            assert!(directories_only);
            assert_eq!(include_abstract, Some(true));
            assert_eq!(include_overview, Some(false));
            assert_eq!(overview_limit, 512);
        }
        _ => panic!("expected tree command"),
    }
    assert!(cli.command.requires_cli_config_file());
    assert!(health.command.requires_cli_config_file());
}

#[test]
fn bare_config_switch_and_version_do_not_require_existing_cli_config() {
    let setup = Cli::try_parse_from(["ov", "config"]).expect("bare config should parse");
    let switch =
        Cli::try_parse_from(["ov", "config", "switch"]).expect("config switch should parse");
    let switch_named = Cli::try_parse_from(["ov", "config", "switch", "prod"])
        .expect("named config switch should parse");
    let version = Cli::try_parse_from(["ov", "version"]).expect("version should parse");

    assert!(!setup.command.requires_cli_config_file());
    assert!(!switch.command.requires_cli_config_file());
    assert!(!switch_named.command.requires_cli_config_file());
    assert!(!version.command.requires_cli_config_file());

    let skills_validate = Cli::try_parse_from(["ov", "skills", "validate", "./skills/foo"])
        .expect("skills validate should parse");
    assert!(!skills_validate.command.requires_cli_config_file());
}

#[test]
fn config_agent_commands_parse_without_secret_value_flags() {
    let add_ov_service = Cli::try_parse_from([
        "ov",
        "config",
        "add",
        "ov-service",
        "--name",
        "prod",
        "--api-key-stdin",
        "--activate",
    ])
    .expect("ov-service add should parse");
    let Commands::Config {
        action:
            Some(ConfigCommands::Add {
                target: ConfigAddTarget::OvService(ov_service),
            }),
    } = add_ov_service.command
    else {
        panic!("expected ov-service add command");
    };
    assert_eq!(ov_service.name.as_deref(), Some("prod"));
    assert!(ov_service.api_key_stdin);
    assert!(ov_service.activate);

    let add_custom = Cli::try_parse_from([
        "ov",
        "config",
        "add",
        "custom",
        "--url",
        "https://ov.example.com",
        "--api-key-env",
        "OV_KEY",
        "--actor-peer-id",
        "peer-a",
    ])
    .expect("custom add should parse");
    let Commands::Config {
        action:
            Some(ConfigCommands::Add {
                target: ConfigAddTarget::Custom(custom),
            }),
    } = add_custom.command
    else {
        panic!("expected custom add command");
    };
    assert_eq!(custom.url.as_deref(), Some("https://ov.example.com"));
    assert_eq!(custom.api_key_env.as_deref(), Some("OV_KEY"));
    assert_eq!(custom.actor_peer_id.as_deref(), Some("peer-a"));

    let edit = Cli::try_parse_from([
        "ov",
        "config",
        "edit",
        "prod",
        "--clear-api-key",
        "--clear-actor-peer-id",
        "--activate",
    ])
    .expect("config edit should parse");
    let Commands::Config {
        action: Some(ConfigCommands::Edit(edit)),
    } = edit.command
    else {
        panic!("expected edit command");
    };
    assert_eq!(edit.name, "prod");
    assert!(edit.clear_api_key);
    assert!(edit.clear_actor_peer_id);
    assert!(edit.activate);

    assert!(
        Cli::try_parse_from(["ov", "config", "add", "ov-service", "--api-key", "secret"]).is_err(),
        "plain API key flag must stay rejected"
    );
    assert!(
        Cli::try_parse_from(["ov", "config", "add", "cloud", "--api-key-stdin"]).is_err(),
        "old cloud provider subcommand must stay rejected"
    );
    assert!(
        Cli::try_parse_from(["ov", "config", "add", "self-managed"]).is_err(),
        "old self-managed provider subcommand must stay rejected"
    );
    assert!(
        Cli::try_parse_from([
            "ov",
            "config",
            "add",
            "custom",
            "--use-root-key-for-normal-commands",
        ])
        .is_err(),
        "root-as-normal flag is obsolete and must stay rejected"
    );
    assert!(
        Cli::try_parse_from([
            "ov",
            "config",
            "edit",
            "prod",
            "--use-root-key-for-normal-commands",
        ])
        .is_err(),
        "root-as-normal edit flag is obsolete and must stay rejected"
    );
}

#[test]
fn cli_config_without_subcommand_parses_as_setup_entrypoint() {
    Cli::try_parse_from(["ov", "config"]).expect("bare config command should parse");
}

#[test]
fn cli_config_single_purpose_subcommands_still_parse() {
    for subcommand in ["show", "validate", "switch"] {
        Cli::try_parse_from(["ov", "config", subcommand])
            .unwrap_or_else(|_| panic!("config {subcommand} should parse"));
    }
}

#[test]
fn cli_config_rejects_removed_setup_cli_subcommand() {
    assert!(Cli::try_parse_from(["ov", "config", "setup-cli"]).is_err());
}

#[test]
fn config_commands_can_load_invalid_runtime_values_for_repair() {
    let cli = Cli::try_parse_from(["ov", "config", "show"]).expect("config show should parse");
    assert!(cli.command.allows_invalid_runtime_config());

    let status = Cli::try_parse_from(["ov", "status"]).expect("status should parse");
    assert!(!status.command.allows_invalid_runtime_config());
}
