use std::ffi::OsString;

use colored::Colorize;
use serde_json::{Map, Value};
use unicode_width::UnicodeWidthStr;

use crate::{
    error::Error,
    i18n::{Language, copy},
    output::OutputFormat,
    terminal_ui::{fit_to_display_width, truncate_to_display_width},
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

pub(crate) fn print_runtime_error(
    command: &str,
    error: &Error,
    format: OutputFormat,
    compact: bool,
    verbose: bool,
) {
    if matches!(format, OutputFormat::Json) {
        eprintln!("{}", render_json_error(error, compact));
    } else {
        print_report(&report_for_runtime_error(command, error), verbose);
    }
}

fn render_json_error(error: &Error, compact: bool) -> String {
    let (message, details, http_status): (String, Option<&Value>, Option<u16>) = match error {
        Error::Api {
            message, details, status, ..
        } => (message.clone(), details.as_ref(), *status),
        _ => (error.to_string(), None, None),
    };

    let mut body = Map::new();
    body.insert("code".to_string(), Value::String(error.code().to_string()));
    body.insert("message".to_string(), Value::String(message));
    // Transparently surface the real backend HTTP status so machine consumers
    // (agents, IDE integrations) can discriminate backend error types instead
    // of guessing from the rendered text. See #3255.
    if let Some(status) = http_status {
        body.insert("http_status".to_string(), Value::Number(status.into()));
    }
    if let Some(details) = details {
        body.insert("details".to_string(), details.clone());
    }
    let envelope = serde_json::json!({"ok": false, "error": body});
    if compact {
        envelope.to_string()
    } else {
        serde_json::to_string_pretty(&envelope).unwrap_or_else(|_| envelope.to_string())
    }
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

// ============ Backend error discrimination (#3255) ============
//
// The CLI used to render several distinct backend conditions (path-lock
// contention, queue backpressure, server-side timeout, real transport
// failure) as a single misleading "Cannot connect to OpenViking" card whose
// next-step hints all pointed at config/URL problems. These helpers classify
// an `Error` into a backend error kind and render each kind with its own
// title, a transparent passthrough of the real code/status/message, and a
// targeted next-step hint. Genuine connection-establishment failures keep the
// legacy "Cannot connect" message so existing parsers/scripts keep working.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BackendErrorKind {
    /// Server queue is overloaded (`RESOURCE_EXHAUSTED` or `DEADLINE_EXCEEDED`
    /// with a "queue" hint). Hint: `ov observer queue`.
    QueueBackpressure,
    /// Another op holds a lock on the target subtree ("path_lock",
    /// "waiting for lock"). Hint: `ov status --verbose`.
    PathLockContention,
    /// Target resource being modified concurrently (`CONFLICT: Resource is
    /// busy`). Hint: wait + retry with backoff.
    ResourceBusy,
    /// Request exceeded its time budget. On a healthy server this is usually
    /// queue/lock pressure rather than a network outage.
    ProcessingTimeout,
    /// Server was reachable but dropped the connection before responding
    /// (connection reset / broken pipe / empty reply). Distinct from "server
    /// is down": the URL is correct, the request just failed mid-flight.
    RequestAborted,
}

/// Build a transparent detail string that surfaces the real backend code,
/// HTTP status, message and structured details so agents/humans can diagnose
/// the actual failure instead of a generic "cannot connect".
fn transparent_detail(error: &Error) -> String {
    let code = error.code();
    match error {
        Error::Api {
            message, details, status, ..
        } => {
            let base = if message.is_empty() {
                status
                    .map(|s| format!("HTTP {s}"))
                    .unwrap_or_else(|| "OpenViking returned an error".to_string())
            } else {
                message.clone()
            };
            let mut out = match status {
                Some(s) if *s >= 400 => format!("[{code}] (HTTP {s}) {base}"),
                _ => format!("[{code}] {base}"),
            };
            if let Some(details) = details {
                if !details.is_null() {
                    out.push_str(" | details: ");
                    out.push_str(&details.to_string());
                }
            }
            out
        }
        Error::Timeout(message) | Error::Network(message) => format!("[{code}] {message}"),
        _ => format!("[{code}] {error}"),
    }
}

/// Classify a structured `Error::Api` into a backend kind. Returns `None` for
/// codes/messages that should keep their existing generic rendering.
fn classify_api_error(error: &Error) -> Option<BackendErrorKind> {
    let (code, message) = match error {
        Error::Api { code, message, .. } => (code.as_deref().unwrap_or(""), message.to_lowercase()),
        _ => return None,
    };

    if message.contains("waiting for lock")
        || message.contains("lock on")
        || message.contains("path_lock")
        || message.contains("lock contention")
        || message.contains("lock timeout")
    {
        return Some(BackendErrorKind::PathLockContention);
    }
    if message.contains("resource is busy") || message.contains("resource busy") {
        return Some(BackendErrorKind::ResourceBusy);
    }
    if code == "RESOURCE_EXHAUSTED" || message.contains("queue") {
        return Some(BackendErrorKind::QueueBackpressure);
    }
    if code == "DEADLINE_EXCEEDED"
        || message.contains("timed out")
        || message.contains("deadline")
    {
        return Some(BackendErrorKind::ProcessingTimeout);
    }
    // Only surface CONFLICT as "resource busy" when the message actually hints
    // at concurrency; a plain "already exists" conflict keeps the generic card.
    if code == "CONFLICT"
        && (message.contains("busy")
            || message.contains("concurrent")
            || message.contains("in progress")
            || message.contains("in-flight")
            || message.contains("lock"))
    {
        return Some(BackendErrorKind::ResourceBusy);
    }
    None
}

/// Render a classified backend error with a specific title, a transparent
/// detail line and targeted next-step actions.
fn backend_error_report(
    command: &str,
    kind: BackendErrorKind,
    detail: String,
    language: Language,
) -> ErrorReport {
    let (title_en, title_zh, message_en, message_zh, actions): (
        &str,
        &str,
        &str,
        &str,
        Vec<ErrorAction>,
    ) = match kind {
        BackendErrorKind::QueueBackpressure => (
            "Processing Timeout (queue busy)",
            "处理超时（队列繁忙）",
            "The server is reachable but busy processing queued work and could not finish this request in time. Wait for the queue to drain, then retry.",
            "服务器可达，但正在处理排队中的任务，未能在此请求内完成。请等待队列排空后重试。",
            vec![
                ErrorAction::new(
                    "ov observer queue",
                    copy(language, "Inspect queue depth and pending work", "查看队列深度与积压任务"),
                ),
                ErrorAction::new(
                    command,
                    copy(language, "Retry once the queue drains", "待队列排空后重试"),
                ),
            ],
        ),
        BackendErrorKind::PathLockContention => (
            "Path Lock Contention",
            "目录锁冲突",
            "Another operation holds a lock on the target subtree. The server is reachable; wait for the other operation to finish or inspect stale locks.",
            "另一操作正持有目标子树的锁。服务器可达，请等待该操作完成或检查残留锁。",
            vec![
                ErrorAction::new(
                    "ov status --verbose",
                    copy(language, "Find stale path locks (grep lock)", "查找残留目录锁（grep lock）"),
                ),
                ErrorAction::new(
                    command,
                    copy(language, "Retry after the lock is released", "锁释放后重试"),
                ),
            ],
        ),
        BackendErrorKind::ResourceBusy => (
            "Resource Busy",
            "资源忙（并发冲突）",
            "The target resource is being modified by another concurrent operation. Wait briefly and retry with backoff.",
            "目标资源正被另一并发操作修改。请稍候并以退避方式重试。",
            vec![
                ErrorAction::new(
                    command,
                    copy(language, "Retry with backoff", "以退避方式重试"),
                ),
                ErrorAction::new(
                    "ov observer queue",
                    copy(language, "Check in-flight work", "查看进行中的任务"),
                ),
            ],
        ),
        BackendErrorKind::ProcessingTimeout => (
            "Processing Timeout",
            "处理超时",
            "The request exceeded its time budget. On a healthy server this usually means queue backpressure or a held lock, not a network outage.",
            "请求超出时间预算。在服务器健康的情况下，通常是队列繁忙或锁未释放，而非网络中断。",
            vec![
                ErrorAction::new(
                    "ov observer queue",
                    copy(language, "Check queue depth", "查看队列深度"),
                ),
                ErrorAction::new(
                    "ov status --verbose",
                    copy(language, "Check for stale locks", "检查残留锁"),
                ),
                ErrorAction::new(
                    "ov config",
                    copy(language, "Increase the request timeout", "提高请求超时时间"),
                ),
            ],
        ),
        BackendErrorKind::RequestAborted => (
            "Request Aborted Mid-Flight",
            "请求中途断开",
            "The server was reachable but dropped the connection before responding — usually a server-side timeout under load, not a wrong URL. Check queue/lock state before assuming an outage.",
            "服务器可达，但在响应前断开了连接——通常是负载下的服务端超时，而非 URL 错误。请先检查队列/锁状态，再判断是否为服务中断。",
            vec![
                ErrorAction::new(
                    "ov health",
                    copy(language, "Confirm the server is still healthy", "确认服务器仍健康"),
                ),
                ErrorAction::new(
                    "ov observer queue",
                    copy(language, "Check queue/lock pressure", "查看队列/锁压力"),
                ),
                ErrorAction::new(
                    command,
                    copy(language, "Retry after a brief wait", "稍候重试"),
                ),
            ],
        ),
    };

    ErrorReport::new(copy(language, title_en, title_zh), copy(language, message_en, message_zh))
        .with_command(command.to_string())
        .with_detail(detail)
        .with_actions(actions)
}

/// Legacy "cannot connect" card for genuine connection-establishment
/// failures. Kept verbatim for backward compatibility (scripts that grep for
/// the old message still match real outages).
fn legacy_connection_report(command: &str, error: &Error, language: Language) -> ErrorReport {
    let detail = match error {
        Error::Network(message) => message.to_string(),
        _ => error.to_string(),
    };
    ErrorReport::new(
        copy(language, "Connection Error", "连接错误"),
        copy(
            language,
            "Could not reach OpenViking. The server may be offline, or this config points to the wrong URL.",
            "无法连接 OpenViking。服务器可能未启动，或当前配置指向了错误的 URL。",
        ),
    )
    .with_command(command.to_string())
    .with_detail(detail)
    .with_actions(vec![
        ErrorAction::new(
            "ov config validate",
            copy(language, "Check the active config", "检查当前配置"),
        ),
        ErrorAction::new(
            "ov health",
            copy(language, "Run a quick server health check", "快速检查服务器健康状态"),
        ),
        ErrorAction::new(
            "ov config switch",
            copy(language, "Switch to another config", "切换到其他配置"),
        ),
    ])
}

fn timeout_report(command: &str, error: &Error, language: Language) -> ErrorReport {
    let message = match error {
        Error::Timeout(message) | Error::Network(message) => message.to_lowercase(),
        _ => String::new(),
    };
    let kind = if message.contains("waiting for lock")
        || message.contains("lock on")
        || message.contains("path_lock")
    {
        BackendErrorKind::PathLockContention
    } else if message.contains("queue") {
        BackendErrorKind::QueueBackpressure
    } else {
        BackendErrorKind::ProcessingTimeout
    };
    backend_error_report(command, kind, transparent_detail(error), language)
}

fn network_report(command: &str, error: &Error, language: Language) -> ErrorReport {
    let message = match error {
        Error::Network(message) => message,
        _ => return legacy_connection_report(command, error, language),
    };
    let lower = message.to_lowercase();

    // Genuine connection-establishment failures keep the legacy card.
    let is_connect_failure = lower.contains("refused")
        || lower.contains("resolve")
        || lower.contains("dns")
        || lower.contains("not known")
        || lower.contains("no such host")
        || lower.contains("network is unreachable")
        || lower.contains("connect error")
        || lower.contains("unreachable");

    // Mid-flight aborts: server was reached but dropped/timed out the request.
    // These are reclassified away from "cannot connect" because the URL is
    // correct and the failure is usually server-side load/timeout.
    let is_midflight_abort = lower.contains("reset")
        || lower.contains("broken pipe")
        || lower.contains("connection closed")
        || lower.contains("empty reply")
        || lower.contains("timed out")
        || lower.contains("timeout");

    if !is_connect_failure && is_midflight_abort {
        let kind = if lower.contains("lock") {
            BackendErrorKind::PathLockContention
        } else if lower.contains("queue") {
            BackendErrorKind::QueueBackpressure
        } else {
            BackendErrorKind::RequestAborted
        };
        return backend_error_report(command, kind, transparent_detail(error), language);
    }

    legacy_connection_report(command, error, language)
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
        Error::Timeout(_) => timeout_report(&command, error, language),
        Error::Network(_) => network_report(&command, error, language),
        Error::Api {
            message, details, ..
        } if error.code() == "REFRESH_FAILED" => {
            let retry_command = command.clone();
            let mut report = ErrorReport::new(
                copy(language, "Compile Refresh Failed", "Compile 刷新失败"),
                api_error_message(error.code(), message),
            )
            .with_command(command)
            .with_actions(vec![
                ErrorAction::new(
                    retry_command,
                    copy(
                        language,
                        "Retry safely; matching files stay unchanged and refresh runs again",
                        "安全重试；相同内容不会重复写入，并会重新执行刷新",
                    ),
                ),
                ErrorAction::new(
                    "ov status",
                    copy(
                        language,
                        "Check OpenViking service status",
                        "检查 OpenViking 服务状态",
                    ),
                ),
            ]);
            if let Some(details) = details {
                report = report.with_detail(details.to_string());
            }
            report
        }
        Error::Api { message, .. } if error.code() == "UNAUTHENTICATED" => ErrorReport::new(
            copy(language, "Authentication Error", "认证错误"),
            api_error_message(error.code(), message),
        )
        .with_command(command)
        .with_actions(vec![
            ErrorAction::new("ov config", copy(language, "Edit this config", "编辑这个配置")),
            ErrorAction::new("ov config switch", copy(language, "Use another config", "使用其他配置")),
        ]),
        Error::Api { message, details, .. } => {
            if let Some(kind) = classify_api_error(error) {
                backend_error_report(&command, kind, transparent_detail(error), language)
            } else {
                let mut report = ErrorReport::new(
                    copy(language, "OpenViking API Error", "OpenViking API 错误"),
                    api_error_message(error.code(), message),
                )
                .with_command(command);
                if let Some(details) = details {
                    report = report.with_detail(details.to_string());
                }
                report
            }
        }
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
                "migrate",
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
        Some("status" | "cancel" | "list") => format!("{program} task {} --help", tokens[2]),
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

fn api_error_message(code: &str, message: &str) -> String {
    if message.is_empty() {
        format!("[{code}] OpenViking returned an error")
    } else {
        format!("[{code}] {message}")
    }
}

pub(crate) fn render_report(report: &ErrorReport, verbose: bool) -> String {
    render_report_with_width(report, verbose, error_output_width())
}

fn render_report_with_width(report: &ErrorReport, verbose: bool, width: usize) -> String {
    let mut output = String::new();
    let language = Language::current();
    let try_command = try_command_for_report(report);

    if let Some(command) = report.command.as_deref() {
        if width >= CARD_WIDTH {
            output.push_str(&format!("{}\n\n", theme::strong(command)));
        } else {
            output.push_str(&format!(
                "{}\n\n",
                theme::strong(truncate_to_display_width(command, width))
            ));
        }
    }
    if let Some(usage) = report.usage.as_deref() {
        if width >= CARD_WIDTH {
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
        } else {
            output.push_str(&format!(
                "{}\n",
                theme::warning(truncate_to_display_width(
                    &format!("{} {usage}", copy(language, "Usage:", "用法：")),
                    width
                ))
                .bold()
            ));
            output.push_str(&format!(
                "{}\n\n",
                theme::muted(truncate_to_display_width(
                    &format!("{} {try_command}", copy(language, "Try:", "可尝试：")),
                    width
                ))
            ));
        }
    }

    output.push_str(&render_card(report, verbose, width));

    if !report.actions.is_empty() {
        output.push_str("\n\n");
        output.push_str(&format!(
            "{}\n",
            theme::strong(copy(language, "Next:", "下一步："))
        ));
        let command_width = action_command_width(&report.actions, width);
        for action in &report.actions {
            output.push_str(&render_action_line(action, command_width, width));
            output.push('\n');
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

fn render_card(report: &ErrorReport, verbose: bool, width: usize) -> String {
    let language = Language::current();
    let inner_width = width.saturating_sub(4).max(1);
    let title = format!("─ {} ", report.title);
    let title = truncate_to_display_width(&title, width.saturating_sub(2));
    let fill = width.saturating_sub(2 + title.width());
    let mut lines = Vec::new();

    lines.extend(wrap_text(&report.message, inner_width));
    if let Some(suggestion) = report.suggestion.as_deref() {
        lines.push(String::new());
        lines.extend(wrap_text(&did_you_mean(language, suggestion), inner_width));
    }
    if verbose && let Some(detail) = report.detail.as_deref() {
        lines.push(String::new());
        lines.extend(wrap_text(&detail_line(language, detail), inner_width));
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
        theme::error("─".repeat(width.saturating_sub(2))),
        theme::error("╯")
    ));
    rendered
}

#[cfg(not(test))]
fn error_output_width() -> usize {
    crossterm::terminal::size()
        .map(|(columns, _)| crate::terminal_ui::terminal_width(columns as usize, CARD_WIDTH))
        .unwrap_or(CARD_WIDTH)
}

#[cfg(test)]
fn error_output_width() -> usize {
    CARD_WIDTH
}

fn action_command_width(actions: &[ErrorAction], width: usize) -> usize {
    let available = width.saturating_sub(4);
    let preferred = actions
        .iter()
        .map(|action| action.command.width())
        .max()
        .unwrap_or_default();

    preferred.min(available / 2).max(1)
}

fn render_action_line(action: &ErrorAction, command_width: usize, width: usize) -> String {
    if width <= 4 {
        return truncate_to_display_width(&action.command, width);
    }

    let description_width = width.saturating_sub(command_width + 4);
    let command = fit_to_display_width(&action.command, command_width);
    let description = truncate_to_display_width(&action.description, description_width);

    format!(
        "  {}  {}",
        theme::command(command).bold(),
        theme::muted(description)
    )
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
        if current.is_empty() && word.width() > width {
            lines.push(word.to_string());
            continue;
        }

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
    let program = parts.first()?;
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
        CARD_WIDTH, ErrorAction, ErrorReport, render_json_error, render_report,
        render_report_with_width, report_for_clap_error, report_for_runtime_error,
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
    fn structured_api_errors_are_not_reclassified_from_message_text() {
        let error = Error::api_response(
            Some("FAILED_PRECONDITION".to_string()),
            "Apply permission at https://open.feishu.cn/app/auth?token=abc",
            Some(serde_json::json!({"feishu_code": 99991672})),
            412,
        );

        let report = report_for_runtime_error("ov add-resource", &error);
        let rendered = strip_ansi(&render_report(&report, false));
        assert!(rendered.contains("OpenViking API Error"));
        assert!(rendered.contains("[FAILED_PRECONDITION]"));
        assert!(!rendered.contains("Authentication Error"));

        let json: serde_json::Value =
            serde_json::from_str(&render_json_error(&error, true)).unwrap();
        assert_eq!(json["error"]["code"], "FAILED_PRECONDITION");
        assert_eq!(json["error"]["details"]["feishu_code"], 99991672);
    }

    #[test]
    fn compile_refresh_failure_explains_safe_retry() {
        let command = "ov compile --from viking://resources/source --to viking://resources/wiki --skill viking://agent/skills/wiki --wait";
        let error = Error::api_response(
            Some("REFRESH_FAILED".to_string()),
            "Content is already at the requested state, but semantic/index refresh failed: injected overview failure. Re-run the same batch-write or ov compile command; matching files will remain unchanged and refresh will be retried.",
            Some(serde_json::json!({"root_uri": "viking://resources/wiki"})),
            500,
        );
        let report = report_for_runtime_error(command, &error);
        let normal = strip_ansi(&render_report(&report, false));

        assert!(normal.contains("Compile Refresh Failed"));
        assert!(normal.contains("injected"));
        assert!(normal.contains("overview"));
        assert!(normal.contains("matching files"));
        assert!(normal.contains("unchanged"));
        assert!(normal.contains(command));
        assert!(!normal.contains("ov config validate"));
    }

    #[test]
    fn local_runtime_json_error_always_has_code() {
        let error = Error::Config("Failed to parse config file".to_string());
        let json: serde_json::Value =
            serde_json::from_str(&render_json_error(&error, true)).unwrap();

        assert_eq!(json["error"]["code"], "FAILED_PRECONDITION");
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

    #[test]
    fn error_report_respects_narrow_terminal_width() {
        let report = ErrorReport::new(
            "Command Error",
            "Plain help is not supported for this command. Use prefixed help instead.",
        )
        .with_command("ov very-long-command-name with many extra positional values")
        .with_usage("ov very-long-command-name --with-a-long-option <long-value>")
        .with_suggestion("ov --help")
        .with_actions(vec![ErrorAction::new(
            "ov --help",
            "Show this command's help",
        )]);
        let width = 32;
        let rendered = strip_ansi(&render_report_with_width(&report, false, width));

        for line in rendered.lines() {
            assert!(
                line.width() <= width,
                "line exceeded narrow width: {line:?}"
            );
        }
    }

    // ---- Backend error discrimination (#3255) ----

    fn api_error(code: &str, message: &str, status: u16) -> Error {
        Error::api_response(
            Some(code.to_string()),
            message.to_string(),
            None,
            status,
        )
    }

    #[test]
    fn classify_api_error_maps_known_backend_conditions() {
        use super::{classify_api_error, BackendErrorKind};

        let queue = api_error(
            "DEADLINE_EXCEEDED",
            "Queue processing timed out after 30.0s",
            504,
        );
        assert_eq!(
            classify_api_error(&queue),
            Some(BackendErrorKind::QueueBackpressure)
        );

        let lock = api_error(
            "ABORTED",
            "[TREE] Timeout waiting for lock on: /local/default/user/hermes/memories/tools",
            409,
        );
        assert_eq!(
            classify_api_error(&lock),
            Some(BackendErrorKind::PathLockContention)
        );

        let busy = api_error(
            "CONFLICT",
            "Resource is busy: viking://resources/myproj/newfile.md",
            409,
        );
        assert_eq!(
            classify_api_error(&busy),
            Some(BackendErrorKind::ResourceBusy)
        );

        let exhausted = api_error("RESOURCE_EXHAUSTED", "queue full", 429);
        assert_eq!(
            classify_api_error(&exhausted),
            Some(BackendErrorKind::QueueBackpressure)
        );

        // A plain duplicate-resource conflict must NOT be misclassified as
        // "resource busy" — it keeps the generic API card.
        let plain_conflict = api_error("CONFLICT", "Resource already exists", 409);
        assert_eq!(classify_api_error(&plain_conflict), None);

        // Unrelated structured errors are not reclassified.
        let precondition =
            api_error("FAILED_PRECONDITION", "Apply permission at https://example/auth", 412);
        assert_eq!(classify_api_error(&precondition), None);
    }

    #[test]
    fn deadline_exceeded_queue_timeout_is_not_connection_error() {
        let error = api_error(
            "DEADLINE_EXCEEDED",
            "Queue processing timed out after 30.0s",
            504,
        );
        let report = report_for_runtime_error("ov add-resource", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Processing Timeout (queue busy)"));
        assert!(rendered.contains("ov observer queue"));
        // Must not collapse to the misleading connection message.
        assert!(!rendered.contains("Connection Error"));
        assert!(!rendered.contains("Could not reach OpenViking"));
        assert!(!rendered.contains("ov config validate"));
    }

    #[test]
    fn path_lock_contention_is_distinct_from_connection_error() {
        let error = api_error(
            "ABORTED",
            "Timeout waiting for lock on: /local/default/user/hermes/memories/tools",
            409,
        );
        let report = report_for_runtime_error("ov rm", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Path Lock Contention"));
        assert!(rendered.contains("ov status --verbose"));
        assert!(rendered.contains("grep lock"));
        assert!(!rendered.contains("Could not reach OpenViking"));
        assert!(!rendered.contains("ov config switch"));
    }

    #[test]
    fn conflict_resource_busy_is_distinct_from_connection_error() {
        let error = api_error(
            "CONFLICT",
            "Resource is busy: viking://resources/myproj/newfile.md",
            409,
        );
        let report = report_for_runtime_error("ov add-resource", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Resource Busy"));
        assert!(rendered.contains("backoff"));
        assert!(!rendered.contains("Could not reach OpenViking"));
    }

    #[test]
    fn three_backend_errors_render_distinct_titles() {
        // Verification signal from #3255: the three reproducers must produce
        // three DIFFERENT error titles instead of one generic connection card.
        let queue = report_for_runtime_error(
            "ov add-resource",
            &api_error(
                "DEADLINE_EXCEEDED",
                "Queue processing timed out after 30.0s",
                504,
            ),
        );
        let lock = report_for_runtime_error(
            "ov rm",
            &api_error(
                "ABORTED",
                "Timeout waiting for lock on: /local/default/x",
                409,
            ),
        );
        let busy = report_for_runtime_error(
            "ov add-resource",
            &api_error(
                "CONFLICT",
                "Resource is busy: viking://resources/myproj/x",
                409,
            ),
        );

        let queue_text = strip_ansi(&render_report(&queue, false));
        let lock_text = strip_ansi(&render_report(&lock, false));
        let busy_text = strip_ansi(&render_report(&busy, false));

        // Each rendering mentions its own distinct title.
        assert!(queue_text.contains("Processing Timeout (queue busy)"));
        assert!(lock_text.contains("Path Lock Contention"));
        assert!(busy_text.contains("Resource Busy"));
        // None of them collapse to the generic connection card.
        for text in [queue_text, lock_text, busy_text] {
            assert!(!text.contains("Connection Error"));
            assert!(!text.contains("Could not reach OpenViking"));
        }
    }

    #[test]
    fn backend_error_passthrough_detail_surfaces_real_code_status_and_message() {
        use super::transparent_detail;
        let error = api_error(
            "DEADLINE_EXCEEDED",
            "Queue processing timed out after 30.0s",
            504,
        );

        // The transparent detail helper surfaces real code + HTTP status +
        // message so agents can diagnose the actual backend failure.
        let detail = transparent_detail(&error);
        assert!(detail.contains("[DEADLINE_EXCEEDED]"));
        assert!(detail.contains("(HTTP 504)"));
        assert!(detail.contains("Queue processing timed out after 30.0s"));

        // And the detail is rendered in verbose mode. Strip card borders and
        // padding (which are not whitespace) before substring-checking a phrase
        // that may wrap across lines.
        let report = report_for_runtime_error("ov add-resource", &error);
        let rendered = strip_ansi(&render_report(&report, true));
        let stripped: String = rendered
            .chars()
            .filter(|c| !matches!(c, '│' | '╭' | '╮' | '╰' | '╯' | '─'))
            .collect();
        let flat: String = stripped.split_whitespace().collect::<Vec<_>>().join(" ");
        assert!(flat.contains("[DEADLINE_EXCEEDED] (HTTP 504) Queue processing timed out after 30.0s"));
    }

    #[test]
    fn genuine_connection_refused_keeps_legacy_message() {
        // Backward compatibility: a real transport failure still renders the
        // original "cannot connect" card with config-oriented hints.
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
    fn midflight_connection_reset_is_not_painted_as_outage() {
        // A connection reset after the request was sent means the server was
        // reachable (URL is correct); it should not say "cannot connect".
        let error =
            Error::Network("HTTP request failed: connection reset by peer".to_string());
        let report = report_for_runtime_error("ov add-resource", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(!rendered.contains("Could not reach OpenViking"));
        assert!(rendered.contains("Request Aborted Mid-Flight"));
        assert!(rendered.contains("ov health"));
        assert!(rendered.contains("ov observer queue"));
    }

    #[test]
    fn client_timeout_points_at_queue_and_lock_not_just_config() {
        let error = Error::Timeout("add-resource: request timed out".to_string());
        let report = report_for_runtime_error("ov add-resource", &error);
        let rendered = strip_ansi(&render_report(&report, false));

        assert!(rendered.contains("Processing Timeout"));
        assert!(rendered.contains("ov observer queue"));
        assert!(rendered.contains("ov status --verbose"));
        // Still offers the "raise timeout" escape hatch for legit short budgets.
        assert!(rendered.contains("ov config"));
        assert!(!rendered.contains("Could not reach OpenViking"));
    }

    #[test]
    fn json_error_envelope_includes_real_http_status() {
        let error = api_error(
            "DEADLINE_EXCEEDED",
            "Queue processing timed out after 30.0s",
            504,
        );
        let json: serde_json::Value =
            serde_json::from_str(&render_json_error(&error, true)).unwrap();

        assert_eq!(json["error"]["code"], "DEADLINE_EXCEEDED");
        assert_eq!(json["error"]["http_status"], 504);
        assert_eq!(json["error"]["message"], "Queue processing timed out after 30.0s");
    }
}
