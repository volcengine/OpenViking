use std::ffi::OsString;

use colored::Colorize;
use unicode_width::UnicodeWidthStr;

use crate::{
    error::Error,
    error_classifier::looks_like_auth_error,
    i18n::{Language, copy},
    theme,
};

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
    let language = Language::current();
    let command = display_command(args);
    let usage = parse_usage(clap_output);

    if is_setup_cli_command(args) {
        return ErrorReport::new(
            copy(language, "Command Error", "命令错误"),
            copy(
                language,
                "Use ov config to add, edit, or delete configs.",
                "请使用 ov config 添加、编辑或删除配置。",
            ),
        )
        .with_command(command)
        .with_optional_usage(usage)
        .with_suggestion("ov config")
        .with_actions(vec![
            ErrorAction::new(
                "ov config",
                copy(
                    language,
                    "Add, edit, or delete configs",
                    "添加、编辑或删除配置",
                ),
            ),
            ErrorAction::new(
                "ov config show",
                copy(language, "Show the active config", "显示当前配置"),
            ),
            ErrorAction::new(
                "ov config validate",
                copy(language, "Check the active config", "检查当前配置"),
            ),
        ]);
    }

    let unknown = parse_unknown_subcommand(clap_output);
    let suggestion = parse_clap_subcommand_suggestion(clap_output)
        .map(|suggested| qualified_suggestion(args, &suggested));
    let mut actions = Vec::new();
    if let Some(suggestion) = suggestion.as_ref() {
        actions.push(ErrorAction::new(
            suggestion,
            copy(language, "Run the suggested command", "运行建议的命令"),
        ));
    }
    let help_command = usage
        .as_deref()
        .and_then(help_command_from_usage)
        .unwrap_or_else(|| "ov --help".to_string());
    let help_description = if help_command == "ov --help" {
        copy(language, "Show all commands", "查看所有命令")
    } else {
        copy(language, "Show this command's help", "查看此命令帮助")
    };
    actions.push(ErrorAction::new(help_command, help_description));

    let message = unknown
        .map(|value| match language {
            Language::En => format!("Unknown command: {value}."),
            Language::ZhCn => format!("未知命令：{value}。"),
        })
        .unwrap_or_else(|| first_error_line(clap_output));

    let mut report = ErrorReport::new(copy(language, "Command Error", "命令错误"), message)
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

pub(crate) fn report_for_plain_help_error(
    command: impl Into<String>,
    help_command: impl Into<String>,
) -> ErrorReport {
    let language = Language::current();
    let help_command = help_command.into();
    ErrorReport::new(
        copy(language, "Command Error", "命令错误"),
        copy(
            language,
            "Plain help is not supported for this command. Use prefixed help instead.",
            "此命令不支持 plain help。请改用带前缀的 help。",
        ),
    )
    .with_command(command)
    .with_suggestion(help_command.clone())
    .with_actions(vec![ErrorAction::new(
        help_command,
        copy(language, "Show this command's help", "查看此命令帮助"),
    )])
}

pub(crate) fn report_for_runtime_error(command: impl Into<String>, error: &Error) -> ErrorReport {
    let language = Language::current();
    let command = command.into();
    match error {
        Error::MissingConfig => ErrorReport::new(
            copy(language, "Configuration Error", "配置错误"),
            copy(
                language,
                "No ovcli.conf detected. Run ov config to create one before using server commands.",
                "未检测到 ovcli.conf。请先运行 ov config 创建配置，再使用服务器命令。",
            ),
        )
        .with_command(command)
        .with_actions(vec![ErrorAction::new(
            "ov config",
            copy(language, "Create a config", "创建配置"),
        )]),
        Error::Config(message) => ErrorReport::new(copy(language, "Configuration Error", "配置错误"), message)
            .with_command(command)
            .with_actions(config_error_actions(message, language)),
        Error::Language(message) => ErrorReport::new(copy(language, "Language Error", "语言错误"), message)
            .with_command(command)
            .with_actions(vec![
                ErrorAction::new("ov language en", copy(language, "Use English", "使用英文")),
                ErrorAction::new("ov language zh-CN", copy(language, "Use Simplified Chinese", "使用简体中文")),
            ]),
        Error::Network(message) => ErrorReport::new(
            copy(language, "Connection Error", "连接错误"),
            copy(
                language,
                "Could not reach OpenViking. The server may be offline, or this config points to the wrong URL.",
                "无法连接 OpenViking。服务器可能未启动，或当前配置指向了错误的 URL。",
            ),
        )
        .with_command(command)
        .with_detail(message)
        .with_actions(vec![
            ErrorAction::new("ov config validate", copy(language, "Check the active config", "检查当前配置")),
            ErrorAction::new("ov health", copy(language, "Run a quick server health check", "快速检查服务器健康状态")),
            ErrorAction::new("ov config switch", copy(language, "Switch to another config", "切换到其他配置")),
        ]),
        Error::Api { message, .. } if looks_like_auth_error(message) => ErrorReport::new(
            copy(language, "Authentication Error", "认证错误"),
            copy(language, "OpenViking rejected the API key for the active config.", "OpenViking 拒绝了当前配置的 API Key。"),
        )
        .with_command(command)
        .with_detail(message)
        .with_actions(vec![
            ErrorAction::new("ov config", copy(language, "Edit this config", "编辑这个配置")),
            ErrorAction::new("ov config switch", copy(language, "Use another config", "使用其他配置")),
        ]),
        Error::Api { message, .. } => ErrorReport::new(
            copy(language, "OpenViking API Error", "OpenViking API 错误"),
            api_error_message(language, message),
        )
        .with_command(command)
        .with_detail(message)
        .with_actions(vec![
            ErrorAction::new("ov config validate", copy(language, "Check the active config", "检查当前配置")),
            ErrorAction::new("ov status", copy(language, "Check OpenViking status", "查看 OpenViking 状态")),
        ]),
        Error::Client(message) => ErrorReport::new(
            copy(language, "Command Error", "命令错误"),
            sentence_case_error(message),
        )
            .with_command(command.clone())
            .with_actions(contextual_help_actions(&command, language)),
        Error::Parse(message) => ErrorReport::new(
            copy(language, "Parse Error", "解析错误"),
            sentence_case_error(message),
        )
            .with_command(command.clone())
            .with_actions(contextual_help_actions(&command, language)),
        Error::Output(message) => ErrorReport::new(copy(language, "Output Error", "输出错误"), message).with_command(command),
        Error::InvalidPath(message) => ErrorReport::new(
            copy(language, "Invalid Path", "路径无效"),
            sentence_case_error(message),
        )
            .with_command(command.clone())
            .with_actions(contextual_help_actions(&command, language)),
        Error::Io(error) => ErrorReport::new(copy(language, "IO Error", "IO 错误"), copy(language, "OpenViking could not read or write a file.", "OpenViking 无法读取或写入文件。"))
            .with_command(command)
            .with_detail(error.to_string()),
        Error::Serialization(error) => {
            ErrorReport::new(copy(language, "Serialization Error", "序列化错误"), copy(language, "OpenViking could not parse structured data.", "OpenViking 无法解析结构化数据。"))
                .with_command(command)
                .with_detail(error.to_string())
        }
        Error::Zip(error) => ErrorReport::new(copy(language, "Archive Error", "压缩包错误"), copy(language, "OpenViking could not process the archive.", "OpenViking 无法处理压缩包。"))
            .with_command(command)
            .with_detail(error.to_string()),
        Error::AlreadyReported => ErrorReport::new(copy(language, "Command Error", "命令错误"), copy(language, "The command failed.", "命令执行失败。"))
            .with_command(command),
    }
}

fn config_error_actions(message: &str, language: Language) -> Vec<ErrorAction> {
    if is_config_file_load_error(message) {
        return vec![ErrorAction::new(
            "ov config",
            copy(
                language,
                "Repair or recreate the active config",
                "修复或重新创建当前配置",
            ),
        )];
    }

    vec![
        ErrorAction::new(
            "ov config",
            copy(language, "Add or edit a config", "添加或编辑配置"),
        ),
        ErrorAction::new(
            "ov config show",
            copy(language, "Show the active config", "显示当前配置"),
        ),
    ]
}

fn is_config_file_load_error(message: &str) -> bool {
    message.starts_with("Failed to read config file:")
        || message.starts_with("Failed to parse config file:")
}

fn contextual_help_actions(command: &str, language: Language) -> Vec<ErrorAction> {
    let help_command = contextual_help_command(command).unwrap_or_else(|| "ov --help".to_string());
    let description = if help_command.ends_with(" --help") && help_command != "ov --help" {
        copy(language, "Show this command's help", "查看此命令帮助")
    } else {
        copy(language, "Show all commands", "查看所有命令")
    };
    vec![ErrorAction::new(help_command, description)]
}

fn contextual_help_command(command: &str) -> Option<String> {
    let tokens = command.split_whitespace().collect::<Vec<_>>();
    let program = *tokens.first()?;
    let root = *tokens.get(1)?;

    match root {
        "config" => Some(group_help_command(
            program,
            root,
            tokens.get(2).copied(),
            &["show", "validate", "switch"],
        )),
        "task" => Some(task_help_command(program, &tokens)),
        "admin" => Some(group_help_command(
            program,
            root,
            tokens.get(2).copied(),
            &[
                "create-account",
                "list-accounts",
                "delete-account",
                "register-user",
                "list-users",
                "remove-user",
                "set-role",
                "regenerate-key",
            ],
        )),
        "system" => Some(system_help_command(program, &tokens)),
        "session" => Some(group_help_command(
            program,
            root,
            tokens.get(2).copied(),
            &[
                "new",
                "list",
                "get",
                "get-session-context",
                "get-session-archive",
                "delete",
                "add-message",
                "add-messages",
                "commit",
            ],
        )),
        "privacy" => Some(group_help_command(
            program,
            root,
            tokens.get(2).copied(),
            &[
                "categories",
                "list",
                "get",
                "upsert",
                "versions",
                "version",
                "activate",
            ],
        )),
        "observer" => Some(group_help_command(
            program,
            root,
            tokens.get(2).copied(),
            &[
                "queue",
                "vikingdb",
                "models",
                "transaction",
                "retrieval",
                "filesystem",
                "system",
            ],
        )),
        command if is_contextual_top_level_command(command) => {
            Some(format!("{program} {command} --help"))
        }
        _ => None,
    }
}

fn group_help_command(
    program: &str,
    root: &str,
    leaf: Option<&str>,
    known_leaves: &[&str],
) -> String {
    match leaf {
        Some(leaf) if known_leaves.contains(&leaf) => format!("{program} {root} {leaf} --help"),
        _ => format!("{program} {root} --help"),
    }
}

fn task_help_command(program: &str, tokens: &[&str]) -> String {
    match tokens.get(2).copied() {
        Some("watch") => match tokens.get(3).copied() {
            Some(leaf)
                if ["ls", "show", "rm", "pause", "resume", "update", "trigger"].contains(&leaf) =>
            {
                format!("{program} task watch {leaf} --help")
            }
            _ => format!("{program} task watch --help"),
        },
        Some("status" | "list") => format!("{program} task {} --help", tokens[2]),
        _ => format!("{program} task --help"),
    }
}

fn system_help_command(program: &str, tokens: &[&str]) -> String {
    match tokens.get(2).copied() {
        Some("crypto") => match tokens.get(3).copied() {
            Some("init-key") => format!("{program} system crypto init-key --help"),
            _ => format!("{program} system crypto --help"),
        },
        Some("backend") => match tokens.get(3).copied() {
            Some("sync-status" | "sync-retry") => {
                format!("{program} system backend {} --help", tokens[3])
            }
            _ => format!("{program} system backend --help"),
        },
        Some("wait" | "status" | "health" | "consistency") => {
            format!("{program} system {} --help", tokens[2])
        }
        _ => format!("{program} system --help"),
    }
}

fn is_contextual_top_level_command(command: &str) -> bool {
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
            | "relations"
            | "link"
            | "unlink"
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
            | "language"
    )
}

fn api_error_message(language: Language, raw: &str) -> String {
    let Some(summary) = summarize_api_error(raw) else {
        return copy(
            language,
            "OpenViking returned an error for this request.",
            "OpenViking 返回了请求错误。",
        )
        .to_string();
    };

    match language {
        Language::En => format!("OpenViking returned an error: {summary}"),
        Language::ZhCn => format!("OpenViking 返回了请求错误：{summary}"),
    }
}

fn summarize_api_error(raw: &str) -> Option<String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }

    if let Some(summary) = summarize_json_api_error(raw) {
        return Some(summary);
    }

    let cleaned = strip_request_id(raw);
    let cleaned = cleaned.trim();
    if cleaned.is_empty() {
        return None;
    }

    if let Some((code, message)) = bracketed_error(cleaned) {
        return Some(format_summary(Some(code), message));
    }

    Some(ensure_sentence(
        cleaned.lines().next().unwrap_or(cleaned).trim(),
    ))
}

fn summarize_json_api_error(raw: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(raw).ok()?;
    let error = value.get("error").unwrap_or(&value);
    let code = error
        .get("code")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty());
    let message = error
        .get("message")
        .and_then(serde_json::Value::as_str)
        .or_else(|| error.get("detail").and_then(serde_json::Value::as_str))
        .filter(|value| !value.trim().is_empty())?;
    Some(format_summary(code, message))
}

fn bracketed_error(value: &str) -> Option<(&str, &str)> {
    let rest = value.strip_prefix('[')?;
    let end = rest.find(']')?;
    let code = rest[..end].trim();
    let message = rest[end + 1..].trim();
    if code.is_empty() || message.is_empty() {
        return None;
    }
    Some((code, message))
}

fn format_summary(code: Option<&str>, message: &str) -> String {
    let message = ensure_sentence(strip_request_id(message).trim());
    match code {
        Some(code) => format!("{code}: {message}"),
        None => message,
    }
}

fn strip_request_id(value: &str) -> &str {
    for marker in ["Request ID:", "Request Id:", "request ID:", "request id:"] {
        if let Some(index) = value.find(marker) {
            return value[..index].trim_end();
        }
    }
    value
}

fn ensure_sentence(value: &str) -> String {
    let value = value.trim().trim_end_matches('.').trim();
    if value.is_empty() {
        return "OpenViking returned an error.".to_string();
    }
    format!("{value}.")
}

pub(crate) fn render_report(report: &ErrorReport, verbose: bool) -> String {
    let mut output = String::new();
    let language = Language::current();
    let try_command = try_command_for_report(report);

    if let Some(command) = report.command.as_deref() {
        output.push_str(&format!("{}\n\n", theme::strong(command)));
    }
    if let Some(usage) = report.usage.as_deref() {
        output.push_str(&format!(
            "{} {}\n",
            theme::warning(copy(language, "Usage:", "用法：")).bold(),
            theme::strong(usage)
        ));
        output.push_str(&format!(
            "{} {}\n\n",
            theme::muted(copy(language, "Try:", "可尝试：")),
            theme::command(&try_command)
        ));
    }

    output.push_str(&render_card(report, verbose));

    if !report.actions.is_empty() {
        output.push_str("\n\n");
        output.push_str(&format!(
            "{}\n",
            theme::strong(copy(language, "Next:", "下一步："))
        ));
        let command_width = report
            .actions
            .iter()
            .map(|action| action.command.width())
            .max()
            .unwrap_or_default();
        for action in &report.actions {
            output.push_str(&format!(
                "  {}{}  {}\n",
                theme::command(action.command.clone()).bold(),
                " ".repeat(command_width.saturating_sub(action.command.width())),
                theme::muted(&action.description)
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
    let language = Language::current();
    let inner_width = CARD_WIDTH.saturating_sub(4);
    let title = format!("─ {} ", report.title);
    let fill = CARD_WIDTH.saturating_sub(2 + title.width());
    let mut lines = Vec::new();

    lines.extend(wrap_text(&report.message, inner_width));
    if let Some(suggestion) = report.suggestion.as_deref() {
        lines.push(String::new());
        lines.extend(wrap_text(&did_you_mean(language, suggestion), inner_width));
    }
    if verbose {
        if let Some(detail) = report.detail.as_deref() {
            lines.push(String::new());
            lines.extend(wrap_text(&detail_line(language, detail), inner_width));
        }
    }

    let mut rendered = String::new();
    rendered.push_str(&format!(
        "{}{}{}{}\n",
        theme::error("╭"),
        theme::error(title).bold(),
        theme::error("─".repeat(fill)),
        theme::error("╮")
    ));
    for line in lines {
        rendered.push_str(&render_card_line(&line, inner_width));
        rendered.push('\n');
    }
    rendered.push_str(&format!(
        "{}{}{}",
        theme::error("╰"),
        theme::error("─".repeat(CARD_WIDTH.saturating_sub(2))),
        theme::error("╯")
    ));
    rendered
}

fn did_you_mean(language: Language, suggestion: &str) -> String {
    match language {
        Language::En => format!("Did you mean: {suggestion}"),
        Language::ZhCn => format!("你是不是想运行：{suggestion}"),
    }
}

fn detail_line(language: Language, detail: &str) -> String {
    match language {
        Language::En => format!("Detail: {detail}"),
        Language::ZhCn => format!("详情：{detail}"),
    }
}

fn render_card_line(line: &str, inner_width: usize) -> String {
    let width = line.width();
    let padding = inner_width.saturating_sub(width);
    format!(
        "{} {}{} {}",
        theme::error("│"),
        theme::body(line),
        " ".repeat(padding),
        theme::error("│")
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
        if !current.is_empty() && current.width() + separator + word.width() > width {
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
    if let Some(message) = required_arguments_message(clap_output) {
        return message;
    }
    if let Some(message) = unexpected_argument_message(clap_output) {
        return message;
    }

    let Some(raw) = clap_output
        .lines()
        .find_map(|line| line.trim().strip_prefix("error: "))
    else {
        return "Command could not be parsed.".to_string();
    };

    sentence_case_error(raw)
}

fn required_arguments_message(clap_output: &str) -> Option<String> {
    let mut lines = clap_output.lines().map(str::trim);
    while let Some(line) = lines.next() {
        if line != "error: the following required arguments were not provided:" {
            continue;
        }

        let arguments = lines
            .by_ref()
            .take_while(|line| !line.is_empty())
            .filter(|line| line.starts_with('<') || line.starts_with('['))
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        return Some(match arguments.as_slice() {
            [] => "Required argument missing.".to_string(),
            [argument] => format!("Required argument missing: {argument}."),
            _ => format!("Required arguments missing: {}.", arguments.join(", ")),
        });
    }

    None
}

fn unexpected_argument_message(clap_output: &str) -> Option<String> {
    clap_output.lines().find_map(|line| {
        let error = line.trim().strip_prefix("error: ")?;
        if !error.starts_with("unexpected argument ") {
            return None;
        }
        first_single_quoted(error)
            .map(|argument| format!("Unexpected argument: {argument}."))
            .or_else(|| Some(sentence_case_error(error)))
    })
}

fn sentence_case_error(raw: &str) -> String {
    let raw = raw.trim().trim_end_matches(':').trim();
    if raw.is_empty() {
        return "Command could not be parsed.".to_string();
    }

    let mut chars = raw.chars();
    let Some(first) = chars.next() else {
        return "Command could not be parsed.".to_string();
    };
    let mut message = first.to_uppercase().collect::<String>();
    message.push_str(chars.as_str());
    if !matches!(message.chars().last(), Some('.' | '?' | '!')) {
        message.push('.');
    }
    message
}

fn try_command_for_report(report: &ErrorReport) -> String {
    report
        .usage
        .as_deref()
        .and_then(help_command_from_usage)
        .unwrap_or_else(|| "ov --help".to_string())
}

fn help_command_from_usage(usage: &str) -> Option<String> {
    let mut parts = Vec::new();
    for token in usage.split_whitespace() {
        if token.starts_with('<') || token.starts_with('[') || token == "..." {
            break;
        }
        parts.push(token);
    }
    let Some(program) = parts.first() else {
        return None;
    };
    if !program.starts_with("ov") {
        return None;
    }
    if parts.len() == 1 {
        Some(format!("{program} --help"))
    } else {
        Some(format!("{} --help", parts.join(" ")))
    }
}

#[cfg(test)]
mod tests {
    use super::{
        CARD_WIDTH, ErrorReport, render_report, report_for_clap_error, report_for_runtime_error,
    };
    use crate::error::Error;
    use std::ffi::OsString;
    use unicode_width::UnicodeWidthStr;

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
    fn required_argument_error_is_sentence_case_and_specific() {
        let clap_output = "\
error: the following required arguments were not provided:
  <TASK_ID>

Usage: ov task status <TASK_ID>
";
        let report = report_for_clap_error(&os_args(&["ov", "task", "status"]), clap_output);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Required argument missing: <TASK_ID>."));
        assert!(!rendered.contains("the following required arguments were not provided:"));
        assert!(rendered.contains("Try: ov task status --help"));
        assert!(rendered.contains("ov task status --help"));
    }

    #[test]
    fn preview_binary_usage_points_to_preview_help() {
        let clap_output = "\
error: the following required arguments were not provided:
  <TASK_ID>

Usage: ov-preview task status <TASK_ID>
";
        let report =
            report_for_clap_error(&os_args(&["ov-preview", "task", "status"]), clap_output);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Required argument missing: <TASK_ID>."));
        assert!(rendered.contains("Try: ov-preview task status --help"));
        assert!(rendered.contains("ov-preview task status --help"));
    }

    #[test]
    fn unexpected_argument_error_is_sentence_case_and_specific() {
        let clap_output = "\
error: unexpected argument '--bad' found

Usage: ov task status <TASK_ID>
";
        let report = report_for_clap_error(
            &os_args(&["ov", "task", "status", "abc", "--bad"]),
            clap_output,
        );
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Unexpected argument: --bad."));
        assert!(!rendered.contains("unexpected argument '--bad' found"));
        assert!(rendered.contains("Try: ov task status --help"));
        assert!(rendered.contains("ov task status --help"));
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
        assert!(!rendered.contains("ov config setup-cli to initialize"));
        assert!(!rendered.contains("no longer available"));
        assert!(!rendered.contains("deprecated"));
    }

    #[test]
    fn runtime_api_error_hides_raw_detail_by_default() {
        let error = Error::api(
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
    fn runtime_api_error_shows_sanitized_detail_by_default() {
        let error = Error::api(
            "[InvalidRequest] resource not found. Request ID: 02177930089909800000000000000000"
                .to_string(),
        );
        let report = report_for_runtime_error("ov read viking://missing", &error);
        let normal = strip_ansi(&render_report(&report, false));
        let verbose = strip_ansi(&render_report(&report, true));

        assert!(normal.contains("OpenViking API Error"));
        assert!(normal.contains("InvalidRequest: resource not found."));
        assert!(!normal.contains("Request ID"));
        assert!(!normal.contains("02177930089909800000000000000000"));

        assert!(verbose.contains("Detail:"));
        assert!(verbose.contains("Request ID"));
    }

    #[test]
    fn runtime_api_error_summarizes_json_error_envelope() {
        let error = Error::api(
            r#"{"error":{"code":"PermissionDenied","message":"root key required","request_id":"abc"}}"#
                .to_string(),
        );
        let report = report_for_runtime_error("ov admin list-users --sudo", &error);
        let normal = strip_ansi(&render_report(&report, false));

        assert!(normal.contains("PermissionDenied: root key required."));
        assert!(!normal.contains("request_id"));
        assert!(!normal.contains("abc"));
    }

    #[test]
    fn plain_help_error_points_to_prefixed_help() {
        let report = super::report_for_plain_help_error("ov config help", "ov config --help");
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Command Error"));
        assert!(rendered.contains("Plain help is not supported"));
        assert!(rendered.contains("Did you mean: ov config --help"));
        assert!(rendered.contains("ov config --help"));
    }

    #[test]
    fn missing_config_error_points_to_config_creation() {
        let report = report_for_runtime_error("ov ls", &Error::MissingConfig);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Configuration Error"));
        assert!(rendered.contains("No ovcli.conf detected"));
        assert!(rendered.contains("Run ov config to create one"));
        assert!(rendered.contains("ov config"));
        assert!(rendered.contains("Create a config"));
        assert!(!rendered.contains("OpenViking API Error"));
        assert!(!rendered.contains("ov config validate"));
        assert!(!rendered.contains("ov status"));
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

    #[test]
    fn language_error_points_to_language_commands() {
        let error = Error::Language("Unsupported language 'fr'. Use 'en' or 'zh-CN'.".to_string());
        let report = report_for_runtime_error("ov language fr", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Language Error"));
        assert!(rendered.contains("Unsupported language 'fr'"));
        assert!(rendered.contains("ov language en"));
        assert!(rendered.contains("ov language zh-CN"));
        assert!(!rendered.contains("ov config"));
    }

    #[test]
    fn client_error_uses_command_specific_help_action() {
        let error = Error::Client("Specify exactly one of --content or --from-file.".to_string());
        let report = report_for_runtime_error("ov write viking://resources/a.md", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Command Error"));
        assert!(rendered.contains("ov write --help"));
        assert!(!rendered.contains("ov --help  Show all commands"));
    }

    #[test]
    fn client_error_adds_terminal_punctuation() {
        let error = Error::Client("Specify exactly one of --content or --from-file".to_string());
        let report = report_for_runtime_error("ov write viking://resources/a.md", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Specify exactly one of --content or --from-file."));
    }

    #[test]
    fn parse_error_uses_nested_command_specific_help_action() {
        let error = Error::Parse(
            "minutes must be > 0 (got 0). To pause a watch task, use `ov task watch pause`."
                .to_string(),
        );
        let report = report_for_runtime_error("ov task watch update demo --interval 0", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Parse Error"));
        assert!(rendered.contains("Minutes must be > 0"));
        assert!(!rendered.contains("minutes must be > 0"));
        assert!(rendered.contains("ov task watch update --help"));
        assert!(!rendered.contains("ov --help  Show all commands"));
    }

    #[test]
    fn malformed_config_error_only_suggests_config_repair() {
        let error = Error::Config("Failed to parse config file: key must be a string".to_string());
        let report = report_for_runtime_error("ov ls", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Configuration Error"));
        assert!(rendered.contains("Failed to parse config file"));
        assert!(rendered.contains("ov config"));
        assert!(rendered.contains("Repair or recreate the active config"));
        assert!(!rendered.contains("ov config show"));
    }

    #[test]
    fn chinese_error_card_uses_display_width_for_borders() {
        let report = ErrorReport::new("命令错误", "未知命令：con").with_suggestion("ov config");
        let rendered = strip_ansi(&render_report(&report, false));

        for line in rendered
            .lines()
            .filter(|line| line.starts_with('╭') || line.starts_with('│') || line.starts_with('╰'))
        {
            assert_eq!(line.width(), CARD_WIDTH, "{line}");
        }
    }
}
