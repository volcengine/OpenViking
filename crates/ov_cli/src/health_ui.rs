use colored::Colorize;
use serde_json::Value;
use unicode_width::UnicodeWidthStr;

use crate::{config::Config, i18n::Language, theme};

const LABEL_WIDTH: usize = 14;
const ACTION_WIDTH: usize = 26;

pub(crate) fn render_health(payload: &Value, config: Option<&Config>) -> String {
    render_health_with_language(payload, config, Language::current())
}

pub(crate) fn render_health_with_language(
    payload: &Value,
    config: Option<&Config>,
    language: Language,
) -> String {
    let mut lines = Vec::new();

    lines.push(title(language));
    lines.push(String::new());
    lines.push(section(match language {
        Language::En => "Connection",
        Language::ZhCn => "连接",
    }));
    lines.push(detail_line(
        match language {
            Language::En => "Status",
            Language::ZhCn => "状态",
        },
        match payload.get("healthy").and_then(Value::as_bool) {
            Some(true) => healthy_value(match language {
                Language::En => "Connected (Healthy)",
                Language::ZhCn => "已连接（健康）",
            }),
            Some(false) => warning_value(match language {
                Language::En => "Connected (Unhealthy)",
                Language::ZhCn => "已连接（不健康）",
            }),
            None => unknown_value(match language {
                Language::En => "Connected (Unknown)",
                Language::ZhCn => "已连接（未知）",
            }),
        },
    ));
    lines.push(detail_line(
        match language {
            Language::En => "Server",
            Language::ZhCn => "服务器",
        },
        server_status_value(string_field(payload, "status"), language),
    ));
    lines.push(detail_line(
        match language {
            Language::En => "Version",
            Language::ZhCn => "版本",
        },
        soft_value(string_field(payload, "version"), language),
    ));
    lines.push(detail_line(
        match language {
            Language::En => "Auth",
            Language::ZhCn => "认证",
        },
        plain_value(&format_auth_mode(
            string_field(payload, "auth_mode"),
            language,
        )),
    ));
    lines.push(String::new());
    lines.push(section(match language {
        Language::En => "Identity",
        Language::ZhCn => "身份",
    }));
    lines.push(detail_line(
        match language {
            Language::En => "Account",
            Language::ZhCn => "账户",
        },
        plain_or_unknown(
            identity_field(
                payload,
                "account_id",
                config.and_then(|c| c.account.as_deref()),
            ),
            language,
        ),
    ));
    lines.push(detail_line(
        match language {
            Language::En => "User",
            Language::ZhCn => "用户",
        },
        plain_or_unknown(
            identity_field(payload, "user_id", config.and_then(|c| c.user.as_deref())),
            language,
        ),
    ));
    lines.push(detail_line(
        match language {
            Language::En => "Role",
            Language::ZhCn => "角色",
        },
        role_value(string_field(payload, "role"), language),
    ));
    lines.push(String::new());
    lines.push(section(match language {
        Language::En => "Details",
        Language::ZhCn => "详情",
    }));
    lines.push(action_line(
        "ov status",
        match language {
            Language::En => "Full system diagnostics",
            Language::ZhCn => "查看完整系统诊断",
        },
    ));
    lines.push(action_line(
        "ov config validate",
        match language {
            Language::En => "Validate active config",
            Language::ZhCn => "验证当前配置",
        },
    ));

    format!("{}\n", lines.join("\n"))
}

fn title(language: Language) -> String {
    theme::brand_title(match language {
        Language::En => "OPENVIKING HEALTH",
        Language::ZhCn => "OPENVIKING 健康检查",
    })
    .bold()
    .to_string()
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

fn string_field<'a>(payload: &'a Value, key: &str) -> Option<&'a str> {
    payload.get(key).and_then(Value::as_str)
}

fn identity_field<'a>(
    payload: &'a Value,
    key: &str,
    config_value: Option<&'a str>,
) -> Option<&'a str> {
    string_field(payload, key).or(config_value)
}

fn format_auth_mode(value: Option<&str>, language: Language) -> String {
    match value {
        Some("api_key") => "API key".to_string(),
        Some("none") => match language {
            Language::En => "None".to_string(),
            Language::ZhCn => "无".to_string(),
        },
        Some(value) => value.replace('_', " "),
        None => match language {
            Language::En => "unknown".to_string(),
            Language::ZhCn => "未知".to_string(),
        },
    }
}

fn server_status_value(value: Option<&str>, language: Language) -> String {
    match value {
        Some("ok") => healthy_value("ok"),
        Some(value) => warning_value(value),
        None => unknown_value(unknown(language)),
    }
}

fn role_value(value: Option<&str>, language: Language) -> String {
    match value {
        Some("admin") => theme::config_name("admin").bold().to_string(),
        Some(value) => plain_value(value),
        None => unknown_value(unknown(language)),
    }
}

fn plain_or_unknown(value: Option<&str>, language: Language) -> String {
    match value {
        Some(value) if !value.is_empty() => plain_value(value),
        _ => unknown_value(unknown(language)),
    }
}

fn plain_value(value: &str) -> String {
    theme::body(value).to_string()
}

fn soft_value(value: Option<&str>, language: Language) -> String {
    match value {
        Some(value) if !value.is_empty() => theme::value(value).to_string(),
        _ => unknown_value(unknown(language)),
    }
}

fn unknown(language: Language) -> &'static str {
    match language {
        Language::En => "unknown",
        Language::ZhCn => "未知",
    }
}

fn healthy_value(value: &str) -> String {
    theme::success(value).bold().to_string()
}

fn warning_value(value: &str) -> String {
    theme::warning(value).bold().to_string()
}

fn unknown_value(value: &str) -> String {
    theme::muted(value).to_string()
}

#[cfg(test)]
mod tests {
    use crate::config::Config;
    use crate::i18n::Language;
    use serde_json::json;

    #[test]
    fn health_rendering_uses_ansi_styling_without_changing_fields() {
        let payload = json!({
            "status": "ok",
            "healthy": true,
            "version": "0.0.0+feat.oauth.studio.consent.16fa076",
            "auth_mode": "api_key",
            "account_id": "default",
            "user_id": "haozhe",
            "role": "admin"
        });

        colored::control::set_override(true);
        let rendered = super::render_health(&payload, None);
        colored::control::unset_override();

        assert!(rendered.contains("\u{1b}["));

        let plain = strip_ansi(&rendered);
        assert!(plain.contains("OPENVIKING HEALTH"));
        assert!(plain.contains("Connection"));
        assert!(plain.contains("Status        Connected (Healthy)"));
        assert!(plain.contains("Server        ok"));
        assert!(plain.contains("Version       0.0.0+feat.oauth.studio.consent.16fa076"));
        assert!(plain.contains("Auth          API key"));
        assert!(plain.contains("Identity"));
        assert!(plain.contains("Account       default"));
        assert!(plain.contains("User          haozhe"));
        assert!(plain.contains("Role          admin"));
        assert!(plain.contains("Details"));
        assert!(plain.contains("ov status                 Full system diagnostics"));
    }

    #[test]
    fn health_rendering_supports_chinese_labels() {
        let payload = json!({
            "status": "ok",
            "healthy": true,
            "version": "0.3.18",
            "auth_mode": "api_key",
            "account_id": "default",
            "user_id": "haozhe",
            "role": "admin"
        });

        let rendered = super::render_health_with_language(&payload, None, Language::ZhCn);
        let plain = strip_ansi(&rendered);

        assert!(plain.contains("OPENVIKING 健康检查"));
        assert!(plain.contains("连接"));
        assert!(plain.contains("状态"));
        assert!(plain.contains("已连接（健康）"));
        assert!(plain.contains("身份"));
        assert!(plain.contains("详情"));
    }

    #[test]
    fn health_rendering_falls_back_to_config_identity_when_payload_omits_identity() {
        let payload = json!({
            "status": "ok",
            "healthy": true,
            "version": "0.3.18.dev29",
            "auth_mode": "dev"
        });
        let config = Config {
            account: Some("default".to_string()),
            user: Some("default".to_string()),
            ..Config::default()
        };

        let rendered = super::render_health(&payload, Some(&config));
        let plain = strip_ansi(&rendered);

        assert!(plain.contains("Account       default"));
        assert!(plain.contains("User          default"));
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
