//! Wizard header, connection status, model discovery, and status box rendering.

use super::prompts::{clear_live_region, live_region_columns};
use crate::base_client::BaseClient;
use crate::config::Config;
use crate::config_wizard::store::{ConfigEntry, ConfigKind, ConfigStore};
use crate::error::Result;
use crate::i18n::{Language, copy};
use crate::terminal_ui::{RenderedRegion, display_width};
use crate::theme::{self, Rgb};
use colored::Colorize;
use crossterm::terminal;
use serde_json::Value;
use std::env;
use std::io::{self, Write};
use std::path::Path;
use unicode_width::UnicodeWidthChar;

const HEADER_TAGLINE: &str = "Context Database for AI Agents";

const HEADER_TAGLINE_ZH: &str = "AI Agent 上下文数据库";

const STATUS_BOX_PROBE_TIMEOUT_SECS: f64 = 3.0;

// The OpenViking mark (one sail + one slash, read as `//`), rasterized from the
// brand kit's 24-grid mark (`mark-24-*.svg`, the size the brand manual assigns
// to terminals) into braille: each cell is 2x4 dots, so with a ~1:2 terminal
// cell every dot is square and the mark keeps its proportions. The ink spans
// 12 rows (48 dots, 8/3 dots per grid unit); E tapered slash uses 25% coverage so its fine tip survives; a dot is inked when a quarter
// of it falls inside the mark. Blank first and last rows keep the art centered
// against the status details.
const OV_LOGO_LINES: [&str; 14] = [
    "",
    "                     ⢀⡔   ⣰⡇",
    "                    ⣠⠏  ⢀⣼⣿⡇",
    "                  ⣠⡾⠃  ⢠⣾⣿⣿⡇",
    "                ⢀⣾⡟⠁  ⣰⣿⣿⣿⣿⡇",
    "              ⢀⣴⣿⠏  ⢀⣼⣿⣿⣿⣿⣿⡇",
    "             ⣴⣿⡿⠃  ⢠⣾⣿⣿⣿⣿⣿⣿⡇",
    "           ⣠⣾⣿⡟⠁  ⣰⣿⣿⣿⣿⣿⣿⣿⣿⡇",
    "         ⣠⣾⣿⣿⠏  ⢀⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇",
    "       ⢀⣴⣿⣿⡿⠃  ⢠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇",
    "     ⢀⣴⣿⣿⣿⡟⠁  ⣰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇",
    "    ⣠⣿⣿⣿⣿⠏  ⢀⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇",
    "  ⣠⣾⣿⣿⣿⡿⠃  ⢠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇",
    "",
];

// Full mode needs enough height for the standalone wordmark, logo art,
// status box, and prompts without wrapping into each other.
const FULL_STATUS_BOX_MIN_ROWS: u16 = 30;

pub(crate) fn wizard_header_lines() -> Vec<String> {
    wordmark_lines()
        .into_iter()
        .map(str::to_string)
        .chain(std::iter::once(String::new()))
        .collect()
}

pub(crate) fn wordmark_width() -> usize {
    wordmark_lines()
        .iter()
        .map(|line| display_width(line))
        .max()
        .unwrap_or_default()
}

fn wordmark_lines() -> [&'static str; 6] {
    [
        " ██████╗ ██████╗ ███████╗███╗   ██╗██╗   ██╗ ██╗ ██╗  ██╗ ██╗ ███╗   ██╗ ██████╗ ",
        "██╔═══██╗██╔══██╗██╔════╝████╗  ██║██║   ██║ ██║ ██║ ██╔╝ ██║ ████╗  ██║██╔════╝ ",
        "██║   ██║██████╔╝█████╗  ██╔██╗ ██║██║   ██║ ██║ █████╔╝  ██║ ██╔██╗ ██║██║  ███╗",
        "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚██╗ ██╔╝ ██║ ██╔═██╗  ██║ ██║╚██╗██║██║   ██║",
        "╚██████╔╝██║     ███████╗██║ ╚████║ ╚████╔╝  ██║ ██║  ██╗ ██║ ██║ ╚████║╚██████╔╝",
        " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝  ╚═══╝   ╚═╝ ╚═╝  ╚═╝ ╚═╝ ╚═╝  ╚═══╝ ╚═════╝ ",
    ]
}

fn header_version_text() -> String {
    format!("v{}", env!("OPENVIKING_CLI_VERSION"))
}

pub(super) fn status_box_width() -> usize {
    preferred_status_box_width()
}

const COMPACT_STATUS_DETAIL_WIDTH: usize = 52;

fn preferred_status_box_width() -> usize {
    let title_width = 4 + display_width(status_box_title(StatusBoxMode::Full));

    wordmark_width().max(title_width)
}

fn compact_status_box_width() -> usize {
    let content_width = 4 + COMPACT_STATUS_DETAIL_WIDTH;
    let title_width = 4 + display_width(status_box_title(StatusBoxMode::Compact));

    content_width.max(title_width)
}

fn status_box_title(mode: StatusBoxMode) -> &'static str {
    match mode {
        StatusBoxMode::Full => copy(Language::current(), HEADER_TAGLINE, HEADER_TAGLINE_ZH),
        StatusBoxMode::Compact => copy(
            Language::current(),
            "OpenViking | Context Database for AI Agents",
            "OpenViking | AI Agent 上下文数据库",
        ),
    }
}

pub(super) fn live_status_box_frame() -> StatusBoxFrame {
    match terminal::size() {
        Ok((columns, rows)) if columns > 4 => status_box_frame_for_size(columns, rows),
        _ => StatusBoxFrame {
            width: preferred_status_box_width(),
            mode: StatusBoxMode::Full,
        },
    }
}

fn status_box_frame_for_size(columns: u16, rows: u16) -> StatusBoxFrame {
    let available = usize::from(columns).saturating_sub(1);
    let preferred = preferred_status_box_width();
    if rows >= FULL_STATUS_BOX_MIN_ROWS && available >= preferred {
        return StatusBoxFrame {
            width: preferred.min(available),
            mode: StatusBoxMode::Full,
        };
    }

    StatusBoxFrame {
        width: compact_status_box_width().min(available),
        mode: StatusBoxMode::Compact,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct StatusBoxFrame {
    width: usize,
    pub(super) mode: StatusBoxMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum StatusBoxMode {
    Full,
    Compact,
}

pub(super) fn compact_kind_label(kind: ConfigKind) -> &'static str {
    match Language::current() {
        Language::En => kind.compact_label(),
        Language::ZhCn => match kind {
            ConfigKind::OpenVikingService => "OpenViking 服务",
            ConfigKind::Custom => "自定义",
        },
    }
}

pub(super) fn print_full_header(mode: StatusBoxMode) {
    if mode != StatusBoxMode::Full {
        return;
    }

    let lines = wizard_header_lines();
    println!();
    for (index, line) in lines.iter().take(wordmark_lines().len()).enumerate() {
        println!("{}", styled_wordmark_line(index, line));
    }
    println!();
}

fn styled_wordmark_line(_index: usize, line: &str) -> String {
    styled_wordmark_line_for_color_level(line, theme::terminal_color_level())
}

fn styled_wordmark_line_for_color_level(line: &str, color_level: theme::ColorLevel) -> String {
    theme::style_theme_color_for_level(line, theme::active_theme().wordmark, true, color_level)
}

fn tagline_texture_color(column: usize, width: usize) -> Rgb {
    tagline_color_for_theme(theme::active_theme(), column, width)
}

// Solid deep teal is readable on both light and dark terminal backgrounds.
fn tagline_color_for_theme(palette: theme::CliTheme, _column: usize, _width: usize) -> Rgb {
    palette.brand_title.rgb_fallback()
}

fn styled_tagline(text: &str) -> String {
    styled_tagline_for_color_level(text, theme::terminal_color_level())
}

fn styled_tagline_for_color_level(text: &str, color_level: theme::ColorLevel) -> String {
    let width = display_width(text).max(1);
    let mut rendered = String::new();
    let mut column = 0usize;

    for ch in text.chars() {
        if ch.is_whitespace() {
            rendered.push(ch);
        } else {
            let rgb = header_display_rgb(tagline_texture_color(column, width), color_level);
            rendered.push_str(&theme::style_rgb_for_level(
                ch.to_string(),
                rgb,
                true,
                color_level,
            ));
        }
        column += UnicodeWidthChar::width(ch).unwrap_or(0);
    }

    rendered
}

pub(super) async fn print_status_box(store: &ConfigStore) -> Result<()> {
    let configs = store.list_configs()?;
    let active = store.load_active()?;
    let config_home = display_config_home(store);
    let Some(active_config) = active.as_ref() else {
        print_status_box_with_runtime(
            active.as_ref(),
            &configs,
            &config_home,
            &StatusBoxRuntime::not_configured(),
        )?;
        return Ok(());
    };

    let rendered_region = print_status_box_with_runtime(
        active.as_ref(),
        &configs,
        &config_home,
        &StatusBoxRuntime::checking(),
    )?;
    let runtime = status_box_runtime(Some(active_config)).await;
    clear_rendered_region(&rendered_region, false)?;
    print_status_box_with_runtime(active.as_ref(), &configs, &config_home, &runtime)?;

    Ok(())
}

fn print_status_box_with_runtime(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
    runtime: &StatusBoxRuntime,
) -> Result<RenderedRegion> {
    let frame = live_status_box_frame();
    let lines = styled_status_box_lines_with_runtime(active, configs, config_home, runtime, frame);
    let render_columns = live_region_columns();

    for line in &lines {
        println!("{line}");
    }
    io::stdout().flush()?;
    Ok(RenderedRegion::from_lines(&lines, render_columns))
}

fn clear_rendered_region(region: &RenderedRegion, cursor_on_last_line: bool) -> io::Result<()> {
    clear_live_region(
        region.rows_to_clear(live_region_columns()),
        cursor_on_last_line,
    )
}

#[cfg(test)]
pub(crate) fn status_box_lines(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
) -> Vec<String> {
    status_box_lines_with_runtime(active, configs, config_home, &StatusBoxRuntime::unknown())
}

#[cfg(test)]
fn status_box_lines_with_runtime(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
    runtime: &StatusBoxRuntime,
) -> Vec<String> {
    status_box_lines_with_runtime_width(
        active,
        configs,
        config_home,
        runtime,
        StatusBoxFrame {
            width: status_box_width(),
            mode: StatusBoxMode::Full,
        },
    )
}

#[cfg(test)]
fn status_box_lines_with_runtime_width(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
    runtime: &StatusBoxRuntime,
    frame: StatusBoxFrame,
) -> Vec<String> {
    let details = status_box_details(active, configs, config_home, runtime);
    let right_rows = status_box_right_rows(details, frame.mode);
    let rows = status_box_row_count(right_rows.len(), frame.mode);
    let mut lines = Vec::with_capacity(rows + 2);

    lines.push(box_title_line(status_box_title(frame.mode), frame.width));
    for index in 0..rows {
        lines.push(box_content_line(
            status_box_logo_line(index, frame.mode),
            right_rows
                .get(index)
                .map(|row| row.plain(frame.width, frame.mode))
                .unwrap_or_default()
                .as_str(),
            frame.width,
            frame.mode,
        ));
    }
    lines.push(box_footer_line(&header_version_text(), frame.width));
    lines
}

fn styled_status_box_lines_with_runtime(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
    runtime: &StatusBoxRuntime,
    frame: StatusBoxFrame,
) -> Vec<String> {
    let details = status_box_details(active, configs, config_home, runtime);
    let right_rows = status_box_right_rows(details, frame.mode);
    let rows = status_box_row_count(right_rows.len(), frame.mode);
    let mut lines = Vec::with_capacity(rows + 4);

    lines.push(String::new());
    lines.push(styled_box_title_line(
        status_box_title(frame.mode),
        frame.width,
    ));
    for index in 0..rows {
        lines.push(styled_box_content_line(
            status_box_logo_line(index, frame.mode),
            right_rows.get(index).unwrap_or(&StatusBoxRightRow::Empty),
            frame.width,
            index,
            frame.mode,
        ));
    }
    lines.push(styled_box_footer_line(&header_version_text(), frame.width));
    lines.push(String::new());
    lines
}

fn status_box_row_count(right_rows: usize, mode: StatusBoxMode) -> usize {
    match mode {
        StatusBoxMode::Full => OV_LOGO_LINES.len().max(right_rows),
        StatusBoxMode::Compact => right_rows,
    }
}

fn status_box_logo_line(index: usize, mode: StatusBoxMode) -> &'static str {
    status_box_logo_lines(mode)
        .get(index)
        .copied()
        .unwrap_or("")
}

fn status_box_logo_lines(mode: StatusBoxMode) -> &'static [&'static str] {
    match mode {
        StatusBoxMode::Full => &OV_LOGO_LINES,
        StatusBoxMode::Compact => &[],
    }
}

async fn status_box_runtime(active: Option<&Config>) -> StatusBoxRuntime {
    let Some(config) = active else {
        return StatusBoxRuntime::not_configured();
    };

    let auth = config.effective_auth(false);
    let client = BaseClient::new(
        config.url.clone(),
        auth.api_key,
        auth.account,
        auth.user,
        config.actor_peer_id.clone(),
        STATUS_BOX_PROBE_TIMEOUT_SECS,
        config.profile,
        config.effective_extra_headers(),
    )
    .with_gateway_token(config.effective_gateway_token());

    match client.get::<Value>("/api/v1/system/status", &[]).await {
        Ok(status) => {
            let healthy = status_payload_is_healthy(&status);
            let runtime = StatusBoxRuntime::connected(healthy, None, None)
                .with_missing_models(extract_models_from_status_payload(&status));

            if runtime.vlm_model.is_some() && runtime.embedding_model.is_some() {
                runtime
            } else {
                runtime.with_missing_models(fetch_observer_models(&client).await)
            }
        }
        Err(_) => match client.get::<Value>("/health", &[]).await {
            Ok(health) => {
                let healthy = health
                    .get("healthy")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                StatusBoxRuntime::connected(healthy, None, None)
                    .with_missing_models(fetch_observer_models(&client).await)
            }
            Err(_) => StatusBoxRuntime::unreachable(),
        },
    }
}

fn status_payload_is_healthy(value: &Value) -> bool {
    status_payload_health(value).unwrap_or(true)
}

fn status_payload_health(value: &Value) -> Option<bool> {
    if let Some(healthy) = value
        .get("is_healthy")
        .or_else(|| value.get("healthy"))
        .and_then(Value::as_bool)
    {
        return Some(healthy);
    }

    let components = value.get("components").and_then(Value::as_object)?;

    if components.is_empty() {
        return None;
    }

    Some(components.values().all(|component| {
        component.get("is_healthy").and_then(Value::as_bool) != Some(false)
            && component.get("has_errors").and_then(Value::as_bool) != Some(true)
    }))
}

async fn fetch_observer_models(client: &BaseClient) -> (Option<String>, Option<String>) {
    client
        .get::<Value>("/api/v1/observer/models", &[])
        .await
        .map(|models| extract_models_from_status_payload(&models))
        .unwrap_or((None, None))
}

fn extract_models_from_status_payload(value: &Value) -> (Option<String>, Option<String>) {
    let status_text = value
        .pointer("/components/models/status")
        .and_then(Value::as_str)
        .or_else(|| value.get("status").and_then(Value::as_str));

    status_text
        .map(extract_models_from_status_text)
        .unwrap_or((None, None))
}

fn extract_models_from_status_text(status: &str) -> (Option<String>, Option<String>) {
    (
        extract_first_model_after_heading(status, "VLM Models:"),
        extract_first_model_after_heading(status, "Embedding Models:"),
    )
}

fn extract_first_model_after_heading(status: &str, heading: &str) -> Option<String> {
    let (_, section) = status.split_once(heading)?;

    for line in section.lines() {
        let trimmed = line.trim();
        if trimmed.ends_with("Models:") && trimmed != heading {
            break;
        }
        if !trimmed.starts_with('|') {
            continue;
        }

        let cells = trimmed
            .trim_matches('|')
            .split('|')
            .map(str::trim)
            .collect::<Vec<_>>();
        let model = cells.first().copied().unwrap_or_default();
        if model.is_empty() || model.eq_ignore_ascii_case("model") {
            continue;
        }

        return Some(model.to_string());
    }

    None
}

pub(crate) fn display_config_home(store: &ConfigStore) -> String {
    let Some(home) = env::var_os("HOME") else {
        return store.config_dir().display().to_string();
    };
    display_config_home_for_home(store.config_dir(), Path::new(&home))
}

fn display_config_home_for_home(config_dir: &Path, home: &Path) -> String {
    match config_dir.strip_prefix(home) {
        Ok(relative) if relative.as_os_str().is_empty() => "~".to_string(),
        Ok(relative) => format!("~/{}", relative.display()),
        Err(_) => config_dir.display().to_string(),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct StatusBoxRuntime {
    connection: RuntimeConnectionStatus,
    vlm_model: Option<String>,
    embedding_model: Option<String>,
}

impl StatusBoxRuntime {
    fn checking() -> Self {
        Self {
            connection: RuntimeConnectionStatus::Checking,
            vlm_model: None,
            embedding_model: None,
        }
    }

    fn not_configured() -> Self {
        Self {
            connection: RuntimeConnectionStatus::NotConfigured,
            vlm_model: None,
            embedding_model: None,
        }
    }

    #[cfg(test)]
    fn unknown() -> Self {
        Self {
            connection: RuntimeConnectionStatus::Unknown,
            vlm_model: None,
            embedding_model: None,
        }
    }

    fn connected(
        healthy: bool,
        vlm_model: Option<String>,
        embedding_model: Option<String>,
    ) -> Self {
        Self {
            connection: if healthy {
                RuntimeConnectionStatus::ConnectedHealthy
            } else {
                RuntimeConnectionStatus::ConnectedUnhealthy
            },
            vlm_model,
            embedding_model,
        }
    }

    fn unreachable() -> Self {
        Self {
            connection: RuntimeConnectionStatus::Unreachable,
            vlm_model: None,
            embedding_model: None,
        }
    }

    fn with_missing_models(mut self, models: (Option<String>, Option<String>)) -> Self {
        if self.vlm_model.is_none() {
            self.vlm_model = models.0;
        }
        if self.embedding_model.is_none() {
            self.embedding_model = models.1;
        }
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum StatusBoxRightRow {
    Detail(StatusBoxDetail),
    Empty,
}

#[cfg(test)]
impl StatusBoxRightRow {
    fn plain(&self, _width: usize, _mode: StatusBoxMode) -> String {
        match self {
            Self::Detail(detail) => detail.plain(),
            Self::Empty => String::new(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RuntimeConnectionStatus {
    Checking,
    NotConfigured,
    ConnectedHealthy,
    ConnectedUnhealthy,
    Unreachable,
    #[cfg(test)]
    Unknown,
}

impl RuntimeConnectionStatus {
    fn plain(self) -> &'static str {
        match Language::current() {
            Language::En => match self {
                Self::Checking => "Checking...",
                Self::NotConfigured => "Not configured",
                Self::ConnectedHealthy => "Connected (Healthy)",
                Self::ConnectedUnhealthy => "Connected (Unhealthy)",
                Self::Unreachable => "Unreachable",
                #[cfg(test)]
                Self::Unknown => "Unknown",
            },
            Language::ZhCn => match self {
                Self::Checking => "检查中...",
                Self::NotConfigured => "未配置",
                Self::ConnectedHealthy => "已连接（健康）",
                Self::ConnectedUnhealthy => "已连接（不健康）",
                Self::Unreachable => "无法连接",
                #[cfg(test)]
                Self::Unknown => "未知",
            },
        }
    }

    fn styled(self) -> String {
        match self {
            Self::Checking => theme::value(self.plain()).bold().to_string(),
            Self::ConnectedHealthy => theme::success(self.plain()).bold().to_string(),
            Self::ConnectedUnhealthy => theme::warning(self.plain()).bold().to_string(),
            Self::Unreachable => theme::error(self.plain()).bold().to_string(),
            Self::NotConfigured => theme::muted(self.plain()).to_string(),
            #[cfg(test)]
            Self::Unknown => theme::muted(self.plain()).to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum StatusBoxDetail {
    Active { name: String, kind: Option<String> },
    Status { connection: RuntimeConnectionStatus },
    Model { label: &'static str, value: String },
    Saved { count: String },
    Home { path: String },
}

impl StatusBoxDetail {
    fn plain(&self) -> String {
        match self {
            Self::Active { name, kind } => match kind {
                Some(kind) => format!("{} {name} {kind}", status_label("Active:")),
                None => format!("{} {name}", status_label("Active:")),
            },
            Self::Status { connection } => {
                format!("{} {}", status_label("Status:"), connection.plain())
            }
            Self::Model { label, value } => format!("{label}: {value}"),
            Self::Saved { count } => format!("{} {count}", status_label("Saved configs:")),
            Self::Home { path } => format!("{} {path}", status_label("Config home:")),
        }
    }

    fn styled(&self) -> String {
        match self {
            Self::Active { name, kind } => {
                let mut rendered = format!(
                    "{} {}",
                    theme::muted(status_label("Active:")),
                    theme::config_name(name).bold()
                );
                if let Some(kind) = kind {
                    rendered.push(' ');
                    rendered.push_str(&theme::strong(kind).to_string());
                }
                rendered
            }
            Self::Status { connection } => {
                format!(
                    "{} {}",
                    theme::muted(status_label("Status:")),
                    connection.styled()
                )
            }
            Self::Model { label, value } => {
                let value = if value == unknown_copy() {
                    theme::muted(value).to_string()
                } else {
                    theme::sky_value(value).bold().to_string()
                };
                format!("{} {}", theme::muted(format!("{label}:")), value)
            }
            Self::Saved { count } => {
                format!(
                    "{} {}",
                    theme::muted(status_label("Saved configs:")),
                    theme::strong(count)
                )
            }
            Self::Home { path } => {
                format!(
                    "{} {}",
                    theme::muted(status_label("Config home:")),
                    theme::sky_value(path).bold()
                )
            }
        }
    }
}

fn status_label(label: &'static str) -> &'static str {
    match (Language::current(), label) {
        (Language::ZhCn, "Active:") => "当前配置：",
        (Language::ZhCn, "Status:") => "状态：",
        (Language::ZhCn, "Saved configs:") => "已保存配置：",
        (Language::ZhCn, "Config home:") => "配置目录：",
        _ => label,
    }
}

fn status_box_details(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
    runtime: &StatusBoxRuntime,
) -> Vec<StatusBoxDetail> {
    let summary = active_summary_lines(active, configs);
    let active = summary
        .first()
        .and_then(|line| active_summary_render_parts(line))
        .map(|parts| StatusBoxDetail::Active {
            name: parts.name,
            kind: parts.kind,
        })
        .unwrap_or(StatusBoxDetail::Active {
            name: "none".to_string(),
            kind: None,
        });
    let saved = summary
        .get(1)
        .and_then(|line| saved_summary_render_parts(line))
        .map(|parts| StatusBoxDetail::Saved { count: parts.count })
        .unwrap_or(StatusBoxDetail::Saved {
            count: "0".to_string(),
        });

    vec![
        active,
        StatusBoxDetail::Status {
            connection: runtime.connection,
        },
        StatusBoxDetail::Model {
            label: "VLM",
            value: runtime
                .vlm_model
                .clone()
                .unwrap_or_else(|| model_placeholder(runtime.connection)),
        },
        StatusBoxDetail::Model {
            label: "Embedding",
            value: runtime
                .embedding_model
                .clone()
                .unwrap_or_else(|| model_placeholder(runtime.connection)),
        },
        saved,
        StatusBoxDetail::Home {
            path: config_home.to_string(),
        },
    ]
}

fn model_placeholder(connection: RuntimeConnectionStatus) -> String {
    if connection == RuntimeConnectionStatus::Checking {
        copy(Language::current(), "Checking...", "检查中...").to_string()
    } else {
        unknown_copy().to_string()
    }
}

fn unknown_copy() -> &'static str {
    copy(Language::current(), "Unknown", "未知")
}

fn status_box_right_rows(
    details: Vec<StatusBoxDetail>,
    mode: StatusBoxMode,
) -> Vec<StatusBoxRightRow> {
    let rows = details
        .into_iter()
        .map(StatusBoxRightRow::Detail)
        .collect::<Vec<_>>();

    match mode {
        StatusBoxMode::Full => center_status_box_rows(rows, OV_LOGO_LINES.len()),
        StatusBoxMode::Compact => rows,
    }
}

fn center_status_box_rows(
    rows: Vec<StatusBoxRightRow>,
    target_rows: usize,
) -> Vec<StatusBoxRightRow> {
    if rows.len() >= target_rows {
        return rows;
    }

    let empty_rows = target_rows - rows.len();
    let top = empty_rows / 2;
    let bottom = empty_rows - top;
    let mut centered = Vec::with_capacity(target_rows);
    centered.extend(std::iter::repeat_n(StatusBoxRightRow::Empty, top));
    centered.extend(rows);
    centered.extend(std::iter::repeat_n(StatusBoxRightRow::Empty, bottom));
    centered
}

#[cfg(test)]
fn box_title_line(title: &str, width: usize) -> String {
    let inner_width = width.saturating_sub(2);
    if title.trim().is_empty() {
        return format!("╭{}╮", "─".repeat(inner_width));
    }

    let title = format!(" {title} ");
    let visible_title = truncate_to_width(&title, inner_width);
    let title_width = display_width(&visible_title);
    let left = inner_width.saturating_sub(title_width) / 2;
    let right = inner_width.saturating_sub(title_width + left);

    format!(
        "╭{}{}{}╮",
        "─".repeat(left),
        visible_title,
        "─".repeat(right)
    )
}

#[cfg(test)]
fn box_footer_line(title: &str, width: usize) -> String {
    let inner_width = width.saturating_sub(2);
    let title = format!(" {title} ");
    let visible_title = truncate_to_width(&title, inner_width);
    let title_width = display_width(&visible_title);
    let right = inner_width.saturating_sub(title_width).min(5);
    let left = inner_width.saturating_sub(title_width + right);

    format!(
        "╰{}{}{}╯",
        "─".repeat(left),
        visible_title,
        "─".repeat(right)
    )
}

#[cfg(test)]
fn box_content_line(left: &str, right: &str, width: usize, mode: StatusBoxMode) -> String {
    let layout = status_box_content_layout(width, mode);
    format!(
        "│ {}{}{} │",
        pad_to_width(left, layout.logo_width),
        " ".repeat(layout.gutter),
        pad_to_width(right, layout.right_width)
    )
}

fn styled_box_title_line(title: &str, width: usize) -> String {
    let inner_width = width.saturating_sub(2);
    let border = theme::active_theme().border.rgb_fallback();

    if title.trim().is_empty() {
        return format!(
            "{}{}{}",
            theme::style_rgb("╭", border, false),
            theme::style_rgb("─".repeat(inner_width), border, false),
            theme::style_rgb("╮", border, false)
        );
    }

    let title = format!(" {title} ");
    let visible_title = truncate_to_width(&title, inner_width);
    let title_width = display_width(&visible_title);
    let left = inner_width.saturating_sub(title_width) / 2;
    let right = inner_width.saturating_sub(title_width + left);

    format!(
        "{}{}{}{}{}",
        theme::style_rgb("╭", border, false),
        theme::style_rgb("─".repeat(left), border, false),
        styled_tagline(&visible_title),
        theme::style_rgb("─".repeat(right), border, false),
        theme::style_rgb("╮", border, false)
    )
}

fn styled_box_footer_line(title: &str, width: usize) -> String {
    let border = theme::active_theme().border.rgb_fallback();
    let version = theme::active_theme().version.rgb_fallback();
    let inner_width = width.saturating_sub(2);
    let title = format!(" {title} ");
    let visible_title = truncate_to_width(&title, inner_width);
    let title_width = display_width(&visible_title);
    let right = inner_width.saturating_sub(title_width).min(5);
    let left = inner_width.saturating_sub(title_width + right);

    format!(
        "{}{}{}{}{}",
        theme::style_rgb("╰", border, false),
        theme::style_rgb("─".repeat(left), border, false),
        theme::style_rgb(&visible_title, version, true),
        theme::style_rgb("─".repeat(right), border, false),
        theme::style_rgb("╯", border, false)
    )
}

fn styled_box_content_line(
    left: &str,
    right: &StatusBoxRightRow,
    width: usize,
    logo_row: usize,
    mode: StatusBoxMode,
) -> String {
    let layout = status_box_content_layout(width, mode);
    let border = theme::active_theme().border.rgb_fallback();
    let logo_height = status_box_logo_lines(mode).len();
    format!(
        "{} {}{}{} {}",
        theme::style_rgb("│", border, false),
        styled_logo_to_width(left, layout.logo_width, logo_row, logo_height),
        " ".repeat(layout.gutter),
        styled_status_right_row_to_width(right, layout.right_width),
        theme::style_rgb("│", border, false)
    )
}

#[derive(Debug, Clone, Copy)]
struct StatusBoxContentLayout {
    logo_width: usize,
    gutter: usize,
    right_width: usize,
}

fn status_box_content_layout(width: usize, mode: StatusBoxMode) -> StatusBoxContentLayout {
    let inner_width = width.saturating_sub(4);
    if mode == StatusBoxMode::Compact {
        return StatusBoxContentLayout {
            logo_width: 0,
            gutter: 0,
            right_width: inner_width,
        };
    }

    let preferred_logo_width = match mode {
        StatusBoxMode::Full => ov_logo_width(),
        StatusBoxMode::Compact => 0,
    };
    let preferred_gutter = 3usize;

    if inner_width <= preferred_logo_width {
        return StatusBoxContentLayout {
            logo_width: inner_width,
            gutter: 0,
            right_width: 0,
        };
    }

    let room_after_logo = inner_width.saturating_sub(preferred_logo_width);
    let gutter = preferred_gutter.min(room_after_logo.saturating_sub(1));
    let logo_width = preferred_logo_width.min(inner_width.saturating_sub(gutter));
    let right_width = inner_width.saturating_sub(logo_width + gutter);

    StatusBoxContentLayout {
        logo_width,
        gutter,
        right_width,
    }
}

fn styled_logo_to_width(line: &str, width: usize, row: usize, logo_height: usize) -> String {
    styled_logo_to_width_with_height(line, width, row, logo_height, theme::terminal_color_level())
}

#[cfg(test)]
fn styled_logo_to_width_for_color_level(
    line: &str,
    width: usize,
    row: usize,
    color_level: theme::ColorLevel,
) -> String {
    styled_logo_to_width_with_height(line, width, row, OV_LOGO_LINES.len(), color_level)
}

fn styled_logo_to_width_with_height(
    line: &str,
    width: usize,
    row: usize,
    logo_height: usize,
    color_level: theme::ColorLevel,
) -> String {
    let mut rendered = String::new();
    let visible = truncate_to_width(line, width);

    for (column, ch) in visible.chars().enumerate() {
        if ch.is_whitespace() {
            rendered.push(ch);
        } else {
            let rgb = header_display_rgb(
                logo_glass_color(ch, column, row, width.max(1), logo_height),
                color_level,
            );
            rendered.push_str(&theme::style_rgb_for_level(
                ch.to_string(),
                rgb,
                true,
                color_level,
            ));
        }
    }
    rendered.push_str(&" ".repeat(width.saturating_sub(display_width(&visible))));
    rendered
}

fn header_display_rgb(rgb: Rgb, color_level: theme::ColorLevel) -> Rgb {
    if matches!(color_level, theme::ColorLevel::TrueColor) {
        rgb
    } else {
        theme::active_theme().border.rgb_fallback()
    }
}

fn logo_glass_color(_ch: char, column: usize, row: usize, width: usize, logo_height: usize) -> Rgb {
    logo_glass_color_for_theme_with_height(theme::active_theme(), column, row, width, logo_height)
}

#[cfg(test)]
fn logo_glass_color_for_theme(
    palette: theme::CliTheme,
    column: usize,
    row: usize,
    width: usize,
) -> Rgb {
    logo_glass_color_for_theme_with_height(palette, column, row, width, OV_LOGO_LINES.len())
}

fn logo_glass_color_for_theme_with_height(
    palette: theme::CliTheme,
    _column: usize,
    _row: usize,
    _width: usize,
    _logo_height: usize,
) -> Rgb {
    palette.mark
}

fn styled_detail_to_width(detail: &StatusBoxDetail, width: usize) -> String {
    let plain = truncate_to_width(&detail.plain(), width);
    let styled = if plain == detail.plain() {
        detail.styled()
    } else {
        theme::muted(&plain).to_string()
    };
    format!(
        "{}{}",
        styled,
        " ".repeat(width.saturating_sub(display_width(&plain)))
    )
}

fn styled_status_right_row_to_width(row: &StatusBoxRightRow, width: usize) -> String {
    match row {
        StatusBoxRightRow::Detail(detail) => styled_detail_to_width(detail, width),
        StatusBoxRightRow::Empty => " ".repeat(width),
    }
}

fn ov_logo_width() -> usize {
    OV_LOGO_LINES
        .iter()
        .map(|line| display_width(line))
        .max()
        .unwrap_or_default()
}

#[cfg(test)]
fn pad_to_width(text: &str, width: usize) -> String {
    let truncated = truncate_to_width(text, width);
    format!(
        "{}{}",
        truncated,
        " ".repeat(width.saturating_sub(display_width(&truncated)))
    )
}

fn truncate_to_width(text: &str, width: usize) -> String {
    if display_width(text) <= width {
        return text.to_string();
    }
    if width == 0 {
        return String::new();
    }
    if width == 1 {
        return "…".to_string();
    }
    let mut used = 0usize;
    let mut truncated = String::new();
    let target_width = width.saturating_sub(display_width("…"));
    for ch in text.chars() {
        let ch_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + ch_width > target_width {
            break;
        }
        truncated.push(ch);
        used += ch_width;
    }
    truncated.push('…');
    truncated
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ActiveSummaryRenderParts {
    pub(crate) label: &'static str,
    pub(crate) name: String,
    pub(crate) kind: Option<String>,
}

pub(crate) fn active_summary_render_parts(line: &str) -> Option<ActiveSummaryRenderParts> {
    let value = line.strip_prefix("Active: ")?;
    let (name, kind) = match value.split_once(" (") {
        Some((name, kind_tail)) if kind_tail.ends_with(')') => {
            (name.to_string(), Some(format!("({kind_tail}")))
        }
        _ => (value.to_string(), None),
    };

    Some(ActiveSummaryRenderParts {
        label: "Active:",
        name,
        kind,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SavedSummaryRenderParts {
    pub(crate) label: &'static str,
    pub(crate) count: String,
}

pub(crate) fn saved_summary_render_parts(line: &str) -> Option<SavedSummaryRenderParts> {
    Some(SavedSummaryRenderParts {
        label: "Saved configs:",
        count: line.strip_prefix("Saved configs: ")?.to_string(),
    })
}

pub(crate) fn active_summary_lines(
    active: Option<&Config>,
    configs: &[ConfigEntry],
) -> Vec<String> {
    let active_line = match active {
        Some(config) => {
            if let Some(entry) = configs.iter().find(|entry| entry.is_active) {
                format!(
                    "Active: {} ({})",
                    entry.name,
                    compact_kind_label(entry.kind)
                )
            } else {
                format!(
                    "Active: unnamed ({})",
                    compact_kind_label(ConfigKind::from_config(config))
                )
            }
        }
        None => "Active: none".to_string(),
    };

    vec![active_line, format!("Saved configs: {}", configs.len())]
}

#[cfg(test)]
#[path = "tests/status.rs"]
mod tests;
