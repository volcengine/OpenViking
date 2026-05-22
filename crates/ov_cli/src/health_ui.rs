use colored::Colorize;
use serde_json::Value;

use crate::config::Config;

const LABEL_WIDTH: usize = 14;
const ACTION_WIDTH: usize = 26;
const PEARL: (u8, u8, u8) = (234, 253, 247);
const JADE: (u8, u8, u8) = (50, 214, 196);
const ORANGE: (u8, u8, u8) = (255, 140, 58);
const ICE: (u8, u8, u8) = (182, 219, 255);
const HEALTHY: (u8, u8, u8) = (103, 255, 182);
const WARNING: (u8, u8, u8) = (255, 211, 92);

pub(crate) fn render_health(payload: &Value, config: Option<&Config>) -> String {
    let mut lines = Vec::new();

    lines.push(title());
    lines.push(String::new());
    lines.push(section("Connection"));
    lines.push(detail_line(
        "Status",
        match payload.get("healthy").and_then(Value::as_bool) {
            Some(true) => healthy_value("Connected (Healthy)"),
            Some(false) => warning_value("Connected (Unhealthy)"),
            None => unknown_value("Connected (Unknown)"),
        },
    ));
    lines.push(detail_line(
        "Server",
        server_status_value(string_field(payload, "status")),
    ));
    lines.push(detail_line(
        "Version",
        soft_value(string_field(payload, "version")),
    ));
    lines.push(detail_line(
        "Auth",
        plain_value(&format_auth_mode(string_field(payload, "auth_mode"))),
    ));
    lines.push(String::new());
    lines.push(section("Identity"));
    lines.push(detail_line(
        "Account",
        plain_or_unknown(identity_field(
            payload,
            "account_id",
            config.and_then(|c| c.account.as_deref()),
        )),
    ));
    lines.push(detail_line(
        "User",
        plain_or_unknown(identity_field(
            payload,
            "user_id",
            config.and_then(|c| c.user.as_deref()),
        )),
    ));
    lines.push(detail_line(
        "Agent",
        plain_or_unknown(identity_field(
            payload,
            "agent_id",
            config.and_then(|c| c.agent_id.as_deref()),
        )),
    ));
    lines.push(detail_line(
        "Role",
        role_value(string_field(payload, "role")),
    ));
    lines.push(String::new());
    lines.push(section("Details"));
    lines.push(action_line("ov status", "Full system diagnostics"));
    lines.push(action_line("ov config validate", "Validate active config"));

    format!("{}\n", lines.join("\n"))
}

fn title() -> String {
    "OPENVIKING HEALTH"
        .truecolor(PEARL.0, PEARL.1, PEARL.2)
        .bold()
        .to_string()
}

fn section(value: &str) -> String {
    value.truecolor(JADE.0, JADE.1, JADE.2).bold().to_string()
}

fn detail_line(label: &str, value: String) -> String {
    let label = format!("{label:<LABEL_WIDTH$}").dimmed();
    format!("  {label}{value}")
}

fn action_line(command: &str, description: &str) -> String {
    let command = format!("{command:<ACTION_WIDTH$}")
        .truecolor(JADE.0, JADE.1, JADE.2)
        .bold();
    format!("  {command}{}", description.dimmed())
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

fn format_auth_mode(value: Option<&str>) -> String {
    match value {
        Some("api_key") => "API key".to_string(),
        Some("none") => "None".to_string(),
        Some(value) => value.replace('_', " "),
        None => "unknown".to_string(),
    }
}

fn server_status_value(value: Option<&str>) -> String {
    match value {
        Some("ok") => healthy_value("ok"),
        Some(value) => warning_value(value),
        None => unknown_value("unknown"),
    }
}

fn role_value(value: Option<&str>) -> String {
    match value {
        Some("admin") => "admin"
            .truecolor(ORANGE.0, ORANGE.1, ORANGE.2)
            .bold()
            .to_string(),
        Some(value) => plain_value(value),
        None => unknown_value("unknown"),
    }
}

fn plain_or_unknown(value: Option<&str>) -> String {
    match value {
        Some(value) if !value.is_empty() => plain_value(value),
        _ => unknown_value("unknown"),
    }
}

fn plain_value(value: &str) -> String {
    value.white().to_string()
}

fn soft_value(value: Option<&str>) -> String {
    match value {
        Some(value) if !value.is_empty() => value.truecolor(ICE.0, ICE.1, ICE.2).to_string(),
        _ => unknown_value("unknown"),
    }
}

fn healthy_value(value: &str) -> String {
    value
        .truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2)
        .bold()
        .to_string()
}

fn warning_value(value: &str) -> String {
    value
        .truecolor(WARNING.0, WARNING.1, WARNING.2)
        .bold()
        .to_string()
}

fn unknown_value(value: &str) -> String {
    value.dimmed().to_string()
}

#[cfg(test)]
mod tests {
    use crate::config::Config;
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
            "agent_id": "default",
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
        assert!(plain.contains("Agent         default"));
        assert!(plain.contains("Role          admin"));
        assert!(plain.contains("Details"));
        assert!(plain.contains("ov status                 Full system diagnostics"));
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
