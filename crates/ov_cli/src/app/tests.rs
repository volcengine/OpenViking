use crate::app::{
    LanguageGateAction, is_language_command_request, language_command_can_run_picker,
    language_gate_action, language_required_message, render_pre_language_help_request,
    resolve_output_format,
};
use crate::cli::tests::os_args;
use crate::cli::{Cli, Commands};
use crate::config::Config;
use crate::output::OutputFormat;
use clap::Parser;

#[test]
fn cli_language_command_and_alias_parse() {
    let cli =
        Cli::try_parse_from(["ov", "language", "zh-CN"]).expect("language command should parse");
    match cli.command {
        Commands::Language { language } => assert_eq!(language.as_deref(), Some("zh-CN")),
        _ => panic!("expected language command"),
    }

    let cli = Cli::try_parse_from(["ov", "lang"]).expect("language alias should parse");
    match cli.command {
        Commands::Language { language } => assert!(language.is_none()),
        _ => panic!("expected language command"),
    }
}

#[test]
fn configured_output_is_used_unless_cli_overrides_it() {
    let config = Config {
        output: "json".to_string(),
        ..Config::default()
    };

    assert_eq!(resolve_output_format(None, &config), OutputFormat::Json);
    assert_eq!(
        resolve_output_format(Some(OutputFormat::Table), &config),
        OutputFormat::Table
    );
    let cli = Cli::try_parse_from(["ov", "status"]).unwrap();
    assert_eq!(cli.output, None);

    let invalid_config = Config {
        output: "yaml".to_string(),
        ..Config::default()
    };
    assert_eq!(
        resolve_output_format(None, &invalid_config),
        OutputFormat::Table,
        "repair commands need a safe fallback for invalid persisted output"
    );
}

#[test]
fn only_explicit_clap_help_bypasses_language_setup() {
    for args in [
        ["ov", "--help"].as_slice(),
        ["ov", "-h"].as_slice(),
        ["ov", "status", "--help"].as_slice(),
        ["ov", "system", "wait", "-h"].as_slice(),
    ] {
        assert!(
            render_pre_language_help_request(&os_args(args)).is_some(),
            "{args:?} should render before language setup"
        );
    }

    for args in [
        ["ov", "status"].as_slice(),
        ["ov", "task"].as_slice(),
        ["ov", "-help"].as_slice(),
        ["ov", "--account", "--help", "status"].as_slice(),
    ] {
        assert!(
            render_pre_language_help_request(&os_args(args)).is_none(),
            "{args:?} should keep the language gate"
        );
    }
}

#[test]
fn language_gate_detects_language_command_after_global_flags() {
    assert!(is_language_command_request(&os_args(&["ov", "language"])));
    assert!(is_language_command_request(&os_args(&[
        "ov", "lang", "zh-CN"
    ])));
    assert!(is_language_command_request(&os_args(&[
        "ov", "--output", "json", "language", "en",
    ])));
    assert!(is_language_command_request(&os_args(&[
        "ov",
        "--output=json",
        "--compact",
        "false",
        "lang",
    ])));
    assert!(is_language_command_request(&os_args(&[
        "ov",
        "--compact",
        "lang",
    ])));
    assert!(!is_language_command_request(&os_args(&["ov", "status"])));
    assert!(!is_language_command_request(&os_args(&["ov", "--help"])));
}

#[test]
fn language_gate_action_continues_prompts_or_exits() {
    assert_eq!(
        language_gate_action(&os_args(&["ov", "status"]), true, false),
        LanguageGateAction::Continue
    );
    assert_eq!(
        language_gate_action(&os_args(&["ov", "language"]), false, false),
        LanguageGateAction::Continue
    );
    assert_eq!(
        language_gate_action(&os_args(&["ov", "status"]), false, true),
        LanguageGateAction::Prompt
    );
    assert_eq!(
        language_gate_action(&os_args(&["ov", "status"]), false, false),
        LanguageGateAction::ExitNonInteractive
    );
}

#[test]
fn language_gate_allows_config_agent_commands_without_saved_language() {
    for args in [
        &["ov", "config", "add", "ov-service", "--api-key-stdin"][..],
        &["ov", "config", "add", "custom"],
        &["ov", "config", "edit", "prod", "--activate"],
        &["ov", "config", "delete", "prod"],
        &["ov", "config", "list"],
        &["ov", "config", "switch", "prod"],
    ] {
        assert_eq!(
            language_gate_action(&os_args(args), false, false),
            LanguageGateAction::Continue,
            "{args:?} should bypass first-run language selection"
        );
    }

    assert_eq!(
        language_gate_action(&os_args(&["ov", "config"]), false, false),
        LanguageGateAction::ExitNonInteractive
    );
}

#[test]
fn language_gate_message_points_to_explicit_language_commands() {
    let message = language_required_message();

    assert!(message.contains("ov language en"));
    assert!(message.contains("ov language zh-CN"));
}

#[test]
fn language_command_without_value_requires_interactive_picker() {
    assert!(language_command_can_run_picker(true, false));
    assert!(language_command_can_run_picker(false, true));
    assert!(!language_command_can_run_picker(false, false));
}
