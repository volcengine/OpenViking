use colored::Colorize;
use serde_json::Value;

use crate::{
    config::{Config, default_config_path},
    config_wizard::{ConfigKind, ConfigStore},
    error::Result,
};

const LABEL_WIDTH: usize = 14;
const ACTION_WIDTH: usize = 26;
const COMPONENT_WIDTH: usize = 15;
const HEALTH_WIDTH: usize = 11;
const PEARL: (u8, u8, u8) = (234, 253, 247);
const JADE: (u8, u8, u8) = (50, 214, 196);
const ORANGE: (u8, u8, u8) = (255, 140, 58);
const ICE: (u8, u8, u8) = (182, 219, 255);
const HEALTHY: (u8, u8, u8) = (103, 255, 182);
const WARNING: (u8, u8, u8) = (255, 211, 92);
const ERROR: (u8, u8, u8) = (255, 91, 91);

#[derive(Debug, Clone, Default)]
pub(crate) struct StatusConfigMeta {
    pub active_name: Option<String>,
    pub saved_count: usize,
}

#[derive(Debug, Clone, Default)]
struct QueueSummary {
    pending: Option<u64>,
    in_progress: Option<u64>,
    errors: Option<u64>,
}

pub(crate) fn current_config_meta() -> StatusConfigMeta {
    let Ok(store) = ConfigStore::new() else {
        return StatusConfigMeta::default();
    };
    let Ok(configs) = store.list_configs() else {
        return StatusConfigMeta::default();
    };

    StatusConfigMeta {
        active_name: configs
            .iter()
            .find(|config| config.is_active)
            .map(|config| config.name.clone()),
        saved_count: configs.len(),
    }
}

pub(crate) fn render_status(
    payload: &Value,
    config: &Config,
    active_name: Option<&str>,
) -> Result<String> {
    let kind = ConfigKind::from_config(config).label();
    let active = active_name.unwrap_or("unknown");
    let queue = queue_summary(component_status(payload, "queue"));
    let models = models_summary(component_status(payload, "models"));

    let mut lines = Vec::new();
    lines.push(status_title());
    lines.push(String::new());
    lines.push(section_title("Config"));
    lines.push(detail_line_styled(
        "Active",
        active_config_value(active, kind),
    ));
    lines.push(detail_line_styled("Server", path_value(&config.url)));
    lines.push(detail_line_styled(
        "Config home",
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section_title("System"));
    lines.push(detail_line_styled(
        "Status",
        if system_is_healthy(payload) {
            status_value("Connected (Healthy)")
        } else {
            unhealthy_value("Connected (Unhealthy)")
        },
    ));
    lines.push(detail_line_styled(
        "Pending",
        activity_count_value(queue.pending),
    ));
    lines.push(detail_line_styled(
        "In progress",
        activity_count_value(queue.in_progress),
    ));
    lines.push(detail_line_styled(
        "Errors",
        error_count_value(queue.errors),
    ));
    lines.push(String::new());
    lines.push(section_title("Models"));
    lines.push(detail_line_styled(
        "VLM",
        model_value(models.vlm.as_deref().unwrap_or("unknown")),
    ));
    lines.push(detail_line_styled(
        "Embedding",
        model_value(models.embedding.as_deref().unwrap_or("unknown")),
    ));
    lines.push(String::new());
    lines.push(section_title("Components"));
    lines.push(component_header_line());
    for component in [
        "queue",
        "vikingdb",
        "models",
        "lock",
        "retrieval",
        "filesystem",
    ] {
        lines.push(component_line(component, payload, &queue, &models));
    }
    lines.push(String::new());
    lines.push(section_title("Details"));
    lines.push(action_line(
        "ov status --verbose",
        "Show full component tables",
    ));
    lines.push(action_line("ov observer queue", "Inspect queue details"));
    lines.push(action_line("ov observer models", "Inspect model usage"));

    Ok(format!("{}\n", lines.join("\n")))
}

pub(crate) fn render_unreachable_status(
    config: &Config,
    active_name: Option<&str>,
    saved_count: usize,
) -> String {
    let kind = ConfigKind::from_config(config).label();
    let active = active_name.unwrap_or("unknown");
    let mut lines = Vec::new();

    lines.push(status_title());
    lines.push(String::new());
    lines.push(section_title("Config"));
    lines.push(detail_line_styled(
        "Active",
        active_config_value(active, kind),
    ));
    lines.push(detail_line_styled("Server", path_value(&config.url)));
    lines.push(detail_line_styled(
        "Config home",
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section_title("System"));
    lines.push(detail_line_styled("Status", error_value("Unreachable")));
    lines.push(detail_line_styled(
        "Saved configs",
        plain_value(&saved_count.to_string()),
    ));
    lines.push(String::new());
    lines.push(section_title("What to try"));
    lines.push(action_line(
        "ov config validate",
        "Check config, auth, and server reachability",
    ));
    lines.push(action_line("ov config", "Edit or switch config"));
    lines.push(action_line("ov health", "Run a quick health check"));

    format!("{}\n", lines.join("\n"))
}

fn status_title() -> String {
    "OPENVIKING STATUS"
        .truecolor(PEARL.0, PEARL.1, PEARL.2)
        .bold()
        .to_string()
}

fn section_title(title: &str) -> String {
    title.truecolor(JADE.0, JADE.1, JADE.2).bold().to_string()
}

fn detail_line_styled(label: &str, value: String) -> String {
    let label = format!("{label:<LABEL_WIDTH$}").dimmed();
    format!("  {label}{value}")
}

fn action_line(command: &str, description: &str) -> String {
    let command = format!("{command:<ACTION_WIDTH$}")
        .truecolor(JADE.0, JADE.1, JADE.2)
        .bold();
    format!("  {command}{}", description.dimmed())
}

fn active_config_value(name: &str, kind: &str) -> String {
    format!(
        "{} {}",
        name.truecolor(ORANGE.0, ORANGE.1, ORANGE.2).bold(),
        format!("({kind})").white().bold()
    )
}

fn path_value(value: &str) -> String {
    if value == "unknown" {
        unknown_value(value)
    } else {
        value.truecolor(ICE.0, ICE.1, ICE.2).to_string()
    }
}

fn model_value(value: &str) -> String {
    if value == "unknown" {
        unknown_value(value)
    } else {
        value.truecolor(ICE.0, ICE.1, ICE.2).bold().to_string()
    }
}

fn status_value(value: &str) -> String {
    value
        .truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2)
        .bold()
        .to_string()
}

fn unhealthy_value(value: &str) -> String {
    value
        .truecolor(WARNING.0, WARNING.1, WARNING.2)
        .bold()
        .to_string()
}

fn error_value(value: &str) -> String {
    value
        .truecolor(ERROR.0, ERROR.1, ERROR.2)
        .bold()
        .to_string()
}

fn plain_value(value: &str) -> String {
    value.white().to_string()
}

fn unknown_value(value: &str) -> String {
    value.dimmed().to_string()
}

fn activity_count_value(value: Option<u64>) -> String {
    match value {
        Some(0) => "0".white().to_string(),
        Some(value) => value
            .to_string()
            .truecolor(WARNING.0, WARNING.1, WARNING.2)
            .bold()
            .to_string(),
        None => unknown_value("unknown"),
    }
}

fn error_count_value(value: Option<u64>) -> String {
    match value {
        Some(0) => "0".truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2).to_string(),
        Some(value) => error_value(&value.to_string()),
        None => unknown_value("unknown"),
    }
}

fn component_header_line() -> String {
    let component = format!("{:<COMPONENT_WIDTH$}", "Component").dimmed().bold();
    let health = format!("{:<HEALTH_WIDTH$}", "Health").dimmed().bold();
    format!("  {component}{health}{}", "Summary".dimmed().bold())
}

fn component_line(
    component: &str,
    payload: &Value,
    queue: &QueueSummary,
    models: &ModelSummary,
) -> String {
    let health = component_health(payload, component);
    let summary = match component {
        "queue" => queue_component_summary(queue),
        "vikingdb" => vikingdb_summary(component_status(payload, component)),
        "models" => models_component_summary(models),
        "lock" => lock_summary(component_status(payload, component)),
        "retrieval" => retrieval_summary(component_status(payload, component)),
        "filesystem" => filesystem_summary(component_status(payload, component)),
        _ => "unknown".to_string(),
    };

    let component = format!("{component:<COMPONENT_WIDTH$}")
        .truecolor(PEARL.0, PEARL.1, PEARL.2)
        .bold();
    let health = styled_health_cell(health);
    let summary = summary_value(&summary);
    format!("  {component}{health}{summary}")
}

fn styled_health_cell(health: &str) -> String {
    let cell = format!("{health:<HEALTH_WIDTH$}");
    match health {
        "healthy" => cell
            .truecolor(HEALTHY.0, HEALTHY.1, HEALTHY.2)
            .bold()
            .to_string(),
        "unhealthy" => cell
            .truecolor(WARNING.0, WARNING.1, WARNING.2)
            .bold()
            .to_string(),
        _ => cell.dimmed().to_string(),
    }
}

fn summary_value(summary: &str) -> String {
    if summary == "unknown" {
        unknown_value(summary)
    } else {
        summary.white().to_string()
    }
}

fn component_status<'a>(payload: &'a Value, component: &str) -> Option<&'a str> {
    payload
        .pointer(&format!("/components/{component}/status"))
        .and_then(Value::as_str)
}

fn component_health(payload: &Value, component: &str) -> &'static str {
    match payload
        .pointer(&format!("/components/{component}/is_healthy"))
        .and_then(Value::as_bool)
    {
        Some(true) => "healthy",
        Some(false) => "unhealthy",
        None => "unknown",
    }
}

fn system_is_healthy(payload: &Value) -> bool {
    if let Some(healthy) = payload
        .get("is_healthy")
        .or_else(|| payload.get("healthy"))
        .and_then(Value::as_bool)
    {
        return healthy;
    }

    let Some(components) = payload.get("components").and_then(Value::as_object) else {
        return false;
    };
    !components.is_empty()
        && components
            .values()
            .all(|component| component.get("is_healthy").and_then(Value::as_bool) != Some(false))
}

fn queue_summary(status: Option<&str>) -> QueueSummary {
    let Some(status) = status else {
        return QueueSummary::default();
    };
    let rows = pipe_rows(status);
    let Some(header) = rows
        .iter()
        .find(|row| row.iter().any(|cell| cell == "Pending"))
    else {
        return QueueSummary::default();
    };
    let Some(total) = rows.iter().find(|row| {
        row.first()
            .is_some_and(|cell| cell.eq_ignore_ascii_case("TOTAL"))
    }) else {
        return QueueSummary::default();
    };

    QueueSummary {
        pending: cell_by_header(header, total, "Pending").and_then(parse_u64),
        in_progress: cell_by_header(header, total, "In Progress").and_then(parse_u64),
        errors: cell_by_header(header, total, "Errors").and_then(parse_u64),
    }
}

fn queue_component_summary(queue: &QueueSummary) -> String {
    match (queue.pending, queue.in_progress, queue.errors) {
        (Some(pending), Some(in_progress), Some(errors)) => {
            format!("{pending} pending, {in_progress} running, {errors} errors")
        }
        _ => "unknown".to_string(),
    }
}

#[derive(Debug, Clone, Default)]
struct ModelSummary {
    vlm: Option<String>,
    embedding: Option<String>,
}

fn models_summary(status: Option<&str>) -> ModelSummary {
    let Some(status) = status else {
        return ModelSummary::default();
    };
    ModelSummary {
        vlm: first_model_after_heading(status, "VLM Models:"),
        embedding: first_model_after_heading(status, "Embedding Models:"),
    }
}

fn models_component_summary(models: &ModelSummary) -> String {
    match (models.vlm.is_some(), models.embedding.is_some()) {
        (true, true) => "VLM + embedding available".to_string(),
        (true, false) => "VLM available".to_string(),
        (false, true) => "embedding available".to_string(),
        (false, false) => "unknown".to_string(),
    }
}

fn first_model_after_heading(status: &str, heading: &str) -> Option<String> {
    let (_, section) = status.split_once(heading)?;
    pipe_rows(section).into_iter().find_map(|row| {
        let first = row.first()?.trim();
        if first.is_empty()
            || first == "Model"
            || first.eq_ignore_ascii_case("provider")
            || first.contains("----")
        {
            return None;
        }
        Some(first.to_string())
    })
}

fn vikingdb_summary(status: Option<&str>) -> String {
    let Some(status) = status else {
        return "unknown".to_string();
    };
    let rows = pipe_rows(status);
    let Some(header) = rows
        .iter()
        .find(|row| row.iter().any(|cell| cell == "Vector Count"))
    else {
        return "unknown".to_string();
    };
    let Some(total) = rows.iter().find(|row| {
        row.first()
            .is_some_and(|cell| cell.eq_ignore_ascii_case("TOTAL"))
    }) else {
        return "unknown".to_string();
    };

    let collections = cell_by_header(header, total, "Index Count").and_then(parse_u64);
    let vectors = cell_by_header(header, total, "Vector Count").and_then(parse_u64);
    match (collections, vectors) {
        (Some(collections), Some(vectors)) => format!(
            "{} {}, {} vectors",
            collections,
            pluralize(collections, "collection"),
            vectors
        ),
        _ => "unknown".to_string(),
    }
}

fn lock_summary(status: Option<&str>) -> String {
    let Some(status) = status else {
        return "unknown".to_string();
    };
    if status.contains("No active locks") {
        return "0 active locks".to_string();
    }
    let Some(total_row) = pipe_rows(status).into_iter().find(|row| {
        row.first()
            .is_some_and(|cell| cell.to_ascii_uppercase().starts_with("TOTAL"))
    }) else {
        return "unknown".to_string();
    };
    let count = total_row
        .first()
        .and_then(|cell| cell.split_once('('))
        .and_then(|(_, rest)| rest.split_once(')'))
        .and_then(|(count, _)| parse_u64(count));
    match count {
        Some(count) => format!("{} active {}", count, pluralize(count, "lock")),
        None => "unknown".to_string(),
    }
}

fn retrieval_summary(status: Option<&str>) -> String {
    let Some(status) = status else {
        return "unknown".to_string();
    };
    let queries = metric_value(status, "Total Queries");
    let zero_rate = metric_value(status, "Zero-Result Rate");
    match (queries, zero_rate) {
        (Some(queries), Some(zero_rate)) => {
            format!("{queries} queries, {zero_rate} zero-result rate")
        }
        _ => "unknown".to_string(),
    }
}

fn filesystem_summary(status: Option<&str>) -> String {
    let Some(status) = status else {
        return "unknown".to_string();
    };
    let mounts = status
        .lines()
        .filter(|line| line.starts_with("Mount: "))
        .count();
    let total_ops: u64 = pipe_rows(status)
        .into_iter()
        .filter(|row| row.first().is_some_and(|cell| cell == "Total Operations"))
        .filter_map(|row| row.get(1).and_then(|cell| parse_u64(cell)))
        .sum();

    match (mounts, total_ops) {
        (0, 0) => "unknown".to_string(),
        (mounts, 0) => format!("{} {}", mounts, pluralize(mounts as u64, "mount")),
        (0, total_ops) => format!("{} ops", compact_number(total_ops)),
        (mounts, total_ops) => format!(
            "{} {}, {} ops",
            mounts,
            pluralize(mounts as u64, "mount"),
            compact_number(total_ops)
        ),
    }
}

fn metric_value(status: &str, metric: &str) -> Option<String> {
    pipe_rows(status).into_iter().find_map(|row| {
        if row.first().is_some_and(|cell| cell == metric) {
            row.get(1).cloned()
        } else {
            None
        }
    })
}

fn pipe_rows(status: &str) -> Vec<Vec<String>> {
    status
        .lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if !trimmed.starts_with('|') {
                return None;
            }
            let cells: Vec<String> = trimmed
                .trim_matches('|')
                .split('|')
                .map(str::trim)
                .filter(|cell| !cell.is_empty())
                .map(ToString::to_string)
                .collect();
            if cells.is_empty() { None } else { Some(cells) }
        })
        .collect()
}

fn cell_by_header<'a>(header: &[String], row: &'a [String], name: &str) -> Option<&'a str> {
    let index = header.iter().position(|cell| cell == name)?;
    row.get(index).map(String::as_str)
}

fn parse_u64(value: &str) -> Option<u64> {
    value
        .chars()
        .filter(|ch| ch.is_ascii_digit())
        .collect::<String>()
        .parse()
        .ok()
}

fn pluralize(count: u64, singular: &str) -> String {
    if count == 1 {
        singular.to_string()
    } else {
        format!("{singular}s")
    }
}

fn compact_number(value: u64) -> String {
    if value >= 1_000_000 {
        format!("{:.1}M", value as f64 / 1_000_000.0)
    } else if value >= 1_000 {
        format!("{:.1}K", value as f64 / 1_000.0)
    } else {
        value.to_string()
    }
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use crate::config::Config;

    fn sample_config() -> Config {
        Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        }
    }

    fn sample_status_payload() -> serde_json::Value {
        json!({
            "is_healthy": true,
            "components": {
                "queue": {
                    "name": "queue",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "\
        +----------------+---------+-------------+-----------+----------+--------+-------+\n\
        |     Queue      | Pending | In Progress | Processed | Requeued | Errors | Total |\n\
        +----------------+---------+-------------+-----------+----------+--------+-------+\n\
        |   Embedding    |   64    |      9      |   3917    |    0     |   0    | 3990  |\n\
        |    Semantic    |    0    |      0      |    16     |    0     |   0    |  16   |\n\
        |     TOTAL      |   64    |      9      |   3933    |    0     |   0    | 4006  |\n\
        +----------------+---------+-------------+-----------+----------+--------+-------+"
                },
                "vikingdb": {
                    "name": "vikingdb",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "\
        +------------+-------------+--------------+--------+\n\
        | Collection | Index Count | Vector Count | Status |\n\
        +------------+-------------+--------------+--------+\n\
        |  context   |      1      |     6877     |   OK   |\n\
        |   TOTAL    |      1      |     6877     |        |\n\
        +------------+-------------+--------------+--------+"
                },
                "models": {
                    "name": "models",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "\nVLM Models:\n\
        +----------------------------+------------+-------+\n\
        |           Model            |  Provider  | Calls |\n\
        +----------------------------+------------+-------+\n\
        | doubao-seed-2-0-pro-260215 | volcengine | 1989  |\n\
        +----------------------------+------------+-------+\n\
        \nEmbedding Models:\n\
        +--------------------------------+------------+-------+\n\
        |             Model              |  Provider  | Calls |\n\
        +--------------------------------+------------+-------+\n\
        | doubao-embedding-vision-251215 | volcengine | 4038  |\n\
        +--------------------------------+------------+-------+"
                },
                "lock": {
                    "name": "lock",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "\
        +-------------+-------+----------+\n\
        |  Handle ID  | Locks | Duration |\n\
        +-------------+-------+----------+\n\
        | b1b3c983... |   1   |  337.6s  |\n\
        |  TOTAL (1)  |   1   |          |\n\
        +-------------+-------+----------+"
                },
                "retrieval": {
                    "name": "retrieval",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "\
        +---------------------+-----------------+\n\
        |       Metric        |      Value      |\n\
        +---------------------+-----------------+\n\
        |    Total Queries    |       121       |\n\
        | Zero-Result Rate   |      28.9%      |\n\
        +---------------------+-----------------+"
                },
                "filesystem": {
                    "name": "filesystem",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "Mount: /local (plugin: localfs)\nMount: /queue (plugin: queuefs)\nMount: /serverinfo (plugin: serverinfofs)\nTotal Operations | 967891"
                }
            },
            "errors": []
        })
    }

    #[test]
    fn healthy_payload_renders_selected_status_sections() {
        let rendered = super::render_status(&sample_status_payload(), &sample_config(), None)
            .expect("status should render");
        let rendered = strip_ansi(&rendered);

        assert!(rendered.contains("OPENVIKING STATUS"));
        assert!(rendered.contains("Config"));
        assert!(rendered.contains("Active        unknown (Self-Managed)"));
        assert!(rendered.contains("Server        http://127.0.0.1:1933"));
        assert!(rendered.contains("System"));
        assert!(rendered.contains("Status        Connected (Healthy)"));
        assert!(rendered.contains("Pending       64"));
        assert!(rendered.contains("In progress   9"));
        assert!(rendered.contains("Errors        0"));
        assert!(rendered.contains("Models"));
        assert!(rendered.contains("VLM           doubao-seed-2-0-pro-260215"));
        assert!(rendered.contains("Embedding     doubao-embedding-vision-251215"));
        assert!(rendered.contains("Components"));
        assert!(rendered.contains("queue          healthy    64 pending, 9 running, 0 errors"));
        assert!(rendered.contains("vikingdb       healthy    1 collection, 6877 vectors"));
        assert!(rendered.contains("Details"));
        assert!(rendered.contains("ov status --verbose       Show full component tables"));
    }

    #[test]
    fn missing_component_text_renders_unknown_safely() {
        let payload = json!({
            "is_healthy": true,
            "components": {
                "queue": {
                    "name": "queue",
                    "is_healthy": true,
                    "has_errors": false,
                    "status": "not a table"
                }
            },
            "errors": []
        });

        let rendered = super::render_status(&payload, &sample_config(), Some("local"))
            .expect("status should render");
        let rendered = strip_ansi(&rendered);

        assert!(rendered.contains("Active        local (Self-Managed)"));
        assert!(rendered.contains("Pending       unknown"));
        assert!(rendered.contains("queue          healthy    unknown"));
        assert!(rendered.contains("models         unknown    unknown"));
    }

    #[test]
    fn default_status_uses_ansi_styling_without_changing_text() {
        colored::control::set_override(true);
        let rendered =
            super::render_status(&sample_status_payload(), &sample_config(), Some("local"))
                .expect("status should render");
        colored::control::unset_override();

        assert!(rendered.contains("\u{1b}["));

        let plain = strip_ansi(&rendered);
        assert!(plain.contains("OPENVIKING STATUS"));
        assert!(plain.contains("Active        local (Self-Managed)"));
        assert!(plain.contains("queue          healthy    64 pending, 9 running, 0 errors"));
        assert!(plain.contains("ov status --verbose       Show full component tables"));
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
