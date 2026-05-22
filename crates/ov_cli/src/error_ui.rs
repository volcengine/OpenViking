use std::ffi::OsString;

use colored::Colorize;

use crate::error::Error;

const CARD_WIDTH: usize = 72;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ErrorAction {
    command: String,
    description: String,
}

impl ErrorAction {
    pub(crate) fn new(command: impl Into<String>, description: impl Into<String>) -> Self {
        Self {
            command: command.into(),
            description: description.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ErrorReport {
    command: Option<String>,
    usage: Option<String>,
    title: String,
    message: String,
    suggestion: Option<String>,
    detail: Option<String>,
    actions: Vec<ErrorAction>,
}

impl ErrorReport {
    pub(crate) fn new(title: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            command: None,
            usage: None,
            title: title.into(),
            message: message.into(),
            suggestion: None,
            detail: None,
            actions: Vec::new(),
        }
    }

    pub(crate) fn with_command(mut self, command: impl Into<String>) -> Self {
        self.command = Some(command.into());
        self
    }

    pub(crate) fn with_usage(mut self, usage: impl Into<String>) -> Self {
        self.usage = Some(usage.into());
        self
    }

    pub(crate) fn with_suggestion(mut self, suggestion: impl Into<String>) -> Self {
        self.suggestion = Some(suggestion.into());
        self
    }

    pub(crate) fn with_detail(mut self, detail: impl Into<String>) -> Self {
        self.detail = Some(detail.into());
        self
    }

    pub(crate) fn with_actions(mut self, actions: Vec<ErrorAction>) -> Self {
        self.actions = actions;
        self
    }
}

pub(crate) fn print_report(report: &ErrorReport, verbose: bool) {
    eprint!("{}", render_report(report, verbose));
}

pub(crate) fn report_for_clap_error(args: &[OsString], clap_output: &str) -> ErrorReport {
    let command = display_command(args);
    let usage = parse_usage(clap_output);

    if is_setup_cli_command(args) {
        return ErrorReport::new(
            "Command Error",
            "Use ov config to add, edit, or delete configs.",
        )
        .with_command(command)
        .with_optional_usage(usage)
        .with_suggestion("ov config")
        .with_actions(vec![
            ErrorAction::new("ov config", "Add, edit, or delete configs"),
            ErrorAction::new("ov config show", "Show the active config"),
            ErrorAction::new("ov config validate", "Check the active config"),
        ]);
    }

    let unknown = parse_unknown_subcommand(clap_output);
    let suggestion = parse_clap_subcommand_suggestion(clap_output)
        .map(|suggested| qualified_suggestion(args, &suggested));
    let message = unknown
        .map(|value| format!("Unknown command: {value}"))
        .unwrap_or_else(|| first_error_line(clap_output));

    let mut actions = Vec::new();
    if let Some(suggestion) = suggestion.as_ref() {
        actions.push(ErrorAction::new(suggestion, "Run the suggested command"));
    }
    actions.push(ErrorAction::new("ov --help", "Show all commands"));

    let mut report = ErrorReport::new("Command Error", message)
        .with_command(command)
        .with_optional_usage(usage)
        .with_actions(actions);
    if let Some(suggestion) = suggestion {
        report = report.with_suggestion(suggestion);
    }
    report
}

pub(crate) fn report_for_message_error(
    command: impl Into<String>,
    title: impl Into<String>,
    message: impl Into<String>,
    actions: Vec<ErrorAction>,
) -> ErrorReport {
    ErrorReport::new(title, message)
        .with_command(command)
        .with_actions(actions)
}

pub(crate) fn report_for_runtime_error(command: impl Into<String>, error: &Error) -> ErrorReport {
    let command = command.into();
    match error {
        Error::Config(message) => ErrorReport::new("Configuration Error", message)
            .with_command(command)
            .with_actions(vec![
                ErrorAction::new("ov config", "Add or edit a config"),
                ErrorAction::new("ov config show", "Show the active config"),
            ]),
        Error::Network(message) => ErrorReport::new(
            "Connection Error",
            "Could not reach OpenViking. The server may be offline, or this config points to the wrong URL.",
        )
        .with_command(command)
        .with_detail(message)
        .with_actions(vec![
            ErrorAction::new("ov config validate", "Check the active config"),
            ErrorAction::new("ov health", "Run a quick server health check"),
            ErrorAction::new("ov config switch", "Switch to another config"),
        ]),
        Error::Api(message) if looks_like_auth_error(message) => ErrorReport::new(
            "Authentication Error",
            "OpenViking rejected the API key for the active config.",
        )
        .with_command(command)
        .with_detail(message)
        .with_actions(vec![
            ErrorAction::new("ov config", "Edit this config"),
            ErrorAction::new("ov config switch", "Use another config"),
        ]),
        Error::Api(message) => ErrorReport::new(
            "OpenViking API Error",
            "OpenViking returned an error for this request.",
        )
        .with_command(command)
        .with_detail(message)
        .with_actions(vec![
            ErrorAction::new("ov config validate", "Check the active config"),
            ErrorAction::new("ov status", "Check OpenViking status"),
        ]),
        Error::Client(message) => ErrorReport::new("Command Error", message)
            .with_command(command)
            .with_actions(vec![ErrorAction::new("ov --help", "Show all commands")]),
        Error::Parse(message) => ErrorReport::new("Parse Error", message)
            .with_command(command)
            .with_actions(vec![ErrorAction::new("ov --help", "Show all commands")]),
        Error::Output(message) => ErrorReport::new("Output Error", message).with_command(command),
        Error::InvalidPath(message) => ErrorReport::new("Invalid Path", message)
            .with_command(command)
            .with_actions(vec![ErrorAction::new("ov --help", "Show all commands")]),
        Error::Io(error) => ErrorReport::new("IO Error", "OpenViking could not read or write a file.")
            .with_command(command)
            .with_detail(error.to_string()),
        Error::Serialization(error) => {
            ErrorReport::new("Serialization Error", "OpenViking could not parse structured data.")
                .with_command(command)
                .with_detail(error.to_string())
        }
        Error::Zip(error) => ErrorReport::new("Archive Error", "OpenViking could not process the archive.")
            .with_command(command)
            .with_detail(error.to_string()),
        Error::AlreadyReported => ErrorReport::new("Command Error", "The command failed.")
            .with_command(command),
    }
}

pub(crate) fn render_report(report: &ErrorReport, verbose: bool) -> String {
    let mut output = String::new();

    if let Some(command) = report.command.as_deref() {
        output.push_str(&format!("{}\n\n", command.bold()));
    }
    if let Some(usage) = report.usage.as_deref() {
        output.push_str(&format!("{} {}\n", "Usage:".yellow().bold(), usage));
        output.push_str(&format!("{} {}\n\n", "Try:".dimmed(), "ov --help".cyan()));
    }

    output.push_str(&render_card(report, verbose));

    if !report.actions.is_empty() {
        output.push_str("\n\n");
        output.push_str(&format!("{}\n", "Next:".bold()));
        let command_width = report
            .actions
            .iter()
            .map(|action| action.command.chars().count())
            .max()
            .unwrap_or_default();
        for action in &report.actions {
            output.push_str(&format!(
                "  {}{}  {}\n",
                action.command.cyan().bold(),
                " ".repeat(command_width.saturating_sub(action.command.chars().count())),
                action.description.dimmed()
            ));
        }
    } else {
        output.push('\n');
    }

    output
}

trait OptionalUsage {
    fn with_optional_usage(self, usage: Option<String>) -> Self;
}

impl OptionalUsage for ErrorReport {
    fn with_optional_usage(self, usage: Option<String>) -> Self {
        if let Some(usage) = usage {
            self.with_usage(usage)
        } else {
            self
        }
    }
}

fn render_card(report: &ErrorReport, verbose: bool) -> String {
    let inner_width = CARD_WIDTH.saturating_sub(4);
    let title = format!("─ {} ", report.title);
    let fill = CARD_WIDTH.saturating_sub(2 + title.chars().count());
    let mut lines = Vec::new();

    lines.extend(wrap_text(&report.message, inner_width));
    if let Some(suggestion) = report.suggestion.as_deref() {
        lines.push(String::new());
        lines.extend(wrap_text(
            &format!("Did you mean: {suggestion}"),
            inner_width,
        ));
    }
    if verbose {
        if let Some(detail) = report.detail.as_deref() {
            lines.push(String::new());
            lines.extend(wrap_text(&format!("Detail: {detail}"), inner_width));
        }
    }

    let mut rendered = String::new();
    rendered.push_str(&format!(
        "{}{}{}{}\n",
        "╭".red(),
        title.red().bold(),
        "─".repeat(fill).red(),
        "╮".red()
    ));
    for line in lines {
        rendered.push_str(&render_card_line(&line, inner_width));
        rendered.push('\n');
    }
    rendered.push_str(&format!(
        "{}{}{}",
        "╰".red(),
        "─".repeat(CARD_WIDTH.saturating_sub(2)).red(),
        "╯".red()
    ));
    rendered
}

fn render_card_line(line: &str, inner_width: usize) -> String {
    let width = line.chars().count();
    let padding = inner_width.saturating_sub(width);
    format!(
        "{} {}{} {}",
        "│".red(),
        line,
        " ".repeat(padding),
        "│".red()
    )
}

fn wrap_text(text: &str, width: usize) -> Vec<String> {
    if text.is_empty() {
        return vec![String::new()];
    }

    let mut lines = Vec::new();
    let mut current = String::new();

    for word in text.split_whitespace() {
        let separator = if current.is_empty() { 0 } else { 1 };
        if !current.is_empty() && current.chars().count() + separator + word.chars().count() > width
        {
            lines.push(current);
            current = String::new();
        }
        if !current.is_empty() {
            current.push(' ');
        }
        current.push_str(word);
    }

    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

pub(crate) fn display_command(args: &[OsString]) -> String {
    let mut parts = vec!["ov".to_string()];
    parts.extend(
        args.iter()
            .skip(1)
            .map(|arg| arg.to_string_lossy().into_owned()),
    );
    parts.join(" ")
}

fn is_setup_cli_command(args: &[OsString]) -> bool {
    let parts = args
        .iter()
        .skip(1)
        .map(|arg| arg.to_string_lossy())
        .collect::<Vec<_>>();
    parts.as_slice() == ["config", "setup-cli"]
}

fn parse_usage(clap_output: &str) -> Option<String> {
    clap_output
        .lines()
        .find_map(|line| line.trim().strip_prefix("Usage: "))
        .map(ToString::to_string)
}

fn parse_unknown_subcommand(clap_output: &str) -> Option<String> {
    clap_output.lines().find_map(|line| {
        let trimmed = line.trim();
        if trimmed.starts_with("error: unrecognized subcommand ") {
            first_single_quoted(trimmed)
        } else {
            None
        }
    })
}

fn parse_clap_subcommand_suggestion(clap_output: &str) -> Option<String> {
    clap_output.lines().find_map(|line| {
        let trimmed = line.trim();
        if trimmed.contains("similar subcommand") {
            first_single_quoted(trimmed)
        } else {
            None
        }
    })
}

fn first_single_quoted(value: &str) -> Option<String> {
    let start = value.find('\'')?;
    let rest = &value[start + 1..];
    let end = rest.find('\'')?;
    Some(rest[..end].to_string())
}

fn qualified_suggestion(args: &[OsString], suggested_leaf: &str) -> String {
    let mut parts = args
        .iter()
        .skip(1)
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    if parts.is_empty() {
        parts.push(suggested_leaf.to_string());
    } else if let Some(last) = parts.last_mut() {
        *last = suggested_leaf.to_string();
    }

    if parts.is_empty() {
        "ov".to_string()
    } else {
        format!("ov {}", parts.join(" "))
    }
}

fn first_error_line(clap_output: &str) -> String {
    clap_output
        .lines()
        .find_map(|line| line.trim().strip_prefix("error: "))
        .map(ToString::to_string)
        .unwrap_or_else(|| "Command could not be parsed.".to_string())
}

fn looks_like_auth_error(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("auth")
        || lower.contains("api key")
        || lower.contains("unauthorized")
        || lower.contains("forbidden")
}

#[cfg(test)]
mod tests {
    use super::{render_report, report_for_clap_error, report_for_runtime_error};
    use crate::error::Error;
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
    fn command_typo_uses_clap_suggestion() {
        let clap_output = "\
error: unrecognized subcommand 'configure'

  tip: a similar subcommand exists: 'config'

Usage: ov [OPTIONS] <COMMAND>
";
        let report = report_for_clap_error(&os_args(&["ov", "configure"]), clap_output);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("ov configure"));
        assert!(rendered.contains("Usage: ov [OPTIONS] <COMMAND>"));
        assert!(rendered.contains("Unknown command: configure"));
        assert!(rendered.contains("Did you mean: ov config"));
        assert!(rendered.contains("ov --help"));
    }

    #[test]
    fn removed_setup_cli_only_suggests_ov_config() {
        let clap_output = "\
error: unrecognized subcommand 'setup-cli'

Usage: ov config [OPTIONS] [COMMAND]
";
        let report = report_for_clap_error(&os_args(&["ov", "config", "setup-cli"]), clap_output);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("ov config setup-cli"));
        assert!(rendered.contains("Did you mean: ov config"));
        assert!(rendered.contains("ov config"));
        assert!(rendered.contains("ov config show"));
        assert!(!rendered.contains("no longer available"));
        assert!(!rendered.contains("deprecated"));
    }

    #[test]
    fn runtime_api_error_hides_raw_detail_by_default() {
        let error = Error::Api(
            "[AuthenticationError] API key invalid. Request ID: 02177930089909800000000000000000"
                .to_string(),
        );
        let report = report_for_runtime_error("ov status", &error);
        let normal = strip_ansi(&render_report(&report, false));
        let verbose = strip_ansi(&render_report(&report, true));

        assert!(normal.contains("Authentication Error"));
        assert!(normal.contains("OpenViking rejected the API key"));
        assert!(normal.contains("ov config"));
        assert!(!normal.contains("Request ID"));
        assert!(!normal.contains("AuthenticationError"));

        assert!(verbose.contains("Detail:"));
        assert!(verbose.contains("Request ID"));
    }

    #[test]
    fn network_error_suggests_validation_and_health_commands() {
        let error = Error::Network("HTTP request failed: connection refused".to_string());
        let report = report_for_runtime_error("ov status", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Connection Error"));
        assert!(rendered.contains("Could not reach OpenViking"));
        assert!(rendered.contains("ov config validate"));
        assert!(rendered.contains("ov health"));
        assert!(rendered.contains("ov config switch"));
    }
}
