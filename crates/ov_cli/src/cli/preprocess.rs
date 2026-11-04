//! Argument normalization and command detection before parsing.

use crate::cli::Cli;
use crate::cli_arg_scan;
use crate::output::OutputFormat;
use clap::CommandFactory;
use std::ffi::OsString;

pub(crate) fn find_command_index(args: &[OsString]) -> Option<usize> {
    let root_value_options = cli_root_value_options();
    let mut i = 1;
    while i < args.len() {
        let token = args[i].to_string_lossy();
        let token_ref = token.as_ref();

        if token_ref == "--compact" || token_ref == "-c" {
            i += compact_option_width(args, i);
            continue;
        }
        if token_ref.starts_with('-') {
            i += if root_value_options.consumes_value(token_ref) && !token_ref.contains('=') {
                2
            } else {
                1
            };
            continue;
        }

        return Some(i);
    }
    None
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PlainHelpMisuse {
    pub(crate) help_command: String,
}

pub(crate) fn plain_help_misuse(args: &[OsString]) -> Option<PlainHelpMisuse> {
    let tokens = command_tokens_for_plain_help(args);
    let help_index = tokens.iter().position(|token| token == "help")?;
    if help_index == 0 {
        return Some(PlainHelpMisuse {
            help_command: "ov --help".to_string(),
        });
    }

    let command = tokens
        .first()
        .map(|token| canonical_plain_help_token(token))?;
    if allows_plain_help_as_user_input(command) {
        return None;
    }

    let mut path = vec![command.to_string()];
    if is_plain_help_group(command) {
        if help_index == 1 {
            return Some(PlainHelpMisuse {
                help_command: prefixed_help_command(&path),
            });
        }
        if let Some(subcommand) = tokens.get(1).map(|token| canonical_plain_help_token(token)) {
            path.push(subcommand.to_string());
        }
        if command == "task"
            && path.get(1).is_some_and(|token| token == "watch")
            && let Some(watch_subcommand) =
                tokens.get(2).map(|token| canonical_plain_help_token(token))
            && watch_subcommand != "help"
        {
            path.push(watch_subcommand.to_string());
        }
        return Some(PlainHelpMisuse {
            help_command: prefixed_help_command(&path),
        });
    }

    Some(PlainHelpMisuse {
        help_command: prefixed_help_command(&path),
    })
}

fn command_tokens_for_plain_help(args: &[OsString]) -> Vec<String> {
    let value_options = cli_value_options();

    let mut tokens = Vec::new();
    let mut i = 1;
    while i < args.len() {
        let token = args[i].to_string_lossy();
        let token_ref = token.as_ref();
        if value_options.consumes_value(token_ref) {
            i += if token_ref.contains('=') { 1 } else { 2 };
            continue;
        }
        if token_ref.starts_with('-') {
            i += 1;
            continue;
        }
        tokens.push(token.into_owned());
        i += 1;
    }
    tokens
}

fn cli_value_options() -> cli_arg_scan::ValueOptions {
    let mut command = Cli::command();
    command.build();
    cli_arg_scan::ValueOptions::from_command(&command)
}

fn cli_root_value_options() -> cli_arg_scan::ValueOptions {
    let mut command = Cli::command();
    command.build();
    cli_arg_scan::ValueOptions::from_command_arguments(&command)
}

fn canonical_plain_help_token(token: &str) -> &str {
    match token {
        "list" => "ls",
        "del" | "delete" => "rm",
        "rename" => "mv",
        "lang" => "language",
        other => other,
    }
}

fn allows_plain_help_as_user_input(command: &str) -> bool {
    matches!(
        command,
        "add-resource" | "add-skill" | "find" | "search" | "grep" | "glob" | "add-memory"
    )
}

fn is_plain_help_group(command: &str) -> bool {
    matches!(
        command,
        "config"
            | "task"
            | "admin"
            | "system"
            | "session"
            | "privacy"
            | "observer"
            | "skills"
            | "mcps"
    )
}

fn prefixed_help_command(path: &[String]) -> String {
    if path.is_empty() {
        "ov --help".to_string()
    } else {
        format!("ov {} --help", path.join(" "))
    }
}

pub(crate) fn pre_parse_requires_cli_config_file(args: &[OsString]) -> bool {
    let tokens = command_tokens_for_config_gate(args);
    let Some(command) = tokens
        .first()
        .map(|token| canonical_plain_help_token(token))
    else {
        return false;
    };

    match command {
        "config" => config_command_requires_cli_config_file(&tokens),
        "language" | "version" => false,
        "task" => known_task_command_requires_config(&tokens),
        "admin" => tokens
            .get(1)
            .map(|token| is_admin_subcommand(token))
            .unwrap_or(false),
        "system" => known_system_command_requires_config(&tokens),
        "session" => tokens
            .get(1)
            .map(|token| is_session_subcommand(token))
            .unwrap_or(false),
        "privacy" => tokens
            .get(1)
            .map(|token| is_privacy_subcommand(token))
            .unwrap_or(false),
        "skills" => tokens
            .get(1)
            .map(|token| is_skill_subcommand(token))
            .unwrap_or(false),
        "mcps" => tokens
            .get(1)
            .map(|token| is_mcp_subcommand(token))
            .unwrap_or(false),
        "observer" => tokens
            .get(1)
            .map(|token| is_observer_subcommand(token))
            .unwrap_or(false),
        _ => is_top_level_server_command(command),
    }
}

fn config_command_requires_cli_config_file(tokens: &[String]) -> bool {
    matches!(tokens.get(1).map(String::as_str), Some("show" | "validate"))
}

pub(crate) fn is_config_agent_command_request(args: &[OsString]) -> bool {
    let tokens = command_tokens_for_config_gate(args);
    if tokens.first().map(String::as_str) != Some("config") {
        return false;
    }

    match tokens.get(1).map(String::as_str) {
        Some("add" | "edit" | "delete" | "list") => true,
        Some("switch") => tokens.get(2).is_some(),
        _ => false,
    }
}

fn command_tokens_for_config_gate(args: &[OsString]) -> Vec<String> {
    let value_options = cli_value_options();
    let root_value_options = cli_root_value_options();
    let mut tokens = Vec::new();
    let mut seen_command = false;
    let mut i = 1;

    while i < args.len() {
        let token = args[i].to_string_lossy();
        let token_ref = token.as_ref();

        if token_ref == "--" {
            tokens.extend(
                args.iter()
                    .skip(i + 1)
                    .map(|arg| arg.to_string_lossy().to_string()),
            );
            break;
        }

        if !seen_command {
            if token_ref == "--compact" || token_ref == "-c" {
                i += compact_option_width(args, i);
                continue;
            }
            if token_ref.starts_with("--") {
                i += if root_value_options.consumes_value(token_ref) && !token_ref.contains('=') {
                    2
                } else {
                    1
                };
                continue;
            }
            if token_ref.starts_with('-') {
                i += if root_value_options.consumes_value(token_ref) && !token_ref.contains('=') {
                    2
                } else {
                    1
                };
                continue;
            }
            seen_command = true;
            tokens.push(token.into_owned());
            i += 1;
            continue;
        }

        if token_ref == "--compact" || token_ref == "-c" {
            i += compact_option_width(args, i);
            continue;
        }
        if value_options.consumes_value(token_ref) {
            i += if token_ref.contains('=') { 1 } else { 2 };
            continue;
        }
        if token_ref.starts_with('-') {
            i += 1;
            continue;
        }
        tokens.push(token.into_owned());
        i += 1;
    }

    tokens
}

fn known_task_command_requires_config(tokens: &[String]) -> bool {
    match tokens.get(1).map(String::as_str) {
        Some("status" | "cancel" | "list") => true,
        Some("watch") => match tokens.get(2).map(String::as_str) {
            None => true,
            Some(token) => is_watch_subcommand(token),
        },
        _ => false,
    }
}

fn known_system_command_requires_config(tokens: &[String]) -> bool {
    match tokens.get(1).map(String::as_str) {
        Some("wait" | "status" | "health" | "consistency") => true,
        Some("crypto") => matches!(tokens.get(2).map(String::as_str), None | Some("init-key")),
        _ => false,
    }
}

fn is_top_level_server_command(command: &str) -> bool {
    matches!(
        command,
        "add-resource"
            | "add-skill"
            | "ls"
            | "tree"
            | "mkdir"
            | "rm"
            | "mv"
            | "stat"
            | "read"
            | "abstract"
            | "overview"
            | "write"
            | "get"
            | "find"
            | "search"
            | "grep"
            | "glob"
            | "add-memory"
            | "export"
            | "backup"
            | "import"
            | "restore"
            | "tui"
            | "chat"
            | "wait"
            | "status"
            | "health"
            | "reindex"
    )
}

fn is_watch_subcommand(token: &str) -> bool {
    matches!(
        token,
        "ls" | "show" | "rm" | "pause" | "resume" | "update" | "trigger"
    )
}

fn is_skill_subcommand(token: &str) -> bool {
    matches!(
        token,
        "add" | "list" | "ls" | "find" | "show" | "update" | "remove" | "rm" | "delete"
    )
}

fn is_mcp_subcommand(token: &str) -> bool {
    matches!(
        token,
        "add"
            | "remove"
            | "list"
            | "find"
            | "validate"
            | "show"
            | "sync"
            | "status"
            | "mount"
            | "unmount"
            | "mounts"
            | "invoke"
    )
}

fn is_admin_subcommand(token: &str) -> bool {
    matches!(
        token,
        "create-account"
            | "list-accounts"
            | "delete-account"
            | "register-user"
            | "list-users"
            | "create-group"
            | "list-groups"
            | "list-group-members"
            | "add-group-member"
            | "remove-group-member"
            | "delete-group"
            | "remove-user"
            | "set-role"
            | "regenerate-key"
            | "set-account-settings"
    )
}

fn is_observer_subcommand(token: &str) -> bool {
    matches!(
        token,
        "queue" | "vikingdb" | "models" | "transaction" | "retrieval" | "filesystem" | "system"
    )
}

fn is_session_subcommand(token: &str) -> bool {
    matches!(
        token,
        "new"
            | "list"
            | "get"
            | "get-session-context"
            | "get-session-archive"
            | "delete"
            | "add-message"
            | "add-messages"
            | "commit"
    )
}

fn is_privacy_subcommand(token: &str) -> bool {
    matches!(
        token,
        "categories"
            | "tree"
            | "list"
            | "get"
            | "reference"
            | "resolve"
            | "upsert"
            | "versions"
            | "version"
            | "activate"
            | "delete"
            | "scan"
    )
}

fn preprocess_privacy_get_shortcut(args: Vec<OsString>) -> Vec<OsString> {
    let Some(cmd_idx) = find_command_index(&args) else {
        return args;
    };
    if args[cmd_idx].to_string_lossy() != "privacy" {
        return args;
    }
    let Some(next) = args.get(cmd_idx + 1) else {
        return args;
    };
    let next_token = next.to_string_lossy();
    if next_token.starts_with('-') || is_privacy_subcommand(&next_token) {
        return args;
    }

    let mut out = Vec::with_capacity(args.len() + 1);
    out.extend(args[..=cmd_idx].iter().cloned());
    out.push(OsString::from("get"));
    out.extend(args[cmd_idx + 1..].iter().cloned());
    out
}

fn preprocess_privacy_upsert_field_flags(args: Vec<OsString>) -> Vec<OsString> {
    let Some(cmd_idx) = find_command_index(&args) else {
        return args;
    };
    if args[cmd_idx].to_string_lossy() != "privacy" {
        return args;
    }
    if args
        .get(cmd_idx + 1)
        .map(|s| s.to_string_lossy().to_string())
        != Some("upsert".to_string())
    {
        return args;
    }

    let mut converted: Vec<OsString> = Vec::with_capacity(args.len());
    let mut i = 0;

    while i < args.len() {
        let arg_lossy = args[i].to_string_lossy();

        if i > cmd_idx + 1 && arg_lossy == "--" {
            i += 1;
            continue;
        }

        let dynamic_field = arg_lossy
            .strip_prefix("--field-")
            .or_else(|| arg_lossy.strip_prefix("--key-"));
        if i > cmd_idx + 1 && dynamic_field.is_some() {
            let suffix = dynamic_field.expect("checked above");
            if suffix.is_empty() {
                converted.push(args[i].clone());
                i += 1;
                continue;
            }

            if let Some((key, value)) = suffix.split_once('=') {
                converted.push(OsString::from("--field"));
                converted.push(OsString::from(format!("{}={}", key, value)));
                i += 1;
                continue;
            }

            if i + 1 < args.len() {
                let next_val = args[i + 1].to_string_lossy();
                converted.push(OsString::from("--field"));
                converted.push(OsString::from(format!("{}={}", suffix, next_val)));
                i += 2;
                continue;
            }

            converted.push(args[i].clone());
            i += 1;
            continue;
        }

        converted.push(args[i].clone());
        i += 1;
    }

    converted
}

fn preprocess_compact_args(args: Vec<OsString>) -> Vec<OsString> {
    if args.is_empty() {
        return args;
    }

    let mut converted = Vec::with_capacity(args.len());
    converted.push(args[0].clone());
    let mut i = 1;

    while i < args.len() {
        let token = args[i].to_string_lossy();
        if token == "--compact" || token == "-c" {
            if let Some(next) = args.get(i + 1) {
                let next_value = next.to_string_lossy();
                if is_bool_arg(&next_value) {
                    converted.push(OsString::from(format!("--compact={next_value}")));
                    i += 2;
                    continue;
                }
            }
            converted.push(OsString::from("--compact=true"));
            i += 1;
            continue;
        }

        converted.push(args[i].clone());
        i += 1;
    }

    converted
}

pub(crate) fn preprocess_cli_args(args: Vec<OsString>) -> Vec<OsString> {
    let args = preprocess_compact_args(args);
    preprocess_privacy_args(args)
}

pub(crate) fn pre_parse_output_options(args: &[OsString]) -> (OutputFormat, bool) {
    let Ok(matches) = Cli::command()
        .ignore_errors(true)
        .try_get_matches_from(args)
    else {
        return (OutputFormat::Table, true);
    };
    (
        matches
            .get_one::<OutputFormat>("output")
            .copied()
            .unwrap_or(OutputFormat::Table),
        matches.get_one::<bool>("compact").copied().unwrap_or(true),
    )
}

pub(crate) fn preprocess_privacy_args(args: Vec<OsString>) -> Vec<OsString> {
    let args = preprocess_privacy_get_shortcut(args);
    preprocess_privacy_upsert_field_flags(args)
}

pub(crate) fn first_command_token(args: &[OsString]) -> Option<String> {
    let root_value_options = cli_root_value_options();
    let mut index = 1usize;

    while index < args.len() {
        let token = args[index].to_string_lossy();
        if token == "--" {
            return args
                .get(index + 1)
                .map(|value| value.to_string_lossy().to_string());
        }
        if token == "--compact" || token == "-c" {
            index += compact_option_width(args, index);
            continue;
        }
        if token.starts_with("--") {
            index += if root_value_options.consumes_value(&token) && !token.contains('=') {
                2
            } else {
                1
            };
            continue;
        }
        if token.starts_with('-') {
            index += if root_value_options.consumes_value(&token) && !token.contains('=') {
                2
            } else {
                1
            };
            continue;
        }
        return Some(token.to_string());
    }

    None
}

fn compact_option_width(args: &[OsString], index: usize) -> usize {
    if args
        .get(index + 1)
        .is_some_and(|value| is_bool_arg(&value.to_string_lossy()))
    {
        2
    } else {
        1
    }
}

fn is_bool_arg(value: &str) -> bool {
    matches!(
        value,
        "true" | "false" | "True" | "False" | "TRUE" | "FALSE"
    )
}
