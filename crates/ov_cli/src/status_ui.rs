use colored::Colorize;
use serde_json::Value;
use unicode_width::UnicodeWidthStr;

use crate::{
    config::{Config, display_config_home},
    config_wizard::{ConfigKind, ConfigStore},
    error::Result,
    i18n::{Language, copy},
    theme,
};

const LABEL_WIDTH: usize = 14;
const ACTION_WIDTH: usize = 26;
const COMPONENT_WIDTH: usize = 15;
const HEALTH_WIDTH: usize = 11;

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
    render_status_with_language(payload, config, active_name, Language::current())
}

pub(crate) fn render_status_with_language(
    payload: &Value,
    config: &Config,
    active_name: Option<&str>,
    language: Language,
) -> Result<String> {
    let kind = kind_label(ConfigKind::from_config(config), language);
    let active = active_name.unwrap_or_else(|| unknown(language));
    let queue = queue_summary(component_status(payload, "queue"));
    let models = models_summary(component_status(payload, "models"));

    let mut lines = Vec::new();
    lines.push(status_title(language));
    lines.push(String::new());
    lines.push(section_title(copy(language, "Config", "配置")));
    lines.push(detail_line_styled(
        copy(language, "Active", "当前配置"),
        active_config_value(active, kind),
    ));
    lines.push(detail_line_styled(
        copy(language, "Server", "服务器"),
        path_value(&config.url),
    ));
    lines.push(detail_line_styled(
        copy(language, "Config home", "配置目录"),
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section_title(copy(language, "System", "系统")));
    lines.push(detail_line_styled(
        copy(language, "Status", "状态"),
        if system_is_healthy(payload) {
            status_value(copy(language, "Connected (Healthy)", "已连接（健康）"))
        } else {
            unhealthy_value(copy(language, "Connected (Unhealthy)", "已连接（不健康）"))
        },
    ));
    lines.push(detail_line_styled(
        copy(language, "Pending", "待处理"),
        activity_count_value(queue.pending),
    ));
    lines.push(detail_line_styled(
        copy(language, "In progress", "处理中"),
        activity_count_value(queue.in_progress),
    ));
    lines.push(detail_line_styled(
        copy(language, "Errors", "错误"),
        error_count_value(queue.errors),
    ));
    lines.push(String::new());
    lines.push(section_title(copy(language, "Models", "模型")));
    lines.push(detail_line_styled(
        "VLM",
        model_value(models.vlm.as_deref().unwrap_or_else(|| unknown(language))),
    ));
    lines.push(detail_line_styled(
        copy(language, "Embedding", "Embedding"),
        model_value(
            models
                .embedding
                .as_deref()
                .unwrap_or_else(|| unknown(language)),
        ),
    ));
    lines.push(String::new());
    lines.push(section_title(copy(language, "Components", "组件")));
    lines.push(component_header_line(language));
    for component in [
        "queue",
        "vikingdb",
        "models",
        "lock",
        "retrieval",
        "filesystem",
    ] {
        lines.push(component_line(
            component, payload, &queue, &models, language,
        ));
    }
    lines.push(String::new());
    lines.push(section_title(copy(language, "Details", "详情")));
    lines.push(action_line(
        "ov status --verbose",
        copy(language, "Show full component tables", "显示完整组件表"),
    ));
    lines.push(action_line(
        "ov observer queue",
        copy(language, "Inspect queue details", "查看队列详情"),
    ));
    lines.push(action_line(
        "ov observer models",
        copy(language, "Inspect model usage", "查看模型使用情况"),
    ));

    Ok(format!("{}\n", lines.join("\n")))
}

pub(crate) fn render_unreachable_status(
    config: &Config,
    active_name: Option<&str>,
    saved_count: usize,
) -> String {
    let language = Language::current();
    let kind = kind_label(ConfigKind::from_config(config), language);
    let active = active_name.unwrap_or_else(|| unknown(language));
    let mut lines = Vec::new();

    lines.push(status_title(language));
    lines.push(String::new());
    lines.push(section_title(copy(language, "Config", "配置")));
    lines.push(detail_line_styled(
        copy(language, "Active", "当前配置"),
        active_config_value(active, kind),
    ));
    lines.push(detail_line_styled(
        copy(language, "Server", "服务器"),
        path_value(&config.url),
    ));
    lines.push(detail_line_styled(
        copy(language, "Config home", "配置目录"),
        path_value(&display_config_home()),
    ));
    lines.push(String::new());
    lines.push(section_title(copy(language, "System", "系统")));
    lines.push(detail_line_styled(
        copy(language, "Status", "状态"),
        error_value(copy(language, "Unreachable", "无法连接")),
    ));
    lines.push(detail_line_styled(
        copy(language, "Saved configs", "已保存配置"),
        plain_value(&saved_count.to_string()),
    ));
    lines.push(String::new());
    lines.push(section_title(copy(language, "What to try", "可以尝试")));
    lines.push(action_line(
        "ov config validate",
        copy(
            language,
            "Check config, auth, and server reachability",
            "检查配置、认证和服务器连接",
        ),
    ));
    lines.push(action_line(
        "ov config",
        copy(language, "Edit or switch config", "编辑或切换配置"),
    ));
    lines.push(action_line(
        "ov health",
        copy(language, "Run a quick health check", "快速健康检查"),
    ));

    format!("{}\n", lines.join("\n"))
}

fn status_title(language: Language) -> String {
    theme::brand_title(copy(language, "OPENVIKING STATUS", "OPENVIKING 状态"))
        .bold()
        .to_string()
}

fn unknown(language: Language) -> &'static str {
    copy(language, "unknown", "未知")
}

fn kind_label(kind: ConfigKind, language: Language) -> &'static str {
    match language {
        Language::En => kind.label(),
        Language::ZhCn => match kind {
            ConfigKind::VolcengineCloud => "火山引擎云",
            ConfigKind::SelfManaged => "自托管",
        },
    }
}

fn section_title(title: &str) -> String {
    theme::heading(title).bold().to_string()
}

fn detail_line_styled(label: &str, value: String) -> String {
    let label = theme::muted(pad_to_display_width(label, LABEL_WIDTH));
    format!("  {label}{value}")
}

fn action_line(command: &str, description: &str) -> String {
    let command = theme::command(pad_to_display_width(command, ACTION_WIDTH)).bold();
    format!("  {command}{}", theme::muted(description))
}

fn active_config_value(name: &str, kind: &str) -> String {
    format!(
        "{} {}",
        theme::config_name(name).bold(),
        theme::strong(format!("({kind})"))
    )
}

fn path_value(value: &str) -> String {
    if value == "unknown" || value == "未知" {
        unknown_value(value)
    } else {
        theme::value(value).to_string()
    }
}

fn model_value(value: &str) -> String {
    if value == "unknown" || value == "未知" {
        unknown_value(value)
    } else {
        theme::value(value).bold().to_string()
    }
}

fn status_value(value: &str) -> String {
    theme::success(value).bold().to_string()
}

fn unhealthy_value(value: &str) -> String {
    theme::warning(value).bold().to_string()
}

fn error_value(value: &str) -> String {
    theme::error(value).bold().to_string()
}

fn plain_value(value: &str) -> String {
    theme::body(value).to_string()
}

fn unknown_value(value: &str) -> String {
    theme::muted(value).to_string()
}

fn activity_count_value(value: Option<u64>) -> String {
    match value {
        Some(0) => theme::body("0").to_string(),
        Some(value) => theme::warning(value.to_string()).bold().to_string(),
        None => unknown_value("unknown"),
    }
}

fn error_count_value(value: Option<u64>) -> String {
    match value {
        Some(0) => theme::success("0").to_string(),
        Some(value) => error_value(&value.to_string()),
        None => unknown_value("unknown"),
    }
}

fn component_header_line(language: Language) -> String {
    let component = theme::muted(pad_to_display_width(
        copy(language, "Component", "组件"),
        COMPONENT_WIDTH,
    ))
    .bold();
    let health = theme::muted(pad_to_display_width(
        copy(language, "Health", "健康"),
        HEALTH_WIDTH,
    ))
    .bold();
    format!(
        "  {component}{health}{}",
        theme::muted(copy(language, "Summary", "摘要")).bold()
    )
}

fn component_line(
    component: &str,
    payload: &Value,
    queue: &QueueSummary,
    models: &ModelSummary,
    language: Language,
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

    let component = theme::command(pad_to_display_width(component, COMPONENT_WIDTH)).bold();
    let health = styled_health_cell(health, language);
    let summary = summary_value(&summary);
    format!("  {component}{health}{summary}")
}

fn styled_health_cell(health: &str, language: Language) -> String {
    let translated = match health {
        "healthy" => copy(language, "healthy", "健康"),
        "unhealthy" => copy(language, "unhealthy", "异常"),
        _ => unknown(language),
    };
    let cell = pad_to_display_width(translated, HEALTH_WIDTH);
    match health {
        "healthy" => theme::success(cell).bold().to_string(),
        "unhealthy" => theme::warning(cell).bold().to_string(),
        _ => theme::muted(cell).to_string(),
    }
}

fn summary_value(summary: &str) -> String {
    if summary == "unknown" {
        unknown_value(summary)
    } else {
        theme::body(summary).to_string()
    }
}

fn pad_to_display_width(value: &str, width: usize) -> String {
    format!(
        "{}{}",
        value,
        " ".repeat(width.saturating_sub(UnicodeWidthStr::width(value)))
    )
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
