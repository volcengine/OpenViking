use colored::Colorize;

use crate::{
    config::{Config, default_config_path},
    config_wizard::ConfigKind,
    error::Error,
};

const LABEL_WIDTH: usize = 14;
const ACTION_WIDTH: usize = 26;
const NAME_WIDTH: usize = 17;
const KIND_WIDTH: usize = 18;
const PEARL: (u8, u8, u8) = (234, 253, 247);
const JADE: (u8, u8, u8) = (50, 214, 196);
const ORANGE: (u8, u8, u8) = (255, 140, 58);
const ICE: (u8, u8, u8) = (182, 219, 255);
const HEALTHY: (u8, u8, u8) = (103, 255, 182);
const WARNING: (u8, u8, u8) = (255, 211, 92);
const ERROR: (u8, u8, u8) = (255, 91, 91);

#[derive(Debug, Clone)]
pub(crate) struct SwitchConfigRow {
    pub name: String,
    pub kind: ConfigKind,
    pub is_active: bool,
}

pub(crate) fn render_validate_success(config: &Config, active_name: Option<&str>) -> String {
    let mut lines = Vec::new();
    let kind = ConfigKind::from_config(config).label();
    let active = active_name.unwrap_or("unknown");

    lines.push(title("OPENVIKING CONFIG CHECK"));
    lines.push(String::new());
    lines.push(section("Config"));
    lines.push(detail_line("Active", active_value(active, kind)));
    lines.push(detail_line("Server", path_value(&config.url)));
    lines.push(detail_line(
        "Config home",
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section("Checks"));
    lines.push(detail_line("Config file", ok_value("valid")));
    lines.push(detail_line("Server", ok_value("reachable")));
    lines.push(detail_line("Auth", ok_value("accepted")));
    lines.push(detail_line("Health", ok_value("healthy")));
    lines.push(String::new());
    lines.push(section("Next"));
    lines.push(action_line("ov status", "Full system diagnostics"));
    lines.push(action_line("ov config switch", "Use another config"));
    lines.push(action_line("ov config", "Add, edit, or delete configs"));

    format!("{}\n", lines.join("\n"))
}

pub(crate) fn render_validate_failure(
    config: &Config,
    active_name: Option<&str>,
    error: &Error,
) -> String {
    let mut lines = Vec::new();
    let kind = ConfigKind::from_config(config).label();
    let active = active_name.unwrap_or("unknown");
    let classification = ValidationFailureKind::from_error(error);

    lines.push(title("OPENVIKING CONFIG CHECK"));
    lines.push(String::new());
    lines.push(section("Config"));
    lines.push(detail_line("Active", active_value(active, kind)));
    lines.push(detail_line("Server", path_value(&config.url)));
    lines.push(detail_line(
        "Config home",
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section("Checks"));
    lines.push(detail_line("Config file", ok_value("valid")));
    lines.push(detail_line("Server", classification.server_check()));
    lines.push(detail_line("Auth", classification.auth_check()));
    lines.push(detail_line("Health", classification.health_check()));
    lines.push(String::new());
    lines.push(section("Issue"));
    lines.push(format!(
        "  {}",
        classification
            .message()
            .truecolor(ERROR.0, ERROR.1, ERROR.2)
    ));
    lines.push(String::new());
    lines.push(section("Try"));
    lines.push(action_line("ov health", "Quick server probe"));
    lines.push(action_line("ov config", "Edit this config"));
    lines.push(action_line("ov config switch", "Use another config"));

    format!("{}\n", lines.join("\n"))
}

pub(crate) fn render_switch_header(
    active_name: Option<&str>,
    active_kind: Option<ConfigKind>,
) -> String {
    let mut lines = Vec::new();
    lines.push(title("OPENVIKING CONFIG SWITCH"));
    lines.push(String::new());
    match (active_name, active_kind) {
        (Some(name), Some(kind)) => lines.push(format!(
            "{} {}",
            "Active:".dimmed(),
            active_value(name, kind.label())
        )),
        _ => lines.push(format!("{} {}", "Active:".dimmed(), unknown_value("none"))),
    }
    format!("{}\n", lines.join("\n"))
}

pub(crate) fn switch_labels(rows: &[SwitchConfigRow]) -> Vec<String> {
    rows.iter()
        .map(|row| {
            let name = format!("{:<NAME_WIDTH$}", row.name)
                .truecolor(PEARL.0, PEARL.1, PEARL.2)
                .bold();
            let kind = format!("{:<KIND_WIDTH$}", row.kind.label()).white();
            if row.is_active {
                format!(
                    "{name}{kind}{}",
                    "[Active]".truecolor(ERROR.0, ERROR.1, ERROR.2).bold()
                )
            } else {
                format!("{name}{kind}")
            }
        })
        .collect()
}

pub(crate) fn render_no_saved_configs() -> String {
    let mut lines = Vec::new();
    lines.push(title("OPENVIKING CONFIG SWITCH"));
    lines.push(String::new());
    lines.push(section("No saved configs"));
    lines.push(format!(
        "  {}",
        "Run ov config to add and save a config first.".dimmed()
    ));
    format!("{}\n", lines.join("\n"))
}

pub(crate) fn render_switch_success(name: &str) -> String {
    format!(
        "{} {}\n{}\n",
        "✓".truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2).bold(),
        format!("Switched active config to '{name}'.")
            .truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2)
            .bold(),
        format!("  {}", "Run ov status to inspect it.".dimmed())
    )
}

pub(crate) fn render_switch_validation_failure(name: &str, error: &Error) -> String {
    let classification = ValidationFailureKind::from_error(error);
    format!(
        "{}\n\n{}\n  {}\n  {}\n\n{}",
        title("OPENVIKING CONFIG SWITCH"),
        section("Issue"),
        format!("Target config '{name}' failed validation.").truecolor(ERROR.0, ERROR.1, ERROR.2),
        classification.message().dimmed(),
        action_line("ov config", "Edit this config")
    )
}

fn title(value: &str) -> String {
    value
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

fn active_value(name: &str, kind: &str) -> String {
    format!(
        "{} {}",
        name.truecolor(ORANGE.0, ORANGE.1, ORANGE.2).bold(),
        format!("({kind})").white().bold()
    )
}

fn path_value(value: &str) -> String {
    value.truecolor(ICE.0, ICE.1, ICE.2).to_string()
}

fn ok_value(value: &str) -> String {
    value
        .truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2)
        .bold()
        .to_string()
}

fn warn_value(value: &str) -> String {
    value
        .truecolor(WARNING.0, WARNING.1, WARNING.2)
        .bold()
        .to_string()
}

fn fail_value(value: &str) -> String {
    value
        .truecolor(ERROR.0, ERROR.1, ERROR.2)
        .bold()
        .to_string()
}

fn unknown_value(value: &str) -> String {
    value.dimmed().to_string()
}

fn display_config_home() -> String {
    let path = default_config_path()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.to_path_buf()));
    let Some(path) = path else {
        return "~/.openviking".to_string();
    };
    let Some(home) = dirs::home_dir() else {
        return path.display().to_string();
    };
    if let Ok(stripped) = path.strip_prefix(&home) {
        return format!("~/{}", stripped.display());
    }
    path.display().to_string()
}

#[derive(Debug, Clone, Copy)]
enum ValidationFailureKind {
    Network,
    Auth,
    Unhealthy,
    Other,
}

impl ValidationFailureKind {
    fn from_error(error: &Error) -> Self {
        match error {
            Error::Network(message) if message.contains("unhealthy") => Self::Unhealthy,
            Error::Network(_) => Self::Network,
            Error::Api(message) if looks_like_auth_error(message) => Self::Auth,
            _ => Self::Other,
        }
    }

    fn server_check(self) -> String {
        match self {
            Self::Network => fail_value("unreachable"),
            Self::Auth | Self::Unhealthy => ok_value("reachable"),
            Self::Other => warn_value("unknown"),
        }
    }

    fn auth_check(self) -> String {
        match self {
            Self::Auth => fail_value("rejected"),
            Self::Network => warn_value("not checked"),
            Self::Unhealthy => ok_value("accepted"),
            Self::Other => warn_value("unknown"),
        }
    }

    fn health_check(self) -> String {
        match self {
            Self::Unhealthy => fail_value("unhealthy"),
            Self::Network | Self::Auth => warn_value("not checked"),
            Self::Other => warn_value("unknown"),
        }
    }

    fn message(self) -> &'static str {
        match self {
            Self::Network => "Could not reach the configured OpenViking server.",
            Self::Auth => "OpenViking rejected the API key for this config.",
            Self::Unhealthy => "OpenViking is reachable but reported an unhealthy state.",
            Self::Other => "The active config could not be validated.",
        }
    }
}

fn looks_like_auth_error(message: &str) -> bool {
    let message = message.to_ascii_lowercase();
    message.contains("api key")
        || message.contains("unauthorized")
        || message.contains("forbidden")
        || message.contains("authentication")
        || message.contains("auth")
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
        assert!(plain.contains("Active        VPS (Self-Managed)"));
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
                kind: ConfigKind::SelfManaged,
                is_active: true,
            },
            super::SwitchConfigRow {
                name: "cloud-799f84".to_string(),
                kind: ConfigKind::VolcengineCloud,
                is_active: false,
            },
        ]);

        let plain = strip_ansi(&labels.join("\n"));
        assert!(plain.contains("local            Self-Managed      [Active]"));
        assert!(plain.contains("cloud-799f84     Volcengine Cloud"));
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
