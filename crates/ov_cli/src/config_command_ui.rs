use colored::Colorize;
use unicode_width::UnicodeWidthStr;

use crate::{
    config::{Config, display_config_home},
    config_wizard::ConfigKind,
    error::Error,
    error_classifier::{ApiErrorKind, api_error_kind},
    i18n::{Language, copy},
    theme,
};

const LABEL_WIDTH: usize = 14;
const ACTION_WIDTH: usize = 26;
const NAME_WIDTH: usize = 17;
const KIND_WIDTH: usize = 18;

#[derive(Debug, Clone)]
pub(crate) struct SwitchConfigRow {
    pub name: String,
    pub kind: ConfigKind,
    pub is_active: bool,
}

pub(crate) fn render_validate_success(config: &Config, active_name: Option<&str>) -> String {
    render_validate_success_with_language(config, active_name, Language::current())
}

pub(crate) fn render_validate_success_with_language(
    config: &Config,
    active_name: Option<&str>,
    language: Language,
) -> String {
    let mut lines = Vec::new();
    let kind = kind_label(ConfigKind::from_config(config), language);
    let active = active_name.unwrap_or_else(|| unknown(language));

    lines.push(title(copy(
        language,
        "OPENVIKING CONFIG CHECK",
        "OPENVIKING 配置检查",
    )));
    lines.push(String::new());
    lines.push(section(copy(language, "Config", "配置")));
    lines.push(detail_line(
        copy(language, "Active", "当前配置"),
        active_value(active, kind),
    ));
    lines.push(detail_line(
        copy(language, "Server", "服务器"),
        path_value(&config.url),
    ));
    lines.push(detail_line(
        copy(language, "Config home", "配置目录"),
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section(copy(language, "Checks", "检查项")));
    lines.push(detail_line(
        copy(language, "Config file", "配置文件"),
        ok_value(copy(language, "valid", "有效")),
    ));
    lines.push(detail_line(
        copy(language, "Server", "服务器"),
        ok_value(copy(language, "reachable", "可连接")),
    ));
    lines.push(detail_line(
        copy(language, "Auth", "认证"),
        ok_value(copy(language, "accepted", "已通过")),
    ));
    lines.push(detail_line(
        copy(language, "Health", "健康状态"),
        ok_value(copy(language, "healthy", "健康")),
    ));
    lines.push(String::new());
    lines.push(section(copy(language, "Next", "下一步")));
    lines.push(action_line(
        "ov status",
        copy(language, "Full system diagnostics", "查看完整系统诊断"),
    ));
    lines.push(action_line(
        "ov config switch",
        copy(language, "Use another config", "切换到其他配置"),
    ));
    lines.push(action_line(
        "ov config",
        copy(
            language,
            "Add, edit, or delete configs",
            "添加、编辑或删除配置",
        ),
    ));

    format!("{}\n", lines.join("\n"))
}

pub(crate) fn render_validate_failure(
    config: &Config,
    active_name: Option<&str>,
    error: &Error,
) -> String {
    render_validate_failure_with_language(config, active_name, error, Language::current())
}

pub(crate) fn render_validate_failure_with_language(
    config: &Config,
    active_name: Option<&str>,
    error: &Error,
    language: Language,
) -> String {
    let mut lines = Vec::new();
    let kind = kind_label(ConfigKind::from_config(config), language);
    let active = active_name.unwrap_or_else(|| unknown(language));
    let classification = ValidationFailureKind::from_error(error);

    lines.push(title(copy(
        language,
        "OPENVIKING CONFIG CHECK",
        "OPENVIKING 配置检查",
    )));
    lines.push(String::new());
    lines.push(section(copy(language, "Config", "配置")));
    lines.push(detail_line(
        copy(language, "Active", "当前配置"),
        active_value(active, kind),
    ));
    lines.push(detail_line(
        copy(language, "Server", "服务器"),
        path_value(&config.url),
    ));
    lines.push(detail_line(
        copy(language, "Config home", "配置目录"),
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section(copy(language, "Checks", "检查项")));
    lines.push(detail_line(
        copy(language, "Config file", "配置文件"),
        ok_value(copy(language, "valid", "有效")),
    ));
    lines.push(detail_line(
        copy(language, "Server", "服务器"),
        classification.server_check(language),
    ));
    lines.push(detail_line(
        copy(language, "Auth", "认证"),
        classification.auth_check(language),
    ));
    lines.push(detail_line(
        copy(language, "Health", "健康状态"),
        classification.health_check(language),
    ));
    lines.push(String::new());
    lines.push(section(copy(language, "Issue", "问题")));
    lines.push(format!(
        "  {}",
        theme::error(classification.message(language))
    ));
    lines.push(String::new());
    lines.push(section(copy(language, "Try", "可以尝试")));
    lines.push(action_line(
        "ov health",
        copy(language, "Quick server probe", "快速检查服务器"),
    ));
    lines.push(action_line(
        "ov config",
        copy(language, "Edit this config", "编辑当前配置"),
    ));
    lines.push(action_line(
        "ov config switch",
        copy(language, "Use another config", "切换到其他配置"),
    ));

    format!("{}\n", lines.join("\n"))
}

pub(crate) fn render_switch_header(
    active_name: Option<&str>,
    active_kind: Option<ConfigKind>,
    invalid_config_names: &[String],
) -> String {
    let language = Language::current();
    let mut lines = Vec::new();
    lines.push(title(copy(
        language,
        "OPENVIKING CONFIG SWITCH",
        "OPENVIKING 配置切换",
    )));
    lines.push(String::new());
    match (active_name, active_kind) {
        (Some(name), Some(kind)) => lines.push(format!(
            "{} {}",
            theme::muted(copy(language, "Active:", "当前配置：")),
            active_value(name, kind_label(kind, language))
        )),
        _ => lines.push(format!(
            "{} {}",
            theme::muted(copy(language, "Active:", "当前配置：")),
            unknown_value(copy(language, "none", "无"))
        )),
    }
    if let Some(warning) = invalid_saved_configs_notice(language, invalid_config_names) {
        lines.push(String::new());
        lines.push(warning);
    }
    format!("{}\n", lines.join("\n"))
}

pub(crate) fn switch_labels(rows: &[SwitchConfigRow]) -> Vec<String> {
    let language = Language::current();
    rows.iter()
        .map(|row| {
            let name = theme::command(pad_to_display_width(&row.name, NAME_WIDTH)).bold();
            let kind = theme::strong(pad_to_display_width(
                kind_label(row.kind, language),
                KIND_WIDTH,
            ));
            if row.is_active {
                format!(
                    "{name}{kind}{}",
                    theme::error(copy(language, "[Active]", "[当前]")).bold()
                )
            } else {
                format!("{name}{kind}")
            }
        })
        .collect()
}

pub(crate) fn render_no_saved_configs(invalid_config_names: &[String]) -> String {
    let language = Language::current();
    let mut lines = Vec::new();
    lines.push(title(copy(
        language,
        "OPENVIKING CONFIG SWITCH",
        "OPENVIKING 配置切换",
    )));
    lines.push(String::new());
    lines.push(section(copy(
        language,
        "No saved configs",
        "没有已保存配置",
    )));
    lines.push(format!(
        "  {}",
        theme::muted(copy(
            language,
            "Run ov config to add and save a config first.",
            "请先运行 ov config 添加并保存配置。",
        ))
    ));
    if let Some(warning) = invalid_saved_configs_notice(language, invalid_config_names) {
        lines.push(String::new());
        lines.push(warning);
    }
    format!("{}\n", lines.join("\n"))
}

pub(crate) fn render_switch_success(name: &str) -> String {
    let language = Language::current();
    format!(
        "{} {}\n{}\n",
        theme::success("✓").bold(),
        theme::success(copy_switch_success(language, name)).bold(),
        format!(
            "  {}",
            theme::muted(copy(
                language,
                "Run ov status to inspect it.",
                "运行 ov status 查看状态。"
            ))
        )
    )
}

pub(crate) fn render_switch_validation_failure(name: &str, error: &Error) -> String {
    let language = Language::current();
    let classification = ValidationFailureKind::from_error(error);
    format!(
        "{}\n\n{}\n  {}\n  {}\n\n{}",
        title(copy(
            language,
            "OPENVIKING CONFIG SWITCH",
            "OPENVIKING 配置切换"
        )),
        section(copy(language, "Issue", "问题")),
        theme::error(copy_target_validation_failed(language, name)),
        theme::muted(classification.message(language)),
        action_line(
            "ov config",
            copy(language, "Edit this config", "编辑这个配置")
        )
    )
}

fn unknown(language: Language) -> &'static str {
    copy(language, "unknown", "未知")
}

fn kind_label(kind: ConfigKind, language: Language) -> &'static str {
    match language {
        Language::En => kind.compact_label(),
        Language::ZhCn => match kind {
            ConfigKind::OpenVikingService => "OpenViking 服务",
            ConfigKind::Custom => "自定义",
        },
    }
}

fn copy_switch_success(language: Language, name: &str) -> String {
    match language {
        Language::En => format!("Switched active config to '{name}'."),
        Language::ZhCn => format!("已切换当前配置为 '{name}'。"),
    }
}

fn copy_target_validation_failed(language: Language, name: &str) -> String {
    match language {
        Language::En => format!("Target config '{name}' failed validation."),
        Language::ZhCn => format!("目标配置 '{name}' 验证失败。"),
    }
}

fn invalid_saved_configs_notice(
    language: Language,
    invalid_config_names: &[String],
) -> Option<String> {
    if invalid_config_names.is_empty() {
        return None;
    }

    let names = invalid_config_names.join(", ");
    Some(match language {
        Language::En => format!(
            "  {}",
            theme::warning(format!(
                "Note: Saved configs with damaged JSON structure were skipped: {names}."
            ))
            .bold()
        ),
        Language::ZhCn => format!(
            "  {}",
            theme::warning(format!(
                "提示：以下已保存配置因 JSON 结构损坏已跳过：{names}。"
            ))
            .bold()
        ),
    })
}

fn title(value: &str) -> String {
    theme::brand_title(value).bold().to_string()
}

fn section(value: &str) -> String {
    theme::heading(value).bold().to_string()
}

fn detail_line(label: &str, value: String) -> String {
    let label = theme::muted(pad_to_display_width(label, LABEL_WIDTH));
    format!("  {label}{value}")
}

fn action_line(command: &str, description: &str) -> String {
    let command = theme::command(pad_to_display_width(command, ACTION_WIDTH)).bold();
    format!("  {command}{}", theme::muted(description))
}

fn pad_to_display_width(value: &str, width: usize) -> String {
    format!(
        "{}{}",
        value,
        " ".repeat(width.saturating_sub(UnicodeWidthStr::width(value)))
    )
}

fn active_value(name: &str, kind: &str) -> String {
    format!(
        "{} {}",
        theme::config_name(name).bold(),
        theme::strong(format!("({kind})"))
    )
}

fn path_value(value: &str) -> String {
    theme::value(value).to_string()
}

fn ok_value(value: &str) -> String {
    theme::success(value).bold().to_string()
}

fn warn_value(value: &str) -> String {
    theme::warning(value).bold().to_string()
}

fn fail_value(value: &str) -> String {
    theme::error(value).bold().to_string()
}

fn unknown_value(value: &str) -> String {
    theme::muted(value).to_string()
}

#[derive(Debug, Clone, Copy)]
enum ValidationFailureKind {
    Network,
    Auth,
    Permission,
    Timeout,
    Response,
    Unhealthy,
    Other,
}

impl ValidationFailureKind {
    fn from_error(error: &Error) -> Self {
        match error {
            Error::ServerUnhealthy(_) => Self::Unhealthy,
            Error::Network(_) => Self::Network,
            Error::Timeout(_) => Self::Timeout,
            Error::Response(_) => Self::Response,
            Error::Api { code, status, .. } => match api_error_kind(code.as_deref(), *status) {
                ApiErrorKind::Authentication => Self::Auth,
                ApiErrorKind::Permission => Self::Permission,
                _ => Self::Other,
            },
            _ => Self::Other,
        }
    }

    fn server_check(self, language: Language) -> String {
        match self {
            Self::Network => fail_value(copy(language, "unreachable", "无法连接")),
            Self::Auth | Self::Permission | Self::Unhealthy => {
                ok_value(copy(language, "reachable", "可连接"))
            }
            Self::Response => warn_value(copy(language, "reachable", "可连接")),
            Self::Timeout => warn_value(copy(language, "timed out", "请求超时")),
            Self::Other => warn_value(copy(language, "not checked", "未检查")),
        }
    }

    fn auth_check(self, language: Language) -> String {
        match self {
            Self::Auth => fail_value(copy(language, "rejected", "被拒绝")),
            Self::Permission => fail_value(copy(language, "permission denied", "权限不足")),
            Self::Network | Self::Timeout | Self::Response => {
                warn_value(copy(language, "not checked", "未检查"))
            }
            Self::Unhealthy => ok_value(copy(language, "accepted", "已通过")),
            Self::Other => warn_value(copy(language, "not checked", "未检查")),
        }
    }

    fn health_check(self, language: Language) -> String {
        match self {
            Self::Unhealthy => fail_value(copy(language, "unhealthy", "不健康")),
            Self::Network | Self::Auth | Self::Permission | Self::Timeout | Self::Response => {
                warn_value(copy(language, "not checked", "未检查"))
            }
            Self::Other => warn_value(copy(language, "not checked", "未检查")),
        }
    }

    fn message(self, language: Language) -> &'static str {
        match language {
            Language::En => match self {
                Self::Network => "Could not reach the configured OpenViking server.",
                Self::Auth => "OpenViking rejected the API key for this config.",
                Self::Permission => "OpenViking rejected this config's permissions.",
                Self::Timeout => "OpenViking did not respond before the timeout.",
                Self::Response => {
                    "OpenViking responded, but the CLI could not understand the response."
                }
                Self::Unhealthy => "OpenViking is reachable but reported an unhealthy state.",
                Self::Other => "The active config could not be validated.",
            },
            Language::ZhCn => match self {
                Self::Network => "无法连接已配置的 OpenViking 服务器。",
                Self::Auth => "OpenViking 拒绝了这个配置的 API Key。",
                Self::Permission => "OpenViking 拒绝了这个配置的权限。",
                Self::Timeout => "OpenViking 未能在超时时间内响应。",
                Self::Response => "OpenViking 已响应，但 CLI 无法理解响应内容。",
                Self::Unhealthy => "服务器可连接，但健康状态异常。",
                Self::Other => "当前配置验证失败。",
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::{config::Config, config_wizard::ConfigKind, error::Error};

    #[test]
    fn validate_success_rendering_is_styled_and_actionable() {
        let config = sample_config();

        colored::control::set_override(true);
        let rendered = super::render_validate_success(&config, Some("VPS"));
        colored::control::unset_override();

        assert!(rendered.contains("\u{1b}["));

        let plain = strip_ansi(&rendered);
        assert!(plain.contains("OPENVIKING CONFIG CHECK"));
        assert!(plain.contains("Active        VPS (Custom)"));
        assert!(plain.contains("Server        http://127.0.0.1:1933"));
        assert!(plain.contains("Config file   valid"));
        assert!(plain.contains("Server        reachable"));
        assert!(plain.contains("Auth          accepted"));
        assert!(plain.contains("Health        healthy"));
        assert!(plain.contains("ov status                 Full system diagnostics"));
        assert!(plain.contains("ov config switch          Use another config"));
    }

    #[test]
    fn validate_failure_rendering_hides_raw_error_and_suggests_recovery() {
        let config = sample_config();
        let error = Error::Network("connection refused at 127.0.0.1:1933".to_string());

        let rendered = super::render_validate_failure(&config, Some("VPS"), &error);
        let plain = strip_ansi(&rendered);

        assert!(plain.contains("OPENVIKING CONFIG CHECK"));
        assert!(plain.contains("Server        unreachable"));
        assert!(plain.contains("Auth          not checked"));
        assert!(plain.contains("Could not reach the configured OpenViking server."));
        assert!(plain.contains("ov health                 Quick server probe"));
        assert!(!plain.contains("connection refused"));
    }

    #[test]
    fn switch_labels_mark_active_once_without_url() {
        let labels = super::switch_labels(&[
            super::SwitchConfigRow {
                name: "local".to_string(),
                kind: ConfigKind::Custom,
                is_active: true,
            },
            super::SwitchConfigRow {
                name: "ov-service-799f84".to_string(),
                kind: ConfigKind::OpenVikingService,
                is_active: false,
            },
        ]);

        let plain = strip_ansi(&labels.join("\n"));
        assert!(plain.lines().any(|line| line.contains("local")
            && line.contains("Custom")
            && line.contains("[Active]")));
        assert!(plain.lines().any(|line| line.contains("ov-service-799f84")
            && line.contains("OpenViking Service")
            && !line.contains("VolcEngine Cloud")
            && !line.contains("[Active]")));
        assert_eq!(plain.matches("[Active]").count(), 1);
        assert!(!plain.contains("http://"));
        assert!(!plain.contains("https://"));
    }

    fn sample_config() -> Config {
        Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        }
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();

        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
                continue;
            }
            output.push(ch);
        }

        output
    }
}
