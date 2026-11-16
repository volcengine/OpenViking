//! CLI startup, language and configuration gates, and exit handling.

mod dispatch;

use crate::CliContext;
use crate::cli::preprocess::{
    first_command_token, is_config_agent_command_request, named_compile_conflict,
    plain_help_misuse, pre_parse_output_options, pre_parse_requires_cli_config_file,
    preprocess_cli_args,
};
use crate::cli::{Cli, Commands, UploadCliOptions, legacy_upload_option_error};
use crate::config::Config;
use crate::error::{Error, Result};
use crate::error_ui;
use crate::handlers;
use crate::help_ui;
use crate::i18n;
use crate::output::OutputFormat;
use crate::theme;
use clap::Parser;
use colored::Colorize;
use std::ffi::OsString;
use std::io::{self, IsTerminal};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LanguageGateAction {
    Continue,
    Prompt,
    ExitNonInteractive,
}

fn language_gate_action(
    args: &[OsString],
    has_selected_language: bool,
    is_interactive: bool,
) -> LanguageGateAction {
    if has_selected_language
        || is_language_command_request(args)
        || is_config_agent_command_request(args)
    {
        LanguageGateAction::Continue
    } else if is_interactive {
        LanguageGateAction::Prompt
    } else {
        LanguageGateAction::ExitNonInteractive
    }
}

async fn ensure_language_selected_before_command(args: &[OsString]) -> Result<bool> {
    match language_gate_action(
        args,
        i18n::has_saved_language() || i18n::explicit_language_from_env().is_some(),
        io::stdin().is_terminal() && io::stdout().is_terminal(),
    ) {
        LanguageGateAction::Continue => Ok(true),
        LanguageGateAction::Prompt => {
            handlers::handle_language(None).await?;
            Ok(i18n::has_saved_language())
        }
        LanguageGateAction::ExitNonInteractive => {
            eprintln!("{}", language_required_message());
            std::process::exit(2);
        }
    }
}

fn is_language_command_request(args: &[OsString]) -> bool {
    matches!(
        first_command_token(args).as_deref(),
        Some("language" | "lang")
    )
}

fn language_required_message() -> String {
    format!(
        "{} {}\n{}:\n  {}\n  {}",
        theme::brand_title("OpenViking").bold(),
        theme::body("needs a display language before running commands."),
        theme::strong("Run one of"),
        theme::command("ov language en").bold(),
        theme::command("ov language zh-CN").bold(),
    )
}

fn language_command_can_run_picker(has_language_value: bool, is_interactive: bool) -> bool {
    has_language_value || is_interactive
}

fn resolve_output_format(cli_output: Option<OutputFormat>, config: &Config) -> OutputFormat {
    cli_output.unwrap_or_else(|| OutputFormat::from(config.output.as_str()))
}

fn render_pre_language_help_request(args: &[OsString]) -> Option<String> {
    if !args
        .iter()
        .skip(1)
        .any(|arg| matches!(arg.to_str(), Some("-h" | "--help")))
    {
        return None;
    }

    let error = Cli::try_parse_from(args).err()?;
    if error.kind() != clap::error::ErrorKind::DisplayHelp {
        return None;
    }

    if help_ui::is_top_level_help_request(args) {
        return Some(help_ui::render_top_level_help());
    }
    help_ui::render_command_help_request(args).or_else(|| Some(error.to_string()))
}

pub(crate) async fn run() {
    let args = preprocess_cli_args(std::env::args_os().collect());
    let command_display = error_ui::display_command(&args);
    let (pre_parse_output_format, pre_parse_compact) = pre_parse_output_options(&args);
    if let Some(help) = render_pre_language_help_request(&args) {
        print!("{help}");
        return;
    }
    match ensure_language_selected_before_command(&args).await {
        Ok(true) => {}
        Ok(false) => return,
        Err(e) => {
            error_ui::print_runtime_error(
                &command_display,
                &e,
                pre_parse_output_format,
                pre_parse_compact,
                false,
            );
            std::process::exit(2);
        }
    }

    if help_ui::is_top_level_help_request(&args) {
        print!("{}", help_ui::render_top_level_help());
        return;
    }
    if let Some(help) = help_ui::render_command_help_request(&args) {
        print!("{help}");
        return;
    }
    if let Some(flag) = named_compile_conflict(&args) {
        let (title, message, guidance) = match i18n::Language::current() {
            i18n::Language::ZhCn => (
                "命令错误",
                format!("--compile-name 不能与 {flag} 同时使用。"),
                "请用 ov compile create --compile-name <名称> 保存输入参数，再用 ov compile --compile-name <名称> 运行。\n详见编译指南：https://docs.openviking.ai/zh/getting-started/13-compile#提交编译",
            ),
            i18n::Language::En => (
                "Command Error",
                format!("--compile-name cannot be combined with {flag}."),
                "Save inputs with ov compile create --compile-name <name>, then run ov compile --compile-name <name>.\nSee https://docs.openviking.ai/en/getting-started/13-compile#submit-compilation",
            ),
        };
        error_ui::print_report(&error_ui::ErrorReport::new(title, message), false);
        eprintln!("\n{guidance}");
        std::process::exit(2);
    }
    if let Some(misuse) = plain_help_misuse(&args) {
        let report = error_ui::report_for_plain_help_error(&command_display, misuse.help_command);
        error_ui::print_report(&report, false);
        std::process::exit(2);
    }

    let mut preloaded_required_config = if pre_parse_requires_cli_config_file(&args) {
        match Config::load_required() {
            Ok(config) => Some(config),
            Err(e) => {
                error_ui::print_runtime_error(
                    &command_display,
                    &e,
                    pre_parse_output_format,
                    pre_parse_compact,
                    false,
                );
                std::process::exit(2);
            }
        }
    } else {
        None
    };

    let cli = match Cli::try_parse_from(args.clone()) {
        Ok(cli) => cli,
        Err(error) => {
            if error.exit_code() == 0 {
                let _ = error.print();
            } else {
                let report = error_ui::report_for_clap_error(&args, &error.to_string());
                error_ui::print_report(&report, false);
            }
            std::process::exit(error.exit_code());
        }
    };

    let output_override = cli.output;
    let compact = cli.compact;
    let legacy_upload_options = UploadCliOptions {
        progress: cli.progress,
        no_progress: cli.no_progress,
        verbose: cli.verbose,
    };
    if let Some(message) = legacy_upload_option_error(legacy_upload_options, &cli.command) {
        let report = error_ui::report_for_message_error(
            &command_display,
            "Command Error",
            message,
            vec![
                error_ui::ErrorAction::new("ov add-resource --help", "Show add-resource options"),
                error_ui::ErrorAction::new("ov add-skill --help", "Show add-skill options"),
            ],
        );
        error_ui::print_report(&report, false);
        std::process::exit(2);
    }

    // Check this before loading config so misplaced --sudo reports the command rule directly.
    if cli.sudo && !cli.command.supports_sudo() {
        let language = i18n::Language::current();
        let (title, message, actions) = match language {
            i18n::Language::En => (
                "Command Error",
                "--sudo is only supported for admin, system, reindex, task status, and task list commands.",
                vec![
                    error_ui::ErrorAction::new("ov admin --help", "Show admin commands"),
                    error_ui::ErrorAction::new("ov system --help", "Show system commands"),
                    error_ui::ErrorAction::new("ov reindex --help", "Show reindex options"),
                    error_ui::ErrorAction::new("ov task --help", "Show task commands"),
                ],
            ),
            i18n::Language::ZhCn => (
                "命令错误",
                "--sudo 只支持 admin、system、reindex、task status 和 task list 命令。",
                vec![
                    error_ui::ErrorAction::new("ov admin --help", "查看管理命令"),
                    error_ui::ErrorAction::new("ov system --help", "查看系统命令"),
                    error_ui::ErrorAction::new("ov reindex --help", "查看重建索引选项"),
                    error_ui::ErrorAction::new("ov task --help", "查看任务命令"),
                ],
            ),
        };
        let report = error_ui::report_for_message_error(&command_display, title, message, actions);
        error_ui::print_report(&report, false);
        std::process::exit(2);
    }

    if let Commands::Language { language } = &cli.command {
        if !language_command_can_run_picker(
            language.is_some(),
            io::stdin().is_terminal() && io::stdout().is_terminal(),
        ) {
            eprintln!("{}", language_required_message());
            std::process::exit(2);
        }
        if let Err(e) = handlers::handle_language(language.clone()).await {
            error_ui::print_runtime_error(
                &command_display,
                &e,
                pre_parse_output_format,
                compact,
                cli.verbose,
            );
            std::process::exit(2);
        }
        return;
    }

    let config_result = if cli.command.requires_cli_config_file() {
        match preloaded_required_config.take() {
            Some(config) => Ok(config),
            None => Config::load_required(),
        }
    } else {
        Config::load_default()
    };
    let config = match config_result {
        Ok(config) => config,
        Err(e) => {
            error_ui::print_runtime_error(
                &command_display,
                &e,
                pre_parse_output_format,
                compact,
                cli.verbose,
            );
            std::process::exit(2);
        }
    };
    let output_format = resolve_output_format(output_override, &config);
    if !cli.command.allows_invalid_runtime_config()
        && let Err(e) = config.validate_runtime_values()
    {
        error_ui::print_runtime_error(&command_display, &e, output_format, compact, cli.verbose);
        std::process::exit(2);
    }
    let ctx = CliContext::from_config(
        config,
        output_format,
        compact,
        cli.account.clone(),
        cli.user.clone(),
        cli.actor_peer_id.clone(),
        cli.sudo,
        None,
        None,
        if cli.profile { Some(true) } else { None },
    );
    let verbose_errors = ctx.is_verbose();

    // Check if --sudo is used but root_api_key is not configured
    if ctx.sudo && ctx.config.root_api_key.is_none() {
        let language = i18n::Language::current();
        let (title, message, actions) = match language {
            i18n::Language::En => (
                "Configuration Error",
                "--sudo requires root_api_key in ~/.openviking/ovcli.conf.",
                vec![
                    error_ui::ErrorAction::new("ov config", "Edit the active config"),
                    error_ui::ErrorAction::new("ov config show", "Show the active config"),
                ],
            ),
            i18n::Language::ZhCn => (
                "配置错误",
                "--sudo 需要在 ~/.openviking/ovcli.conf 中配置 root_api_key。",
                vec![
                    error_ui::ErrorAction::new("ov config", "编辑当前配置"),
                    error_ui::ErrorAction::new("ov config show", "显示当前配置"),
                ],
            ),
        };
        let report = error_ui::report_for_message_error(&command_display, title, message, actions);
        error_ui::print_report(&report, false);
        std::process::exit(2);
    };

    let result = dispatch::dispatch(cli.command, ctx, legacy_upload_options).await;

    if let Err(e) = result {
        if e.code() == "INTERRUPTED" {
            if matches!(output_format, OutputFormat::Json) {
                error_ui::print_runtime_error(
                    &command_display,
                    &e,
                    output_format,
                    compact,
                    verbose_errors,
                );
            } else if let Error::Api { message, .. } = &e {
                eprintln!("{message}");
            }
            std::process::exit(130);
        }
        if !matches!(e, Error::AlreadyReported) {
            error_ui::print_runtime_error(
                &command_display,
                &e,
                output_format,
                compact,
                verbose_errors,
            );
        }
        std::process::exit(1);
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
