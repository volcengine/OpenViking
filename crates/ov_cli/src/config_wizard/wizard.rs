use std::{
    env,
    io::{self, Write},
    path::Path,
};

use colored::Colorize;
use crossterm::{
    ExecutableCommand,
    cursor::{Hide, MoveToColumn, MoveUp, Show},
    event::{self, Event, KeyCode, KeyModifiers},
    terminal::{Clear, ClearType, disable_raw_mode, enable_raw_mode},
};
use uuid::Uuid;

use crate::{
    base_client::BaseClient,
    config::Config,
    error::{Error, Result},
};
use serde_json::Value;

use super::store::{
    ApiKeyRole, ConfigDraft, ConfigEntry, ConfigKind, ConfigStore, VOLCENGINE_CLOUD_URL,
    build_config, self_managed_allows_empty_api_key, validate_candidate_config,
    validate_candidate_config_with_role, validate_config_name, validation_error_copy,
};

const VOLCENGINE_API_KEY_URL: &str =
    "https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing";
const SELF_MANAGED_DEFAULT_URL: &str = "http://127.0.0.1:1933";
const NAV_HINT: &str = "↑/↓ choose · Enter select · Esc back · Ctrl+C exit";
const INPUT_HINT: &str = "Enter continue · Esc back · Ctrl+C exit";
const HEADER_TAGLINE: &str = "Context Database for AI Agents";
const WORDMARK_GRADIENT_START: Rgb = Rgb(234, 253, 247);
const WORDMARK_GRADIENT_MID: Rgb = Rgb(50, 214, 196);
const WORDMARK_GRADIENT_END: Rgb = Rgb(7, 95, 100);
const LOGO_GRADIENT_END: Rgb = Rgb(4, 61, 66);
const TAGLINE_ICE_START: Rgb = Rgb(234, 253, 247);
const TAGLINE_ICE_MID: Rgb = Rgb(50, 214, 196);
const TAGLINE_ICE_END: Rgb = Rgb(7, 95, 100);
const BOX_BORDER: Rgb = Rgb(50, 214, 196);
const VERSION_ACCENT: Rgb = Rgb(50, 214, 196);
const STATUS_BOX_PROBE_TIMEOUT_SECS: f64 = 1.5;

#[derive(Clone, Copy, PartialEq, Eq)]
enum IdentityMode {
    LocalNoKey,
    RootKey,
}

const OV_LOGO_LINES: [&str; 14] = [
    "",
    "             ⢻⣶⣄",
    "             ⠈⣿⣿⣟⢦⡀",
    "              ⣿⣿⣿⡌⢻⣦⡀",
    "              ⣿⣿⣿⣧ ⠹⣿⣦⡀",
    "             ⢀⣿⣿⣿⣿  ⢹⣿⣷⡀",
    "             ⣼⣿⣿⣿⡟  ⠈⣿⣿⣷",
    "           ⢀⣼⣿⣿⣿⣿⣁⣀⣤⣤⣿⣿⣿⡄  ⡀",
    "          ⢀⣾⡿⠿⠛⢛⣿⣿⣿⣿⣿⣿⣿⣿⡇⢀⣼⠃",
    "             ⢀⣰⣿⣿⣿⣿⣿⠿⠟⠛⠋⣡⣿⠇",
    "   ⠠⣶⣾⣿⣿⣿⣶⣤⣀ ⠾⠟⠛⠉⠉   ⣀⣤⣾⡿⠃",
    "     ⠙⢿⣿⣿⣿⣿⣿⣿⣷⣶⣶⣶⣶⣶⣿⣿⣿⡿⠟⠁",
    "       ⠈⠛⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠛⠉",
    "            ⠈⠉⠉⠉⠁",
];

pub async fn run_config_wizard() -> Result<()> {
    let store = ConfigStore::new()?;
    run_config_wizard_with_store(store).await
}

async fn run_config_wizard_with_store(store: ConfigStore) -> Result<()> {
    print_header();
    print_status_box(&store).await?;
    let mut ui = LiveRegion::default();

    loop {
        match prompt_select(
            &mut ui,
            "What would you like to configure?",
            "Choose action",
            &main_action_labels(),
            0,
            &[],
        )? {
            PromptResult::Value(0) => {
                if run_add_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(1) => {
                if run_edit_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(2) => {
                if run_delete_config(&store, &mut ui)? {
                    return Ok(());
                }
            }
            PromptResult::Back | PromptResult::Quit => {
                print_cancelled(&mut ui)?;
                return Ok(());
            }
            PromptResult::Value(_) => unreachable!("selection is constrained by action list"),
        }
    }
}

pub(crate) fn wizard_header_lines() -> Vec<String> {
    let mut lines: Vec<String> = wordmark_lines()
        .iter()
        .map(|line| (*line).to_string())
        .collect();
    lines.push(String::new());
    lines
}

pub(crate) fn wordmark_width() -> usize {
    wordmark_lines()
        .iter()
        .map(|line| line.chars().count())
        .max()
        .unwrap_or_default()
}

fn wordmark_lines() -> [&'static str; 6] {
    [
        " ██████╗ ██████╗ ███████╗███╗   ██╗██╗   ██╗██╗██╗  ██╗██╗███╗   ██╗ ██████╗ ",
        "██╔═══██╗██╔══██╗██╔════╝████╗  ██║██║   ██║██║██║ ██╔╝██║████╗  ██║██╔════╝ ",
        "██║   ██║██████╔╝█████╗  ██╔██╗ ██║██║   ██║██║█████╔╝ ██║██╔██╗ ██║██║  ███╗",
        "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚██╗ ██╔╝██║██╔═██╗ ██║██║╚██╗██║██║   ██║",
        "╚██████╔╝██║     ███████╗██║ ╚████║ ╚████╔╝ ██║██║  ██╗██║██║ ╚████║╚██████╔╝",
        " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝  ╚═══╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝ ╚═════╝ ",
    ]
}

fn header_version_text() -> String {
    format!("v{}", env!("OPENVIKING_CLI_VERSION"))
}

fn status_box_width() -> usize {
    wordmark_width()
}

pub(crate) fn main_action_labels() -> [&'static str; 3] {
    ["Add config", "Edit config", "Delete config"]
}

pub(crate) fn cloud_validation_failure_choices() -> [&'static str; 2] {
    ["Retry API key", "Cancel"]
}

pub(crate) fn self_managed_validation_failure_choices() -> [&'static str; 3] {
    ["Edit server URL", "Edit API key", "Cancel"]
}

pub(crate) fn edit_api_key_choice_labels(
    kind: ConfigKind,
    has_existing: bool,
) -> Vec<&'static str> {
    if !has_existing {
        return Vec::new();
    }

    match kind {
        ConfigKind::VolcengineCloud => vec!["Keep existing API key", "Replace API key"],
        ConfigKind::SelfManaged => {
            vec!["Keep existing API key", "Replace API key", "Clear API key"]
        }
    }
}

pub(crate) fn should_prompt_root_identity(
    api_key_role: Option<ApiKeyRole>,
    api_key_was_entered: bool,
    account: Option<&str>,
    user: Option<&str>,
) -> bool {
    api_key_role == Some(ApiKeyRole::Root)
        && (api_key_was_entered || is_blank(account) || is_blank(user))
}

fn print_header() {
    let lines = wizard_header_lines();
    println!();
    for (index, line) in lines.iter().take(wordmark_lines().len()).enumerate() {
        println!("{}", styled_wordmark_line(index, line));
    }
}

fn styled_wordmark_line(_index: usize, line: &str) -> String {
    let width = wordmark_width().max(1);
    let mut rendered = String::new();

    for (column, ch) in line.chars().enumerate() {
        if ch.is_whitespace() {
            rendered.push(ch);
        } else {
            let Rgb(red, green, blue) = wordmark_gradient_color(column, width);
            rendered.push_str(
                &ch.to_string()
                    .truecolor(red, green, blue)
                    .bold()
                    .to_string(),
            );
        }
    }

    rendered
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Rgb(pub(crate) u8, pub(crate) u8, pub(crate) u8);

pub(crate) fn wordmark_gradient_color(column: usize, width: usize) -> Rgb {
    if width <= 1 {
        return WORDMARK_GRADIENT_START;
    }

    let ratio = column as f32 / (width - 1) as f32;
    if ratio <= 0.56 {
        interpolate_rgb(WORDMARK_GRADIENT_START, WORDMARK_GRADIENT_MID, ratio / 0.56)
    } else {
        interpolate_rgb(
            WORDMARK_GRADIENT_MID,
            WORDMARK_GRADIENT_END,
            (ratio - 0.56) / 0.44,
        )
    }
}

fn interpolate_rgb(start: Rgb, end: Rgb, ratio: f32) -> Rgb {
    let ratio = ratio.clamp(0.0, 1.0);
    Rgb(
        interpolate_channel(start.0, end.0, ratio),
        interpolate_channel(start.1, end.1, ratio),
        interpolate_channel(start.2, end.2, ratio),
    )
}

fn interpolate_channel(start: u8, end: u8, ratio: f32) -> u8 {
    (start as f32 + (end as f32 - start as f32) * ratio).round() as u8
}

pub(crate) fn tagline_ice_color(column: usize, width: usize) -> Rgb {
    if width <= 1 {
        return TAGLINE_ICE_START;
    }

    let midpoint = width / 2;
    if column <= midpoint {
        let ratio = if midpoint == 0 {
            0.0
        } else {
            column as f32 / midpoint as f32
        };
        interpolate_rgb(TAGLINE_ICE_START, TAGLINE_ICE_MID, ratio)
    } else {
        let tail_width = (width - 1).saturating_sub(midpoint).max(1);
        let ratio = (column - midpoint) as f32 / tail_width as f32;
        interpolate_rgb(TAGLINE_ICE_MID, TAGLINE_ICE_END, ratio)
    }
}

fn tagline_texture_color(column: usize, width: usize) -> Rgb {
    let base = tagline_ice_color(column, width);
    match column % 11 {
        0 | 1 => mix_rgb(base, Rgb(255, 255, 255), 0.28),
        6 | 7 => mix_rgb(base, WORDMARK_GRADIENT_END, 0.18),
        _ => base,
    }
}

fn mix_rgb(base: Rgb, overlay: Rgb, amount: f32) -> Rgb {
    interpolate_rgb(base, overlay, amount)
}

fn styled_tagline(text: &str) -> String {
    let width = text.chars().count().max(1);
    let mut rendered = String::new();

    for (column, ch) in text.chars().enumerate() {
        if ch.is_whitespace() {
            rendered.push(ch);
        } else {
            let Rgb(red, green, blue) = tagline_texture_color(column, width);
            rendered.push_str(
                &ch.to_string()
                    .truecolor(red, green, blue)
                    .bold()
                    .to_string(),
            );
        }
    }

    rendered
}

async fn print_status_box(store: &ConfigStore) -> Result<()> {
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

    let rendered_lines = print_status_box_with_runtime(
        active.as_ref(),
        &configs,
        &config_home,
        &StatusBoxRuntime::checking(),
    )?;
    let runtime = status_box_runtime(Some(active_config)).await;
    clear_live_region(rendered_lines, false)?;
    print_status_box_with_runtime(active.as_ref(), &configs, &config_home, &runtime)?;

    Ok(())
}

fn print_status_box_with_runtime(
    active: Option<&Config>,
    configs: &[ConfigEntry],
    config_home: &str,
    runtime: &StatusBoxRuntime,
) -> Result<usize> {
    let width = status_box_width();

    println!();
    println!("{}", styled_box_title_line(HEADER_TAGLINE, width));
    let details = status_box_details(active, configs, config_home, runtime);
    let rows = OV_LOGO_LINES.len().max(details.len());
    let details = center_status_box_details(details, rows);
    for index in 0..rows {
        let logo = OV_LOGO_LINES.get(index).copied().unwrap_or("");
        let detail = details.get(index).unwrap_or(&StatusBoxDetail::Empty);
        println!("{}", styled_box_content_line(logo, detail, width, index));
    }
    println!("{}", styled_box_footer_line(&header_version_text(), width));
    println!();
    io::stdout().flush()?;
    Ok(rows + 4)
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
    let width = status_box_width();
    let details = status_box_details(active, configs, config_home, runtime);
    let rows = OV_LOGO_LINES.len().max(details.len());
    let details = center_status_box_details(details, rows);
    let mut lines = Vec::with_capacity(rows + 2);

    lines.push(box_title_line(HEADER_TAGLINE, width));
    for index in 0..rows {
        lines.push(box_content_line(
            OV_LOGO_LINES.get(index).copied().unwrap_or(""),
            details
                .get(index)
                .map(StatusBoxDetail::plain)
                .unwrap_or_default()
                .as_str(),
            width,
        ));
    }
    lines.push(box_footer_line(&header_version_text(), width));
    lines
}

async fn status_box_runtime(active: Option<&Config>) -> StatusBoxRuntime {
    let Some(config) = active else {
        return StatusBoxRuntime::not_configured();
    };

    let client = BaseClient::new(
        config.url.clone(),
        config.api_key.clone(),
        config.agent_id.clone(),
        config.account.clone(),
        config.user.clone(),
        STATUS_BOX_PROBE_TIMEOUT_SECS,
        config.extra_headers.clone(),
    );

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

    let Some(components) = value.get("components").and_then(Value::as_object) else {
        return None;
    };

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SaveAction {
    SaveAndActivate,
    SaveOnly,
    SaveActive,
    Cancel,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SaveOutcome {
    Activated,
    SavedOnly,
    UpdatedActive,
}

impl RuntimeConnectionStatus {
    fn plain(self) -> &'static str {
        match self {
            Self::Checking => "Checking...",
            Self::NotConfigured => "Not configured",
            Self::ConnectedHealthy => "Connected (Healthy)",
            Self::ConnectedUnhealthy => "Connected (Unhealthy)",
            Self::Unreachable => "Unreachable",
            #[cfg(test)]
            Self::Unknown => "Unknown",
        }
    }

    fn styled(self) -> String {
        match self {
            Self::Checking => self.plain().truecolor(234, 253, 247).bold().to_string(),
            Self::ConnectedHealthy => self.plain().truecolor(116, 255, 177).bold().to_string(),
            Self::ConnectedUnhealthy => self.plain().yellow().bold().to_string(),
            Self::Unreachable => self.plain().red().bold().to_string(),
            Self::NotConfigured => self.plain().dimmed().to_string(),
            #[cfg(test)]
            Self::Unknown => self.plain().dimmed().to_string(),
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
    Empty,
}

impl StatusBoxDetail {
    fn plain(&self) -> String {
        match self {
            Self::Active { name, kind } => match kind {
                Some(kind) => format!("Active: {name} {kind}"),
                None => format!("Active: {name}"),
            },
            Self::Status { connection } => format!("Status: {}", connection.plain()),
            Self::Model { label, value } => format!("{label}: {value}"),
            Self::Saved { count } => format!("Saved configs: {count}"),
            Self::Home { path } => format!("Config home: {path}"),
            Self::Empty => String::new(),
        }
    }

    fn styled(&self) -> String {
        match self {
            Self::Active { name, kind } => {
                let mut rendered = format!(
                    "{} {}",
                    "Active:".dimmed(),
                    name.truecolor(255, 132, 54).bold()
                );
                if let Some(kind) = kind {
                    rendered.push(' ');
                    rendered.push_str(&kind.white().bold().to_string());
                }
                rendered
            }
            Self::Status { connection } => {
                format!("{} {}", "Status:".dimmed(), connection.styled())
            }
            Self::Model { label, value } => {
                let value = if value == "Unknown" {
                    value.dimmed().to_string()
                } else {
                    value.truecolor(151, 210, 255).bold().to_string()
                };
                format!("{} {}", format!("{label}:").dimmed(), value)
            }
            Self::Saved { count } => {
                format!("{} {}", "Saved configs:".dimmed(), count.white().bold())
            }
            Self::Home { path } => {
                format!(
                    "{} {}",
                    "Config home:".dimmed(),
                    path.truecolor(186, 218, 255)
                )
            }
            Self::Empty => String::new(),
        }
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
        "Checking...".to_string()
    } else {
        "Unknown".to_string()
    }
}

fn center_status_box_details(details: Vec<StatusBoxDetail>, rows: usize) -> Vec<StatusBoxDetail> {
    let top_padding = rows.saturating_sub(details.len()) / 2;
    let mut centered = Vec::with_capacity(rows);
    centered.extend(std::iter::repeat_n(StatusBoxDetail::Empty, top_padding));
    centered.extend(details);
    centered.resize(rows, StatusBoxDetail::Empty);
    centered
}

#[cfg(test)]
fn box_title_line(title: &str, width: usize) -> String {
    let inner_width = width.saturating_sub(2);
    let title = format!(" {title} ");
    let title_width = title.chars().count().min(inner_width);
    let visible_title = truncate_to_width(&title, title_width);
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
    let title_width = title.chars().count().min(inner_width);
    let visible_title = truncate_to_width(&title, title_width);
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
fn box_content_line(left: &str, right: &str, width: usize) -> String {
    let logo_width = ov_logo_width();
    let gutter = 3usize;
    let right_width = width.saturating_sub(4 + logo_width + gutter);
    format!(
        "│ {}{}{} │",
        pad_to_width(left, logo_width),
        " ".repeat(gutter),
        pad_to_width(right, right_width)
    )
}

fn styled_box_title_line(title: &str, width: usize) -> String {
    let inner_width = width.saturating_sub(2);
    let title = format!(" {title} ");
    let title_width = title.chars().count().min(inner_width);
    let visible_title = truncate_to_width(&title, title_width);
    let left = inner_width.saturating_sub(title_width) / 2;
    let right = inner_width.saturating_sub(title_width + left);
    let Rgb(red, green, blue) = BOX_BORDER;

    format!(
        "{}{}{}{}{}",
        "╭".truecolor(red, green, blue),
        "─".repeat(left).truecolor(red, green, blue),
        styled_tagline(&visible_title),
        "─".repeat(right).truecolor(red, green, blue),
        "╮".truecolor(red, green, blue)
    )
}

fn styled_box_footer_line(title: &str, width: usize) -> String {
    let Rgb(red, green, blue) = BOX_BORDER;
    let Rgb(version_red, version_green, version_blue) = VERSION_ACCENT;
    let inner_width = width.saturating_sub(2);
    let title = format!(" {title} ");
    let title_width = title.chars().count().min(inner_width);
    let visible_title = truncate_to_width(&title, title_width);
    let right = inner_width.saturating_sub(title_width).min(5);
    let left = inner_width.saturating_sub(title_width + right);

    format!(
        "{}{}{}{}{}",
        "╰".truecolor(red, green, blue),
        "─".repeat(left).truecolor(red, green, blue),
        visible_title
            .truecolor(version_red, version_green, version_blue)
            .bold(),
        "─".repeat(right).truecolor(red, green, blue),
        "╯".truecolor(red, green, blue)
    )
}

fn styled_box_content_line(
    left: &str,
    detail: &StatusBoxDetail,
    width: usize,
    logo_row: usize,
) -> String {
    let logo_width = ov_logo_width();
    let gutter = 3usize;
    let right_width = width.saturating_sub(4 + logo_width + gutter);
    let Rgb(red, green, blue) = BOX_BORDER;
    format!(
        "{} {}{}{} {}",
        "│".truecolor(red, green, blue),
        styled_logo_to_width(left, logo_width, logo_row),
        " ".repeat(gutter),
        styled_detail_to_width(detail, right_width),
        "│".truecolor(red, green, blue)
    )
}

fn styled_logo_to_width(line: &str, width: usize, row: usize) -> String {
    let mut rendered = String::new();
    let visible = truncate_to_width(line, width);

    for (column, ch) in visible.chars().enumerate() {
        if ch.is_whitespace() {
            rendered.push(ch);
        } else {
            let Rgb(red, green, blue) = logo_glass_color(ch, column, row, width.max(1));
            rendered.push_str(
                &ch.to_string()
                    .truecolor(red, green, blue)
                    .bold()
                    .to_string(),
            );
        }
    }
    rendered.push_str(&" ".repeat(width.saturating_sub(visible.chars().count())));
    rendered
}

fn logo_glass_color(_ch: char, column: usize, row: usize, width: usize) -> Rgb {
    if width <= 1 {
        return WORDMARK_GRADIENT_START;
    }

    let column_ratio = column as f32 / (width - 1) as f32;
    let row_height = OV_LOGO_LINES.len().saturating_sub(1).max(1);
    let row_ratio = row as f32 / row_height as f32;
    let ratio = (column_ratio * 0.4 + row_ratio * 0.6).clamp(0.0, 1.0);

    if ratio <= 0.46 {
        interpolate_rgb(WORDMARK_GRADIENT_START, WORDMARK_GRADIENT_MID, ratio / 0.46)
    } else {
        interpolate_rgb(
            WORDMARK_GRADIENT_MID,
            LOGO_GRADIENT_END,
            (ratio - 0.46) / 0.54,
        )
    }
}

fn styled_detail_to_width(detail: &StatusBoxDetail, width: usize) -> String {
    let plain = truncate_to_width(&detail.plain(), width);
    let styled = if plain == detail.plain() {
        detail.styled()
    } else {
        plain.dimmed().to_string()
    };
    format!(
        "{}{}",
        styled,
        " ".repeat(width.saturating_sub(plain.chars().count()))
    )
}

fn ov_logo_width() -> usize {
    OV_LOGO_LINES
        .iter()
        .map(|line| line.chars().count())
        .max()
        .unwrap_or_default()
}

#[cfg(test)]
fn pad_to_width(text: &str, width: usize) -> String {
    let truncated = truncate_to_width(text, width);
    format!(
        "{}{}",
        truncated,
        " ".repeat(width.saturating_sub(truncated.chars().count()))
    )
}

fn truncate_to_width(text: &str, width: usize) -> String {
    if text.chars().count() <= width {
        return text.to_string();
    }
    if width == 0 {
        return String::new();
    }
    if width == 1 {
        return "…".to_string();
    }
    format!("{}…", text.chars().take(width - 1).collect::<String>())
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
                format!("Active: {} ({})", entry.name, entry.kind.label())
            } else {
                format!(
                    "Active: unnamed ({})",
                    ConfigKind::from_config(config).label()
                )
            }
        }
        None => "Active: none".to_string(),
    };

    vec![active_line, format!("Saved configs: {}", configs.len())]
}

async fn run_add_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Kind,
        Name,
        Url,
        ApiKey,
        Account,
        User,
        Validate,
    }

    let mut stage = Stage::Kind;
    let mut kind = ConfigKind::SelfManaged;
    let mut name: Option<String> = None;
    let mut url = SELF_MANAGED_DEFAULT_URL.to_string();
    let mut api_key: Option<String> = None;
    let mut account: Option<String> = None;
    let mut user: Option<String> = None;
    let mut identity_mode: Option<IdentityMode> = None;

    loop {
        match stage {
            Stage::Kind => match prompt_select(
                ui,
                "Create a new OpenViking config.",
                "Where should this CLI connect?",
                &["Volcengine Cloud", "Self-Managed"],
                0,
                &[],
            )? {
                PromptResult::Value(0) => {
                    kind = ConfigKind::VolcengineCloud;
                    url = VOLCENGINE_CLOUD_URL.to_string();
                    name = None;
                    api_key = None;
                    account = None;
                    user = None;
                    identity_mode = None;
                    stage = Stage::Name;
                }
                PromptResult::Value(1) => {
                    kind = ConfigKind::SelfManaged;
                    url = SELF_MANAGED_DEFAULT_URL.to_string();
                    name = None;
                    api_key = None;
                    account = None;
                    user = None;
                    identity_mode = None;
                    stage = Stage::Name;
                }
                PromptResult::Back => return Ok(false),
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
                PromptResult::Value(_) => unreachable!("selection is constrained by kind list"),
            },
            Stage::Name => match prompt_add_config_name(
                ui,
                "Create a new OpenViking config.",
                add_config_name_label(),
            )? {
                PromptResult::Value(value) => {
                    name = value;
                    stage = if kind == ConfigKind::VolcengineCloud {
                        Stage::ApiKey
                    } else {
                        Stage::Url
                    };
                }
                PromptResult::Back => {
                    name = None;
                    api_key = None;
                    account = None;
                    user = None;
                    identity_mode = None;
                    url = SELF_MANAGED_DEFAULT_URL.to_string();
                    stage = Stage::Kind;
                }
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::Url => match prompt_text(
                ui,
                "Create a new OpenViking config.",
                "Server URL",
                Some(&url),
                Some(InputValueLabel::Default),
                false,
                false,
                &[],
            )? {
                PromptResult::Value(value) => {
                    url = value;
                    stage = Stage::ApiKey;
                }
                PromptResult::Back => stage = Stage::Name,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::ApiKey => {
                let helper_lines = if kind == ConfigKind::VolcengineCloud {
                    volcengine_api_key_helper_lines()
                } else {
                    self_managed_api_key_helper_lines(self_managed_allows_empty_api_key(&url))
                };

                let label = if kind == ConfigKind::SelfManaged {
                    if self_managed_allows_empty_api_key(&url) {
                        "API key (optional)"
                    } else {
                        "API key"
                    }
                } else {
                    "API key"
                };
                let allow_empty_api_key =
                    kind == ConfigKind::SelfManaged && self_managed_allows_empty_api_key(&url);
                match prompt_text(
                    ui,
                    "Create a new OpenViking config.",
                    label,
                    None,
                    None,
                    allow_empty_api_key,
                    true,
                    &helper_lines,
                )? {
                    PromptResult::Value(value) => {
                        api_key = empty_to_none(value);
                        account = None;
                        user = None;
                        if kind == ConfigKind::SelfManaged
                            && api_key.is_none()
                            && self_managed_allows_empty_api_key(&url)
                        {
                            identity_mode = Some(IdentityMode::LocalNoKey);
                            stage = Stage::Account;
                        } else {
                            identity_mode = None;
                            stage = Stage::Validate;
                        }
                    }
                    PromptResult::Back => {
                        stage = if kind == ConfigKind::VolcengineCloud {
                            Stage::Name
                        } else {
                            Stage::Url
                        };
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::Account => {
                let mode = identity_mode.unwrap_or(IdentityMode::LocalNoKey);
                match prompt_identity_value(
                    ui,
                    "Create a new OpenViking config.",
                    "Account ID",
                    mode,
                )? {
                    PromptResult::Value(value) => {
                        account = Some(value);
                        stage = Stage::User;
                    }
                    PromptResult::Back => {
                        account = None;
                        user = None;
                        stage = Stage::ApiKey;
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::User => {
                let mode = identity_mode.unwrap_or(IdentityMode::LocalNoKey);
                match prompt_identity_value(ui, "Create a new OpenViking config.", "User ID", mode)?
                {
                    PromptResult::Value(value) => {
                        user = Some(value);
                        stage = Stage::Validate;
                    }
                    PromptResult::Back => {
                        user = None;
                        stage = Stage::Account;
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::Validate => {
                let draft_name = name
                    .clone()
                    .map(Ok)
                    .unwrap_or_else(|| allocate_config_name(store, kind))?;
                let draft = ConfigDraft {
                    name: draft_name.clone(),
                    kind,
                    url: url.clone(),
                    api_key: api_key.clone(),
                    account: account.clone(),
                    user: user.clone(),
                };
                match validate_draft(ui, "Create a new OpenViking config.", &draft).await {
                    Ok(ValidatedConfig {
                        config,
                        api_key_role,
                    }) => {
                        if should_prompt_root_identity(
                            api_key_role,
                            false,
                            account.as_deref(),
                            user.as_deref(),
                        ) {
                            identity_mode = Some(IdentityMode::RootKey);
                            stage = Stage::Account;
                            continue;
                        }
                        match prompt_save_action(
                            ui,
                            "Create a new OpenViking config.",
                            "Save config?",
                            add_save_action_labels(),
                            0,
                        )? {
                            PromptResult::Value(SaveAction::SaveAndActivate) => {
                                ui.clear()?;
                                save_config(store, &draft_name, &config, true)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveOnly) => {
                                ui.clear()?;
                                save_config(store, &draft_name, &config, false)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::Cancel) => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveActive) => {
                                unreachable!("add save choices cannot produce active-edit action")
                            }
                            PromptResult::Back => {
                                stage = if identity_mode.is_some() {
                                    Stage::User
                                } else {
                                    Stage::ApiKey
                                };
                            }
                            PromptResult::Quit => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                        }
                    }
                    Err(error) => {
                        let helper_lines =
                            vec![validation_error_copy(kind, &error).red().to_string()];
                        let choices: Vec<&str> = if kind == ConfigKind::VolcengineCloud {
                            cloud_validation_failure_choices().to_vec()
                        } else {
                            self_managed_validation_failure_choices().to_vec()
                        };
                        match prompt_select(
                            ui,
                            "Create a new OpenViking config.",
                            "Validation failed. What next?",
                            &choices,
                            0,
                            &helper_lines,
                        )? {
                            PromptResult::Value(0) => {
                                stage = if kind == ConfigKind::VolcengineCloud {
                                    Stage::ApiKey
                                } else {
                                    Stage::Url
                                };
                            }
                            PromptResult::Value(1) => {
                                stage = if kind == ConfigKind::VolcengineCloud {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                } else {
                                    Stage::ApiKey
                                };
                            }
                            PromptResult::Value(2) if kind == ConfigKind::SelfManaged => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Back => stage = Stage::ApiKey,
                            PromptResult::Value(_) => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Quit => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                        }
                    }
                }
            }
        }
    }
}

async fn run_edit_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Select,
        Name,
        Url,
        ApiKeyChoice,
        ApiKeyInput,
        Account,
        User,
        Validate,
    }

    let configs = store.list_configs()?;
    if configs.is_empty() {
        let helper_lines = vec!["No saved configs to edit.".yellow().to_string()];
        let _ = prompt_select(
            ui,
            "Update a saved config.",
            "Nothing to edit.",
            &["Back"],
            0,
            &helper_lines,
        )?;
        return Ok(false);
    }

    let mut stage = Stage::Select;
    let mut selected = 0usize;
    let mut name = String::new();
    let mut kind = ConfigKind::SelfManaged;
    let mut url = String::new();
    let mut api_key: Option<String> = None;
    let mut account: Option<String> = None;
    let mut user: Option<String> = None;
    let mut identity_mode: Option<IdentityMode> = None;
    let mut api_key_was_entered = false;

    loop {
        match stage {
            Stage::Select => match prompt_config_select(
                ui,
                "Update a saved config.",
                "Config to edit",
                &configs,
            )? {
                PromptResult::Value(index) => {
                    selected = index;
                    let config = &configs[index];
                    name = config.name.clone();
                    kind = config.kind;
                    url = config.config.url.clone();
                    api_key = config.config.api_key.clone();
                    account = config.config.account.clone();
                    user = config.config.user.clone();
                    identity_mode = None;
                    api_key_was_entered = false;
                    stage = Stage::Name;
                }
                PromptResult::Back => return Ok(false),
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::Name => {
                match prompt_config_name(ui, "Update a saved config.", "Config name", Some(&name))?
                {
                    PromptResult::Value(value) => {
                        name = value;
                        stage = if kind == ConfigKind::VolcengineCloud {
                            Stage::ApiKeyChoice
                        } else {
                            Stage::Url
                        };
                    }
                    PromptResult::Back => stage = Stage::Select,
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::Url => match prompt_text(
                ui,
                "Update a saved config.",
                "Server URL",
                Some(&url),
                Some(InputValueLabel::Current),
                false,
                false,
                &[],
            )? {
                PromptResult::Value(value) => {
                    url = value;
                    stage = Stage::ApiKeyChoice;
                }
                PromptResult::Back => stage = Stage::Name,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::ApiKeyChoice => {
                let helper_lines = if kind == ConfigKind::VolcengineCloud {
                    volcengine_api_key_helper_lines()
                } else {
                    self_managed_api_key_helper_lines(self_managed_allows_empty_api_key(&url))
                };

                let has_existing = api_key.as_deref().is_some_and(|value| !value.is_empty());
                if !has_existing {
                    stage = Stage::ApiKeyInput;
                    continue;
                }

                let choices = edit_api_key_choice_labels(kind, has_existing);
                match prompt_select(
                    ui,
                    "Update a saved config.",
                    "API key",
                    &choices,
                    0,
                    &helper_lines,
                )? {
                    PromptResult::Value(0) => {
                        api_key_was_entered = false;
                        stage = Stage::Validate;
                    }
                    PromptResult::Value(1) => stage = Stage::ApiKeyInput,
                    PromptResult::Value(_) => {
                        api_key = None;
                        api_key_was_entered = false;
                        if kind == ConfigKind::SelfManaged
                            && self_managed_allows_empty_api_key(&url)
                        {
                            identity_mode = Some(IdentityMode::LocalNoKey);
                            stage = Stage::Account;
                        } else {
                            identity_mode = None;
                            stage = Stage::Validate;
                        }
                    }
                    PromptResult::Back => {
                        stage = if kind == ConfigKind::VolcengineCloud {
                            Stage::Name
                        } else {
                            Stage::Url
                        };
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::ApiKeyInput => {
                let has_existing = api_key.as_deref().is_some_and(|value| !value.is_empty());
                let allow_empty =
                    kind == ConfigKind::SelfManaged && self_managed_allows_empty_api_key(&url);
                let label = if allow_empty {
                    "API key (optional)"
                } else {
                    "API key"
                };
                let helper_lines = if kind == ConfigKind::VolcengineCloud {
                    volcengine_api_key_helper_lines()
                } else {
                    self_managed_api_key_helper_lines(allow_empty)
                };
                match prompt_text(
                    ui,
                    "Update a saved config.",
                    label,
                    api_key.as_deref(),
                    api_key.as_deref().map(|_| InputValueLabel::Current),
                    allow_empty,
                    true,
                    &helper_lines,
                )? {
                    PromptResult::Value(value) => {
                        api_key = empty_to_none(value);
                        api_key_was_entered = api_key.is_some();
                        if kind == ConfigKind::SelfManaged
                            && api_key.is_none()
                            && self_managed_allows_empty_api_key(&url)
                        {
                            identity_mode = Some(IdentityMode::LocalNoKey);
                            stage = Stage::Account;
                        } else {
                            identity_mode = None;
                            stage = Stage::Validate;
                        }
                    }
                    PromptResult::Back => {
                        stage = if has_existing {
                            Stage::ApiKeyChoice
                        } else if kind == ConfigKind::VolcengineCloud {
                            Stage::Name
                        } else {
                            Stage::Url
                        };
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::Account => {
                let mode = identity_mode.unwrap_or(IdentityMode::LocalNoKey);
                match prompt_identity_value(ui, "Update a saved config.", "Account ID", mode)? {
                    PromptResult::Value(value) => {
                        account = Some(value);
                        stage = Stage::User;
                    }
                    PromptResult::Back => {
                        if identity_mode == Some(IdentityMode::RootKey) {
                            account = None;
                            user = None;
                            stage = Stage::ApiKeyInput;
                        } else {
                            stage = Stage::ApiKeyInput;
                        }
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::User => {
                let mode = identity_mode.unwrap_or(IdentityMode::LocalNoKey);
                match prompt_identity_value(ui, "Update a saved config.", "User ID", mode)? {
                    PromptResult::Value(value) => {
                        user = Some(value);
                        if identity_mode == Some(IdentityMode::RootKey) {
                            api_key_was_entered = false;
                        }
                        stage = Stage::Validate;
                    }
                    PromptResult::Back => {
                        user = None;
                        stage = Stage::Account;
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::Validate => {
                let draft = ConfigDraft {
                    name: name.clone(),
                    kind,
                    url: url.clone(),
                    api_key: api_key.clone(),
                    account: account.clone(),
                    user: user.clone(),
                };
                match validate_draft(ui, "Update a saved config.", &draft).await {
                    Ok(ValidatedConfig {
                        config,
                        api_key_role,
                    }) => {
                        if should_prompt_root_identity(
                            api_key_role,
                            api_key_was_entered,
                            account.as_deref(),
                            user.as_deref(),
                        ) {
                            identity_mode = Some(IdentityMode::RootKey);
                            stage = Stage::Account;
                            continue;
                        }
                        match prompt_save_action(
                            ui,
                            "Update a saved config.",
                            if configs[selected].is_active {
                                "Save changes to active config?"
                            } else {
                                "Save changes?"
                            },
                            edit_save_action_labels(configs[selected].is_active),
                            0,
                        )? {
                            PromptResult::Value(SaveAction::SaveActive) => {
                                ui.clear()?;
                                let original = configs[selected].name.clone();
                                store.save_edited_config(&original, &name, &config)?;
                                print_saved(store, &name, SaveOutcome::UpdatedActive)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveOnly) => {
                                ui.clear()?;
                                let original = configs[selected].name.clone();
                                store.save_edited_config(&original, &name, &config)?;
                                print_saved(store, &name, SaveOutcome::SavedOnly)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveAndActivate) => {
                                ui.clear()?;
                                let original = configs[selected].name.clone();
                                store.save_edited_config(&original, &name, &config)?;
                                store.activate_config(&name)?;
                                print_saved(store, &name, SaveOutcome::Activated)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::Cancel) => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Back => {
                                stage = if identity_mode.is_some() {
                                    Stage::User
                                } else {
                                    Stage::ApiKeyChoice
                                };
                            }
                            PromptResult::Quit => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                        }
                    }
                    Err(error) => {
                        let helper_lines =
                            vec![validation_error_copy(kind, &error).red().to_string()];
                        let choices = if kind == ConfigKind::VolcengineCloud {
                            cloud_validation_failure_choices().to_vec()
                        } else {
                            self_managed_validation_failure_choices().to_vec()
                        };
                        match prompt_select(
                            ui,
                            "Update a saved config.",
                            "Validation failed. What next?",
                            &choices,
                            0,
                            &helper_lines,
                        )? {
                            PromptResult::Value(0) => {
                                stage = if kind == ConfigKind::VolcengineCloud {
                                    Stage::ApiKeyInput
                                } else {
                                    Stage::Url
                                };
                            }
                            PromptResult::Value(1) => {
                                stage = if kind == ConfigKind::VolcengineCloud {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                } else {
                                    Stage::ApiKeyChoice
                                };
                            }
                            PromptResult::Value(2) if kind == ConfigKind::SelfManaged => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Back => stage = Stage::ApiKeyChoice,
                            PromptResult::Value(_) => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Quit => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                        }
                    }
                }
            }
        }
    }
}

fn run_delete_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Select,
        Confirm,
    }

    let configs = store.list_configs()?;
    if configs.is_empty() {
        let helper_lines = vec!["No saved configs to delete.".yellow().to_string()];
        let _ = prompt_select(
            ui,
            "Delete a saved config.",
            "Nothing to delete.",
            &["Back"],
            0,
            &helper_lines,
        )?;
        return Ok(false);
    }

    let mut stage = Stage::Select;
    let mut selected = 0usize;

    loop {
        match stage {
            Stage::Select => match prompt_config_select(
                ui,
                "Delete a saved config.",
                "Config to delete",
                &configs,
            )? {
                PromptResult::Value(index) => {
                    selected = index;
                    if configs[index].is_active {
                        let helper_lines = active_delete_block_helper_lines();
                        let _ = prompt_select(
                            ui,
                            "Delete a saved config.",
                            "Active config cannot be deleted.",
                            &["Back"],
                            0,
                            &helper_lines,
                        )?;
                        return Ok(false);
                    }
                    stage = Stage::Confirm;
                }
                PromptResult::Back => return Ok(false),
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::Confirm => {
                let name = &configs[selected].name;
                match confirm(
                    ui,
                    "Delete a saved config.",
                    &format!("Delete config '{name}'?"),
                    false,
                )? {
                    PromptResult::Value(true) => {
                        ui.clear()?;
                        store.delete_config(name)?;
                        println!();
                        println!(
                            "{} {}",
                            "✓".green(),
                            format!("Deleted config '{name}'.").green()
                        );
                        println!(
                            "{} {}",
                            "Removed:".dimmed(),
                            store
                                .saved_config_path(name)?
                                .display()
                                .to_string()
                                .magenta()
                        );
                        println!("{} {}", "Next:".dimmed(), next_step_copy());
                        return Ok(true);
                    }
                    PromptResult::Value(false) => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                    PromptResult::Back => stage = Stage::Select,
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
        }
    }
}

struct ValidatedConfig {
    config: Config,
    api_key_role: Option<ApiKeyRole>,
}

async fn validate_draft(
    ui: &mut LiveRegion,
    section: &str,
    draft: &ConfigDraft,
) -> Result<ValidatedConfig> {
    let mut config = build_config(draft)?;
    let require_api_key = draft.kind == ConfigKind::VolcengineCloud
        || (draft.kind == ConfigKind::SelfManaged
            && !self_managed_allows_empty_api_key(&draft.url));
    ui.render(&status_live_lines(section, "Validating connection..."))?;
    let api_key_role = if config
        .api_key
        .as_deref()
        .is_some_and(|key| !key.trim().is_empty())
    {
        let mut detection_config = config.clone();
        detection_config.account = None;
        detection_config.user = None;
        let role = validate_candidate_config_with_role(&detection_config, require_api_key).await?;
        if role == Some(ApiKeyRole::Root) {
            let has_identity = config
                .account
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty())
                && config
                    .user
                    .as_deref()
                    .is_some_and(|value| !value.trim().is_empty());
            if has_identity {
                validate_candidate_config(&config, require_api_key).await?;
            }
        } else {
            config.account = None;
            config.user = None;
        }
        role
    } else {
        validate_candidate_config(&config, require_api_key).await?;
        None
    };
    Ok(ValidatedConfig {
        config,
        api_key_role,
    })
}

fn save_config(store: &ConfigStore, name: &str, config: &Config, activate: bool) -> Result<()> {
    if activate {
        store.save_and_activate(name, config)?;
        print_saved(store, name, SaveOutcome::Activated)
    } else {
        store.save_named_config(name, config)?;
        print_saved(store, name, SaveOutcome::SavedOnly)
    }
}

fn print_saved(store: &ConfigStore, name: &str, outcome: SaveOutcome) -> Result<()> {
    println!();
    let message = match outcome {
        SaveOutcome::Activated => format!("Saved config '{name}' and made it active."),
        SaveOutcome::SavedOnly => format!("Saved config '{name}'."),
        SaveOutcome::UpdatedActive => format!("Saved active config '{name}'."),
    };
    println!("{} {}", "✓".green(), message.green());
    println!(
        "{} {}",
        "Saved to:".dimmed(),
        store
            .saved_config_path(name)?
            .display()
            .to_string()
            .magenta()
    );
    match outcome {
        SaveOutcome::Activated | SaveOutcome::UpdatedActive => {
            println!(
                "{} {}",
                "Active config:".dimmed(),
                store.active_path().display().to_string().magenta()
            );
        }
        SaveOutcome::SavedOnly => {
            println!(
                "{} {}",
                "Activate later:".dimmed(),
                "ov config switch".cyan()
            );
        }
    }
    println!("{} {}", "Next:".dimmed(), next_step_copy());
    Ok(())
}

fn next_step_copy() -> String {
    format!("Run {} to get started.", "ov --help".cyan().bold())
}

pub(crate) fn add_config_name_label() -> &'static str {
    "Config name (optional)"
}

fn add_config_name_helper_lines() -> Vec<String> {
    vec!["Leave empty to generate one.".dimmed().to_string()]
}

pub(crate) fn volcengine_api_key_helper_lines() -> Vec<String> {
    vec![format!(
        "{} {}",
        "Get your API key:".dimmed(),
        VOLCENGINE_API_KEY_URL
    )]
}

pub(crate) fn self_managed_api_key_helper_lines(allow_empty: bool) -> Vec<String> {
    let copy = if allow_empty {
        "Optional for local servers. Add one if auth is enabled."
    } else {
        "Required for remote self-managed servers."
    };
    vec![copy.dimmed().to_string()]
}

fn prompt_add_config_name(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
) -> Result<PromptResult<Option<String>>> {
    let mut error: Option<String> = None;
    loop {
        let mut helper_lines = add_config_name_helper_lines();
        if let Some(value) = error.as_ref() {
            helper_lines.push(value.red().to_string());
        }

        match prompt_text(ui, section, prompt, None, None, true, false, &helper_lines)? {
            PromptResult::Value(value) => {
                let value = value.trim();
                if value.is_empty() {
                    return Ok(PromptResult::Value(None));
                }
                match validate_config_name(value) {
                    Ok(()) => return Ok(PromptResult::Value(Some(value.to_string()))),
                    Err(next_error) => error = Some(next_error.to_string()),
                }
            }
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

pub(crate) fn allocate_config_name(store: &ConfigStore, kind: ConfigKind) -> Result<String> {
    let prefix = match kind {
        ConfigKind::VolcengineCloud => "cloud",
        ConfigKind::SelfManaged => "local",
    };

    for _ in 0..32 {
        let suffix = Uuid::new_v4().simple().to_string();
        let candidate = format!("{prefix}-{}", &suffix[..6]);
        if !store.saved_config_path(&candidate)?.exists() {
            return Ok(candidate);
        }
    }

    Err(Error::Config(
        "Could not generate a unique config name. Please enter one manually.".to_string(),
    ))
}

fn prompt_config_name(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: Option<&str>,
) -> Result<PromptResult<String>> {
    let mut error: Option<String> = None;
    loop {
        let helper_lines: Vec<String> = error
            .as_ref()
            .map(|value| vec![value.red().to_string()])
            .unwrap_or_default();
        match prompt_text(
            ui,
            section,
            prompt,
            default,
            Some(InputValueLabel::Current),
            false,
            false,
            &helper_lines,
        )? {
            PromptResult::Value(value) => match validate_config_name(&value) {
                Ok(()) => return Ok(PromptResult::Value(value)),
                Err(next_error) => error = Some(next_error.to_string()),
            },
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

fn prompt_identity_value(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    mode: IdentityMode,
) -> Result<PromptResult<String>> {
    let (default, value_label, helper_lines) = identity_prompt_parts(mode);
    prompt_text(
        ui,
        section,
        prompt,
        default,
        value_label,
        false,
        false,
        &helper_lines,
    )
}

fn identity_prompt_parts(
    mode: IdentityMode,
) -> (Option<&'static str>, Option<InputValueLabel>, Vec<String>) {
    match mode {
        IdentityMode::LocalNoKey => (
            Some("default"),
            Some(InputValueLabel::Default),
            vec!["Local no-key identity.".dimmed().to_string()],
        ),
        IdentityMode::RootKey => (
            None,
            None,
            vec![
                "Root API keys require an explicit account and user."
                    .dimmed()
                    .to_string(),
            ],
        ),
    }
}

fn prompt_config_select(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    configs: &[ConfigEntry],
) -> Result<PromptResult<usize>> {
    let items: Vec<String> = configs.iter().map(config_select_label).collect();
    prompt_select(ui, section, prompt, &items, 0, &[])
}

fn config_select_label(entry: &ConfigEntry) -> String {
    let label = format!("{} - {}", entry.name, entry.kind.label());
    if entry.is_active {
        format!("{} {}", label, "[Active]".red().bold())
    } else {
        label
    }
}

fn active_delete_block_helper_lines() -> Vec<String> {
    vec![
        "Deleting the active config is blocked.".red().to_string(),
        format!(
            "{} {} {}",
            "Run".dimmed(),
            "ov config switch".cyan().bold(),
            "to choose another config, then delete this one.".dimmed()
        ),
    ]
}

pub(crate) fn add_save_action_labels() -> Vec<&'static str> {
    vec!["Save and activate", "Save only", "Cancel"]
}

pub(crate) fn edit_save_action_labels(is_active: bool) -> Vec<&'static str> {
    if is_active {
        vec!["Save changes", "Cancel"]
    } else {
        vec!["Save only", "Save and activate", "Cancel"]
    }
}

fn prompt_save_action(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    items: Vec<&'static str>,
    default: usize,
) -> Result<PromptResult<SaveAction>> {
    match prompt_select(ui, section, prompt, &items, default, &[])? {
        PromptResult::Value(index) => {
            let action = match items.get(index).copied() {
                Some("Save and activate") => SaveAction::SaveAndActivate,
                Some("Save only") => SaveAction::SaveOnly,
                Some("Save changes") => SaveAction::SaveActive,
                Some("Cancel") => SaveAction::Cancel,
                _ => unreachable!("selection is constrained by save action list"),
            };
            Ok(PromptResult::Value(action))
        }
        PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
    }
}

fn prompt_select<T: ToString>(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    items: &[T],
    default: usize,
    helper_lines: &[String],
) -> Result<PromptResult<usize>> {
    let items: Vec<String> = items.iter().map(ToString::to_string).collect();
    let mut selected = default.min(items.len().saturating_sub(1));
    let raw = RawPrompt::enter(true)?;

    loop {
        ui.render(&select_live_lines(
            section,
            prompt,
            &items,
            selected,
            helper_lines,
        ))?;

        if let Event::Key(key) = event::read()? {
            match key.code {
                KeyCode::Up => {
                    selected = if selected == 0 {
                        items.len().saturating_sub(1)
                    } else {
                        selected - 1
                    };
                }
                KeyCode::Down => selected = (selected + 1) % items.len().max(1),
                KeyCode::Enter => {
                    drop(raw);
                    ui.clear()?;
                    return Ok(PromptResult::Value(selected));
                }
                KeyCode::Esc => {
                    drop(raw);
                    ui.clear()?;
                    return Ok(PromptResult::Back);
                }
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    drop(raw);
                    ui.clear()?;
                    return Ok(PromptResult::Quit);
                }
                _ => {}
            }
        }
    }
}

fn prompt_text(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: Option<&str>,
    value_label: Option<InputValueLabel>,
    allow_empty: bool,
    secret: bool,
    helper_lines: &[String],
) -> Result<PromptResult<String>> {
    let mut error: Option<String> = None;
    'attempt: loop {
        let mut value = String::new();
        let default_copy = default.unwrap_or_default();
        ui.render_input(
            &input_live_lines(
                section,
                prompt,
                default,
                value_label,
                secret,
                helper_lines,
                error.as_deref(),
            ),
            "  > ",
        )?;

        let raw = RawPrompt::enter(false)?;
        loop {
            if let Event::Key(key) = event::read()? {
                match key.code {
                    KeyCode::Enter => {
                        let chosen = if value.trim().is_empty() {
                            default_copy.trim().to_string()
                        } else {
                            value.trim().to_string()
                        };
                        drop(raw);
                        if chosen.is_empty() && !allow_empty {
                            ui.clear()?;
                            error = Some("Value cannot be empty.".to_string());
                            continue 'attempt;
                        }
                        ui.clear()?;
                        return Ok(PromptResult::Value(chosen));
                    }
                    KeyCode::Esc => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Back);
                    }
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Quit);
                    }
                    KeyCode::Backspace => {
                        if value.pop().is_some() {
                            raw_write("\x08 \x08")?;
                            io::stdout().flush()?;
                        }
                    }
                    KeyCode::Char(ch) => {
                        if !key.modifiers.contains(KeyModifiers::CONTROL) {
                            value.push(ch);
                            if secret {
                                raw_write("*")?;
                            } else {
                                raw_write(ch.to_string())?;
                            }
                            io::stdout().flush()?;
                        }
                    }
                    _ => {}
                }
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InputValueLabel {
    Default,
    Current,
}

impl InputValueLabel {
    fn text(self) -> &'static str {
        match self {
            Self::Default => "Default:",
            Self::Current => "Current:",
        }
    }
}

fn confirm(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: bool,
) -> Result<PromptResult<bool>> {
    let items = ["Yes", "No"];
    match prompt_select(ui, section, prompt, &items, usize::from(!default), &[])? {
        PromptResult::Value(0) => Ok(PromptResult::Value(true)),
        PromptResult::Value(1) => Ok(PromptResult::Value(false)),
        PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
        PromptResult::Value(_) => unreachable!("selection is constrained by confirm list"),
    }
}

#[derive(Default)]
struct LiveRegion {
    lines_drawn: usize,
    cursor_on_last_line: bool,
}

impl LiveRegion {
    fn render(&mut self, lines: &[String]) -> Result<()> {
        self.clear()?;
        for line in lines {
            raw_line(line)?;
        }
        io::stdout().flush()?;
        self.lines_drawn = lines.len();
        self.cursor_on_last_line = false;
        Ok(())
    }

    fn render_input(&mut self, lines: &[String], prompt: &str) -> Result<()> {
        self.clear()?;
        for line in lines {
            raw_line(line)?;
        }
        raw_write(prompt)?;
        io::stdout().flush()?;
        self.lines_drawn = lines.len() + 1;
        self.cursor_on_last_line = true;
        Ok(())
    }

    fn clear(&mut self) -> Result<()> {
        clear_live_region(self.lines_drawn, self.cursor_on_last_line)?;
        self.lines_drawn = 0;
        self.cursor_on_last_line = false;
        Ok(())
    }
}

pub(crate) fn select_live_lines<T: ToString>(
    section: &str,
    prompt: &str,
    items: &[T],
    selected: usize,
    helper_lines: &[String],
) -> Vec<String> {
    let mut lines = vec![
        format!("{} {}", "◆".purple().bold(), section.bold()),
        String::new(),
        format!("{} {}", "?".yellow().bold(), prompt.bold()),
        format!("  {}", NAV_HINT.dimmed()),
    ];

    if !helper_lines.is_empty() {
        lines.push(String::new());
        lines.extend(helper_lines.iter().map(|line| format!("  {line}")));
    }

    lines.push(String::new());
    lines.extend(items.iter().enumerate().map(|(index, item)| {
        let item = item.to_string();
        if index == selected {
            format!("  › {item}").green().bold().to_string()
        } else {
            format!("    {item}")
        }
    }));
    lines
}

fn input_live_lines(
    section: &str,
    prompt: &str,
    default: Option<&str>,
    value_label: Option<InputValueLabel>,
    secret: bool,
    helper_lines: &[String],
    error: Option<&str>,
) -> Vec<String> {
    let mut lines = vec![
        format!("{} {}", "◆".purple().bold(), section.bold()),
        String::new(),
        format!("{} {}", "?".yellow().bold(), prompt.bold()),
        format!("  {}", INPUT_HINT.dimmed()),
    ];

    if !helper_lines.is_empty() {
        lines.push(String::new());
        lines.extend(helper_lines.iter().map(|line| format!("  {line}")));
    }

    if let (Some(default_value), Some(value_label)) = (default, value_label) {
        let rendered_default = if secret {
            if default_value.trim().is_empty() {
                "(empty)".dimmed().to_string()
            } else {
                "(existing value)".dimmed().to_string()
            }
        } else {
            default_value.dimmed().to_string()
        };
        lines.push(format!(
            "  {} {}",
            value_label.text().dimmed(),
            rendered_default
        ));
    }

    if let Some(error) = error {
        lines.push(format!("  {}", error.red()));
    }

    lines.push(String::new());
    lines
}

fn status_live_lines(section: &str, status: &str) -> Vec<String> {
    vec![
        format!("{} {}", "◆".purple().bold(), section.bold()),
        String::new(),
        format!("{} {}", "…".cyan().bold(), status.bold()),
    ]
}

fn clear_live_region(count: usize, cursor_on_last_line: bool) -> io::Result<()> {
    let mut stdout = io::stdout();

    if count == 0 {
        return Ok(());
    }

    if cursor_on_last_line {
        stdout.execute(MoveToColumn(0))?;
        stdout.execute(Clear(ClearType::CurrentLine))?;
        for _ in 1..count {
            stdout.execute(MoveUp(1))?;
            stdout.execute(MoveToColumn(0))?;
            stdout.execute(Clear(ClearType::CurrentLine))?;
        }
    } else {
        for _ in 0..count {
            stdout.execute(MoveToColumn(0))?;
            stdout.execute(MoveUp(1))?;
            stdout.execute(MoveToColumn(0))?;
            stdout.execute(Clear(ClearType::CurrentLine))?;
        }
    }

    stdout.execute(MoveToColumn(0))?;
    Ok(())
}

fn raw_line(text: impl AsRef<str>) -> io::Result<()> {
    let mut stdout = io::stdout();
    stdout.execute(MoveToColumn(0))?;
    write!(stdout, "{}\r\n", text.as_ref())
}

fn raw_write(text: impl AsRef<str>) -> io::Result<()> {
    write!(io::stdout(), "{}", text.as_ref())
}

fn empty_to_none(value: String) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn is_blank(value: Option<&str>) -> bool {
    value.is_none_or(|value| value.trim().is_empty())
}

fn print_cancelled(ui: &mut LiveRegion) -> Result<()> {
    ui.clear()?;
    println!();
    println!(
        "{}",
        "Cancelled. No partial configuration was written.".yellow()
    );
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PromptResult<T> {
    Value(T),
    Back,
    Quit,
}

struct RawPrompt {
    hide_cursor: bool,
}

impl RawPrompt {
    fn enter(hide_cursor: bool) -> Result<Self> {
        enable_raw_mode()?;
        if hide_cursor {
            io::stdout().execute(Hide)?;
        }
        Ok(Self { hide_cursor })
    }
}

impl Drop for RawPrompt {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        if self.hide_cursor {
            let _ = io::stdout().execute(Show);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        BOX_BORDER, IdentityMode, InputValueLabel, LOGO_GRADIENT_END, OV_LOGO_LINES, Rgb,
        StatusBoxRuntime, VERSION_ACCENT, active_delete_block_helper_lines, active_summary_lines,
        active_summary_render_parts, add_config_name_label, add_save_action_labels,
        allocate_config_name, cloud_validation_failure_choices, config_select_label,
        display_config_home, edit_api_key_choice_labels, edit_save_action_labels,
        extract_models_from_status_payload, identity_prompt_parts, input_live_lines,
        logo_glass_color, main_action_labels, next_step_copy, ov_logo_width,
        saved_summary_render_parts, select_live_lines, self_managed_api_key_helper_lines,
        self_managed_validation_failure_choices, should_prompt_root_identity, status_box_lines,
        status_box_lines_with_runtime, status_box_width, status_payload_is_healthy,
        tagline_ice_color, validate_config_name, volcengine_api_key_helper_lines,
        wizard_header_lines, wordmark_gradient_color, wordmark_lines, wordmark_width,
    };
    use crate::config::Config;
    use crate::config_wizard::store::{ApiKeyRole, ConfigEntry, ConfigKind, ConfigStore};
    use serde_json::json;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_dir(name: &str) -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should be valid")
            .as_nanos();
        std::env::temp_dir().join(format!("openviking-wizard-{name}-{suffix}"))
    }

    #[test]
    fn wizard_header_is_scrollback_branded() {
        let header = wizard_header_lines().join("\n");

        assert!(header.contains("█████"));
        assert!(!header.contains("Context Database for AI Agents"));
        assert!(!header.contains(".openviking ~ ov config"));
        assert!(!header.contains("CLI config"));
        assert!(!header.contains("profile manager"));
        assert!(!header.contains("↑/↓ choose"));
        assert!(!header.contains("OpenViking CLI v"));
    }

    #[test]
    fn wizard_header_has_spaced_bands() {
        let lines = wizard_header_lines();

        assert_eq!(lines[wordmark_lines().len()], "");
        assert_eq!(lines.len(), wordmark_lines().len() + 1);
    }

    #[test]
    fn header_omits_tagline_and_version() {
        let lines = wizard_header_lines();
        let header = lines.join("\n");

        assert!(!header.contains("Context Database for AI Agents"));
        assert!(!header.contains("OpenViking CLI v"));
    }

    #[test]
    fn status_box_lines_align_to_wordmark_width_with_tagline_title_and_version_footer() {
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let lines = status_box_lines(
            Some(&config),
            &[ConfigEntry {
                name: "test".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::SelfManaged,
            }],
            "~/.openviking",
        );
        let version = format!("v{}", env!("OPENVIKING_CLI_VERSION"));

        assert!(lines.len() >= 6);
        assert!(lines[0].contains("Context Database for AI Agents"));
        assert!(!lines[0].contains(&version));
        assert!(
            lines
                .last()
                .expect("footer should render")
                .contains(&version)
        );
        assert!(!lines[0].contains("OpenViking CLI"));
        assert!(lines[0].starts_with('╭'));
        assert!(lines[0].ends_with('╮'));
        assert!(lines.last().expect("footer should render").starts_with('╰'));
        assert!(lines.last().expect("footer should render").ends_with('╯'));
        assert_eq!(status_box_width(), wordmark_width());
        assert_eq!(lines[0].chars().count(), status_box_width());
        assert!(
            lines
                .last()
                .expect("footer should render")
                .find(&version)
                .expect("version should render")
                > status_box_width() / 2
        );
        for line in &lines {
            assert_eq!(line.chars().count(), status_box_width(), "{line:?}");
        }
    }

    #[test]
    fn status_box_contains_config_status_without_global_controls() {
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let lines = status_box_lines(
            Some(&config),
            &[ConfigEntry {
                name: "orange".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::SelfManaged,
            }],
            "~/.openviking",
        );
        let text = lines.join("\n");

        assert!(text.contains("Active:"));
        assert!(text.contains("orange (Self-Managed)"));
        assert!(text.contains("Status:"));
        assert!(text.contains("VLM:"));
        assert!(text.contains("Embedding:"));
        assert!(text.contains("Saved configs:"));
        assert!(text.contains("1"));
        assert!(text.contains("Config home:"));
        assert!(text.contains("~/.openviking"));
        assert!(text.contains("Context Database for AI Agents"));
        assert!(!text.contains("Save policy:"));
        assert!(!text.contains("validation + explicit save"));
        assert!(!text.contains("↑/↓ choose"));
        assert!(!text.contains("Enter select"));
        assert!(!text.contains("Esc back"));
    }

    #[test]
    fn status_box_checking_runtime_renders_immediate_placeholders() {
        let lines = status_box_lines_with_runtime(
            None,
            &[],
            "~/.openviking",
            &StatusBoxRuntime::checking(),
        );
        let text = lines.join("\n");

        assert!(text.contains("Status: Checking..."));
        assert!(text.contains("VLM: Checking..."));
        assert!(text.contains("Embedding: Checking..."));
    }

    #[test]
    fn status_box_can_render_healthy_status_and_runtime_models() {
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let runtime = StatusBoxRuntime::connected(
            true,
            Some("doubao-seed-2-0-pro-260215".to_string()),
            Some("doubao-embedding-vision-251215".to_string()),
        );
        let lines = status_box_lines_with_runtime(
            Some(&config),
            &[ConfigEntry {
                name: "vps".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::SelfManaged,
            }],
            "~/.openviking",
            &runtime,
        );
        let text = lines.join("\n");

        assert!(text.contains("Status: Connected (Healthy)"));
        assert!(text.contains("VLM: doubao-seed-2-0-pro-260215"));
        assert!(text.contains("Embedding: doubao-embedding-vision-251215"));
    }

    #[test]
    fn status_payload_parser_reuses_ov_status_model_tables() {
        let payload = json!({
            "is_healthy": true,
            "components": {
                "models": {
                    "name": "models",
                    "is_healthy": true,
                    "status": "\nVLM Models:\n+-------+\n| Model | Provider |\n+-------+\n| doubao-seed-2-0-pro-260215 | volcengine |\n+-------+\n\nEmbedding Models:\n+-------+\n| Model | Provider |\n+-------+\n| doubao-embedding-vision-251215 | volcengine |\n+-------+\n"
                }
            }
        });

        assert_eq!(
            extract_models_from_status_payload(&payload),
            (
                Some("doubao-seed-2-0-pro-260215".to_string()),
                Some("doubao-embedding-vision-251215".to_string())
            )
        );
    }

    #[test]
    fn status_payload_health_falls_back_to_component_health() {
        let payload = json!({
            "components": {
                "queue": { "is_healthy": true, "has_errors": false },
                "models": { "is_healthy": true, "has_errors": false }
            }
        });

        assert!(status_payload_is_healthy(&payload));
    }

    #[test]
    fn status_box_details_are_vertically_centered_without_label_justification() {
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let lines = status_box_lines(
            Some(&config),
            &[ConfigEntry {
                name: "orange".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::SelfManaged,
            }],
            "~/.openviking",
        );

        let active_index = lines
            .iter()
            .position(|line| line.contains("Active:"))
            .expect("active detail should render");

        assert!(active_index > 2, "details should not start at the top");
        assert!(
            active_index + 6 < lines.len() - 1,
            "details should not end at the bottom"
        );
        assert!(lines[active_index].contains("Active: orange (Self-Managed)"));
        assert!(lines[active_index + 1].contains("Status: Unknown"));
        assert!(lines[active_index + 2].contains("VLM: Unknown"));
        assert!(lines[active_index + 3].contains("Embedding: Unknown"));
        assert!(lines[active_index + 4].contains("Saved configs: 1"));
        assert!(lines[active_index + 5].contains("Config home: ~/.openviking"));
        assert!(!lines[active_index + 6].contains("Save policy:"));
    }

    #[test]
    fn status_box_uses_filled_logo_instead_of_outline_sail() {
        let lines = status_box_lines(None, &[], "~/.openviking");
        let text = lines.join("\n");

        assert!(text.contains("⣿⣿⣿⣧ ⠹⣿⣦⡀"));
        assert!(text.contains("⢀⣾⡿⠿⠛⢛⣿⣿⣿⣿⣿⣿⣿⣿⡇⢀⣼⠃"));
        assert!(text.contains("⠠⣶⣾⣿⣿⣿⣶⣤⣀ ⠾⠟⠛⠉⠉   ⣀⣤⣾⡿⠃"));
        assert!(!text.contains("████ ▓▓▓▓"));
        assert!(!text.contains("/\\"));
        assert!(!text.contains("/____\\"));
    }

    #[test]
    fn status_box_logo_uses_faceted_sails_with_negative_space() {
        let logo = OV_LOGO_LINES.join("\n");
        let split_rows = OV_LOGO_LINES
            .iter()
            .filter(|line| visible_group_count(line) >= 2)
            .count();

        assert_eq!(OV_LOGO_LINES.len(), 14);
        assert!(ov_logo_width() <= 28);
        assert!(
            logo.contains('⣿'),
            "logo should use high-detail filled facets"
        );
        assert!(logo.contains('⠿'), "logo should include sharp cut facets");
        assert!(logo.contains("⣿⣿⣿⣧ ⠹⣿⣦⡀"));
        assert!(logo.contains("⢀⣾⡿⠿⠛⢛⣿⣿⣿⣿⣿⣿⣿⣿⡇⢀⣼⠃"));
        assert!(logo.contains("⠠⣶⣾⣿⣿⣿⣶⣤⣀ ⠾⠟⠛⠉⠉   ⣀⣤⣾⡿⠃"));
        assert!(
            split_rows >= 5,
            "logo should preserve visible internal gaps"
        );
        assert!(
            !logo.contains("████████████████"),
            "logo should not collapse into a solid block"
        );
    }

    fn visible_group_count(line: &str) -> usize {
        let mut groups = 0;
        let mut in_group = false;
        for ch in line.chars() {
            if ch.is_whitespace() {
                in_group = false;
            } else if !in_group {
                groups += 1;
                in_group = true;
            }
        }
        groups
    }

    #[test]
    fn display_config_home_uses_tilde_for_current_home() {
        let home = std::env::var_os("HOME").expect("HOME should be set for CLI tests");
        let dir = PathBuf::from(home).join(".openviking");
        let store = ConfigStore::for_config_dir(dir.clone());

        assert_eq!(display_config_home(&store), "~/.openviking");
    }

    #[test]
    fn wordmark_lines_have_identical_width() {
        let wordmark = wordmark_lines();
        let width = wordmark[0].chars().count();

        for line in wordmark {
            assert_eq!(line.chars().count(), width, "{line:?} should match");
        }
    }

    #[test]
    fn wordmark_visible_edges_are_consistent() {
        let wordmark = wordmark_lines();
        let width = wordmark_width();

        for line in wordmark {
            let first_visible = line
                .chars()
                .position(|ch| !ch.is_whitespace())
                .expect("wordmark line should have visible text");
            assert!(
                first_visible <= 1,
                "{line:?} should not protrude or drift horizontally"
            );
            assert!(
                line.trim_end().chars().count() >= width - 1,
                "{line:?} should reach the right edge"
            );
        }
    }

    #[test]
    fn wordmark_does_not_use_protruding_corner_glyphs() {
        let lines = wizard_header_lines();
        let wordmark = &lines[0..wordmark_lines().len()];

        assert!(!wordmark[0].starts_with('◢'));
        assert!(!wordmark[0].ends_with('◣'));
        assert!(!wordmark[wordmark.len() - 1].starts_with('◥'));
        assert!(!wordmark[wordmark.len() - 1].ends_with('◤'));
    }

    #[test]
    fn wordmark_gradient_runs_pearl_jade() {
        let width = wordmark_width();

        assert_eq!(wordmark_gradient_color(0, width), Rgb(234, 253, 247));
        assert_eq!(wordmark_gradient_color(width / 2, width), Rgb(70, 218, 201));
        assert_eq!(wordmark_gradient_color(width - 1, width), Rgb(7, 95, 100));
    }

    #[test]
    fn tagline_ice_color_runs_pearl_jade() {
        let width = "Context Database for AI Agents".chars().count();

        assert_eq!(tagline_ice_color(0, width), Rgb(234, 253, 247));
        assert_eq!(tagline_ice_color(width / 2, width), Rgb(50, 214, 196));
        assert_eq!(tagline_ice_color(width - 1, width), Rgb(7, 95, 100));
    }

    #[test]
    fn status_box_border_uses_pearl_jade() {
        assert_eq!(BOX_BORDER, Rgb(50, 214, 196));
    }

    #[test]
    fn status_box_footer_version_uses_pearl_jade_accent() {
        assert_eq!(VERSION_ACCENT, Rgb(50, 214, 196));
    }

    #[test]
    fn status_box_logo_uses_diagonal_pearl_jade_gradient() {
        let width = ov_logo_width();

        assert_eq!(logo_glass_color('⣿', 0, 0, width), Rgb(234, 253, 247));
        assert_eq!(
            logo_glass_color('⣿', width / 2, 7, width),
            Rgb(44, 194, 179)
        );
        assert_eq!(
            logo_glass_color('⣿', width - 1, 13, width),
            LOGO_GRADIENT_END
        );

        let upper = logo_glass_color('⣿', width / 2, 1, width);
        let lower = logo_glass_color('⣿', width / 2, 12, width);
        assert!(
            lower.0 < upper.0 && lower.1 < upper.1 && lower.2 < upper.2,
            "logo should darken from top-left toward bottom-right"
        );
    }

    #[test]
    fn active_summary_hides_url_and_shows_kind() {
        let mut config = Config::default();
        config.url = "http://127.0.0.1:1933".to_string();
        let lines = active_summary_lines(
            Some(&config),
            &[ConfigEntry {
                name: "local".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::SelfManaged,
            }],
        );

        assert_eq!(lines[0], "Active: local (Self-Managed)");
        assert_eq!(lines[1], "Saved configs: 1");
        assert!(!lines[0].contains("127.0.0.1"));
        assert!(!lines[0].starts_with(' '));
        assert!(!lines[1].starts_with(' '));
    }

    #[test]
    fn summary_render_parts_split_config_name_type_and_count() {
        let active = active_summary_render_parts("Active: test (Self-Managed)")
            .expect("active summary should split");
        let saved =
            saved_summary_render_parts("Saved configs: 2").expect("saved summary should split");

        assert_eq!(active.label, "Active:");
        assert_eq!(active.name, "test");
        assert_eq!(active.kind.as_deref(), Some("(Self-Managed)"));
        assert_eq!(saved.label, "Saved configs:");
        assert_eq!(saved.count, "2");
    }

    #[test]
    fn add_config_name_copy_marks_name_optional() {
        assert_eq!(add_config_name_label(), "Config name (optional)");
    }

    #[test]
    fn add_config_name_rendering_has_no_default_or_current_label() {
        let lines = input_live_lines(
            "Create a new OpenViking config.",
            add_config_name_label(),
            None,
            None,
            false,
            &["Leave empty to generate one.".to_string()],
            None,
        );

        let text = lines.join("\n");
        assert!(text.contains("Config name (optional)"));
        assert!(text.contains("Leave empty to generate one."));
        assert!(!text.contains("Default:"));
        assert!(!text.contains("Current:"));
    }

    #[test]
    fn edit_config_name_rendering_uses_current_label() {
        let lines = input_live_lines(
            "Update a saved config.",
            "Config name",
            Some("test"),
            Some(InputValueLabel::Current),
            false,
            &[],
            None,
        );

        let text = lines.join("\n");
        assert!(text.contains("Current:"));
        assert!(text.contains("test"));
        assert!(!text.contains("Default:"));
    }

    #[test]
    fn generated_config_name_is_valid_prefixed_and_non_colliding() {
        let dir = unique_dir("generated-name");
        fs::create_dir_all(&dir).expect("dir should exist");
        let store = ConfigStore::for_config_dir(dir);

        let name = allocate_config_name(&store, ConfigKind::SelfManaged)
            .expect("generated name should be available");

        assert!(name.starts_with("local-"));
        assert_eq!(name.len(), "local-".len() + 6);
        validate_config_name(&name).expect("generated name should be valid");
    }

    #[test]
    fn provider_helper_copy_is_minimal_and_self_managed_is_clear() {
        let cloud = volcengine_api_key_helper_lines();
        let local_self_managed = self_managed_api_key_helper_lines(true);
        let remote_self_managed = self_managed_api_key_helper_lines(false);

        assert!(cloud.iter().any(|line| line.contains("Get your API key:")));
        assert!(!cloud.iter().any(|line| line.contains("Server URL")));
        assert!(
            local_self_managed.iter().any(
                |line| line.contains("Optional for local servers. Add one if auth is enabled.")
            )
        );
        assert!(
            remote_self_managed
                .iter()
                .any(|line| line.contains("Required for remote self-managed servers."))
        );
        assert!(
            !remote_self_managed
                .iter()
                .any(|line| line.contains("Usually not needed locally"))
        );
        assert!(
            !local_self_managed
                .iter()
                .chain(remote_self_managed.iter())
                .any(|line| line.contains(';'))
        );
    }

    #[test]
    fn add_self_managed_api_key_rendering_has_no_existing_value_placeholder() {
        let lines = input_live_lines(
            "Create a new OpenViking config.",
            "API key (optional)",
            None,
            None,
            true,
            &self_managed_api_key_helper_lines(true),
            None,
        );
        let text = lines.join("\n");

        assert!(!text.contains("(existing value)"));
        assert!(!text.contains("Default:"));
        assert!(!text.contains("Current:"));
    }

    #[test]
    fn local_no_key_identity_prompt_shows_default_identity() {
        let (default, value_label, helper_lines) = identity_prompt_parts(IdentityMode::LocalNoKey);
        let lines = input_live_lines(
            "Create a new OpenViking config.",
            "Account ID",
            default,
            value_label,
            false,
            &helper_lines,
            None,
        );
        let text = lines.join("\n");

        assert!(text.contains("Default:"));
        assert!(text.contains("default"));
        assert!(text.contains("Local no-key identity."));
        assert!(!text.contains("Press Enter"));
    }

    #[test]
    fn root_key_identity_prompt_has_no_default_identity() {
        let (default, value_label, helper_lines) = identity_prompt_parts(IdentityMode::RootKey);
        let lines = input_live_lines(
            "Create a new OpenViking config.",
            "Account ID",
            default,
            value_label,
            false,
            &helper_lines,
            None,
        );
        let text = lines.join("\n");

        assert!(!text.contains("Default:"));
        assert!(!text.contains("Current:"));
        assert!(text.contains("Root API keys require an explicit account and user."));
    }

    #[test]
    fn validation_failure_choices_are_kind_specific() {
        assert_eq!(
            cloud_validation_failure_choices(),
            ["Retry API key", "Cancel"]
        );
        assert_eq!(
            self_managed_validation_failure_choices(),
            ["Edit server URL", "Edit API key", "Cancel"]
        );
        assert!(!cloud_validation_failure_choices().contains(&"Edit config name"));
    }

    #[test]
    fn config_select_label_marks_active_with_badge() {
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let active = ConfigEntry {
            name: "VPS".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::SelfManaged,
        };
        let inactive = ConfigEntry {
            name: "local".to_string(),
            config,
            is_active: false,
            kind: ConfigKind::SelfManaged,
        };

        let active_label = config_select_label(&active);
        assert!(active_label.contains("VPS - Self-Managed"));
        assert!(active_label.contains("[Active]"));
        assert!(!active_label.contains("* "));

        let inactive_label = config_select_label(&inactive);
        assert_eq!(inactive_label, "local - Self-Managed");
        assert!(!inactive_label.contains("[Active]"));
    }

    #[test]
    fn active_delete_copy_mentions_switch_command() {
        let copy = active_delete_block_helper_lines().join("\n");

        assert!(copy.contains("Deleting the active config is blocked."));
        assert!(copy.contains("ov config switch"));
        assert!(copy.contains("then delete this one"));
    }

    #[test]
    fn save_choices_allow_saving_without_activation() {
        assert_eq!(
            add_save_action_labels(),
            ["Save and activate", "Save only", "Cancel"]
        );
        assert_eq!(
            edit_save_action_labels(false),
            ["Save only", "Save and activate", "Cancel"]
        );
        assert_eq!(edit_save_action_labels(true), ["Save changes", "Cancel"]);
    }

    #[test]
    fn success_copy_points_to_help_command() {
        assert!(next_step_copy().contains("ov --help"));
        assert!(next_step_copy().contains("get started"));
    }

    #[test]
    fn edit_api_key_choices_match_kind_and_existing_key_state() {
        assert!(edit_api_key_choice_labels(ConfigKind::SelfManaged, false).is_empty());
        assert!(edit_api_key_choice_labels(ConfigKind::VolcengineCloud, false).is_empty());
        assert_eq!(
            edit_api_key_choice_labels(ConfigKind::SelfManaged, true),
            ["Keep existing API key", "Replace API key", "Clear API key"]
        );
        assert_eq!(
            edit_api_key_choice_labels(ConfigKind::VolcengineCloud, true),
            ["Keep existing API key", "Replace API key"]
        );
    }

    #[test]
    fn replaced_root_key_requires_identity_confirmation() {
        assert!(should_prompt_root_identity(
            Some(ApiKeyRole::Root),
            true,
            Some("old-account"),
            Some("old-user"),
        ));
        assert!(should_prompt_root_identity(
            Some(ApiKeyRole::Root),
            false,
            Some("old-account"),
            None,
        ));
        assert!(!should_prompt_root_identity(
            Some(ApiKeyRole::Root),
            false,
            Some("old-account"),
            Some("old-user"),
        ));
        assert!(!should_prompt_root_identity(
            Some(ApiKeyRole::Regular),
            true,
            Some("old-account"),
            Some("old-user"),
        ));
    }

    #[test]
    fn live_select_lines_are_current_step_only() {
        let lines = select_live_lines(
            "What would you like to configure?",
            "Choose action",
            &["Add config", "Edit config", "Delete config"],
            1,
            &[],
        );

        assert!(lines[0].contains("What would you like to configure?"));
        assert!(lines.iter().any(|line| line.contains("› Edit config")));
        assert!(!lines.iter().any(|line| line.contains("✓ Choose action")));
        assert!(!lines.iter().any(|line| line.contains("Back")));
        assert_eq!(lines.len(), 8);
    }

    #[test]
    fn wizard_main_actions_stay_focused() {
        assert_eq!(
            main_action_labels(),
            ["Add config", "Edit config", "Delete config"]
        );
    }
}
