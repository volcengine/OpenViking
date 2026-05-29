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
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};
use uuid::Uuid;

use crate::{
    base_client::BaseClient,
    config::{Config, DEFAULT_SELF_MANAGED_URL},
    error::{Error, Result},
    i18n::{self, Language, copy},
    theme::{self, Rgb},
};
use serde_json::Value;

use super::store::{
    ApiKeyRole, ConfigDraft, ConfigEntry, ConfigKind, ConfigStore, VOLCENGINE_CLOUD_URL,
    build_config, self_managed_allows_empty_api_key, validate_candidate_config,
    validate_candidate_config_with_role, validate_config_name, validation_error_copy,
};

const VOLCENGINE_API_KEY_URL: &str =
    "https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing";
const HEADER_TAGLINE: &str = "Context Database for AI Agents";
const HEADER_TAGLINE_ZH: &str = "AI Agent 上下文数据库";
const STATUS_BOX_PROBE_TIMEOUT_SECS: f64 = 3.0;

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
    let mut ui = LiveRegion::default();
    if !ensure_language_selected(&mut ui)? {
        return Ok(());
    }
    print_header();
    print_status_box(&store).await?;

    loop {
        let language = Language::current();
        match prompt_select(
            &mut ui,
            copy(
                language,
                "What would you like to configure?",
                "你想配置什么？",
            ),
            copy(language, "Choose action", "选择操作"),
            &main_action_labels_for_language(language),
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

fn ensure_language_selected(ui: &mut LiveRegion) -> Result<bool> {
    if i18n::has_saved_language() {
        return Ok(true);
    }

    let choices = ["English", "简体中文"];
    match prompt_select(
        ui,
        "Language / 语言",
        "Choose display language / 选择显示语言",
        &choices,
        0,
        &[format!(
            "{} {}",
            theme::muted("Change later:"),
            theme::command("ov language").bold()
        )],
    )? {
        PromptResult::Value(0) => i18n::save_language(Language::En)?,
        PromptResult::Value(1) => i18n::save_language(Language::ZhCn)?,
        PromptResult::Back | PromptResult::Quit => {
            print_cancelled(ui)?;
            return Ok(false);
        }
        PromptResult::Value(_) => unreachable!("selection is constrained by language list"),
    }

    ui.clear()?;
    Ok(true)
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

fn status_box_width() -> usize {
    wordmark_width()
}

fn nav_hint() -> &'static str {
    copy(
        Language::current(),
        "↑/↓ choose · Enter select · Esc back · Ctrl+C exit",
        "↑/↓ 选择 · Enter 确认 · Esc 返回 · Ctrl+C 退出",
    )
}

fn input_hint() -> &'static str {
    copy(
        Language::current(),
        "Enter continue · Esc back · Ctrl+C exit",
        "Enter 继续 · Esc 返回 · Ctrl+C 退出",
    )
}

fn section_add() -> &'static str {
    copy(
        Language::current(),
        "Create a new OpenViking config.",
        "创建新的 OpenViking 配置。",
    )
}

fn section_edit() -> &'static str {
    copy(
        Language::current(),
        "Update a saved config.",
        "更新已保存的配置。",
    )
}

fn section_delete() -> &'static str {
    copy(
        Language::current(),
        "Delete a saved config.",
        "删除已保存的配置。",
    )
}

fn kind_label(kind: ConfigKind) -> &'static str {
    match Language::current() {
        Language::En => kind.label(),
        Language::ZhCn => match kind {
            ConfigKind::VolcengineCloud => "火山引擎云",
            ConfigKind::SelfManaged => "自托管",
        },
    }
}

fn provider_labels(language: Language) -> [&'static str; 2] {
    match language {
        Language::En => ["Volcengine Cloud", "Self-Managed"],
        Language::ZhCn => ["火山引擎云", "自托管"],
    }
}

fn api_key_label(optional: bool) -> &'static str {
    match (Language::current(), optional) {
        (Language::En, true) => "API key (optional)",
        (Language::En, false) => "API key",
        (Language::ZhCn, true) => "API Key（可选）",
        (Language::ZhCn, false) => "API Key",
    }
}

pub(crate) fn main_action_labels() -> [&'static str; 3] {
    ["Add config", "Edit config", "Delete config"]
}

fn main_action_labels_for_language(language: Language) -> [&'static str; 3] {
    match language {
        Language::En => main_action_labels(),
        Language::ZhCn => ["添加配置", "编辑配置", "删除配置"],
    }
}

pub(crate) fn cloud_validation_failure_choices() -> [&'static str; 2] {
    ["Retry API key", "Cancel"]
}

fn cloud_validation_failure_choices_for_language(language: Language) -> [&'static str; 2] {
    match language {
        Language::En => cloud_validation_failure_choices(),
        Language::ZhCn => ["重新输入 API Key", "取消"],
    }
}

pub(crate) fn self_managed_validation_failure_choices() -> [&'static str; 3] {
    ["Edit server URL", "Edit API key", "Cancel"]
}

fn self_managed_validation_failure_choices_for_language(language: Language) -> [&'static str; 3] {
    match language {
        Language::En => self_managed_validation_failure_choices(),
        Language::ZhCn => ["修改服务器 URL", "修改 API Key", "取消"],
    }
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

fn edit_api_key_choice_labels_for_language(
    kind: ConfigKind,
    has_existing: bool,
    language: Language,
) -> Vec<&'static str> {
    if language == Language::En {
        return edit_api_key_choice_labels(kind, has_existing);
    }

    if !has_existing {
        return Vec::new();
    }

    match kind {
        ConfigKind::VolcengineCloud => vec!["保留现有 API Key", "替换 API Key"],
        ConfigKind::SelfManaged => vec!["保留现有 API Key", "替换 API Key", "清除 API Key"],
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

pub(crate) fn wordmark_gradient_color(column: usize, width: usize) -> Rgb {
    wordmark_gradient_color_for_theme(theme::active_theme(), column, width)
}

fn wordmark_gradient_color_for_theme(palette: theme::CliTheme, column: usize, width: usize) -> Rgb {
    if width <= 1 {
        return palette.wordmark_start;
    }

    let ratio = column as f32 / (width - 1) as f32;
    if ratio <= 0.56 {
        interpolate_rgb(palette.wordmark_start, palette.wordmark_mid, ratio / 0.56)
    } else {
        interpolate_rgb(
            palette.wordmark_mid,
            palette.wordmark_end,
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

fn tagline_ice_color_for_theme(palette: theme::CliTheme, column: usize, width: usize) -> Rgb {
    if width <= 1 {
        return palette.tagline_start;
    }

    let midpoint = width / 2;
    if column <= midpoint {
        let ratio = if midpoint == 0 {
            0.0
        } else {
            column as f32 / midpoint as f32
        };
        interpolate_rgb(palette.tagline_start, palette.tagline_mid, ratio)
    } else {
        let tail_width = (width - 1).saturating_sub(midpoint).max(1);
        let ratio = (column - midpoint) as f32 / tail_width as f32;
        interpolate_rgb(palette.tagline_mid, palette.tagline_end, ratio)
    }
}

fn tagline_texture_color(column: usize, width: usize) -> Rgb {
    let palette = theme::active_theme();
    let base = tagline_ice_color_for_theme(palette, column, width);
    let ratio = if width <= 1 {
        0.0
    } else {
        column as f32 / (width - 1) as f32
    };
    let center_glow = (1.0 - (ratio - 0.5).abs() * 2.0).clamp(0.0, 1.0);

    mix_rgb(base, palette.wordmark_start, center_glow * 0.18)
}

fn mix_rgb(base: Rgb, overlay: Rgb, amount: f32) -> Rgb {
    interpolate_rgb(base, overlay, amount)
}

fn styled_tagline(text: &str) -> String {
    let width = display_width(text).max(1);
    let mut rendered = String::new();
    let mut column = 0usize;

    for ch in text.chars() {
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
        column += UnicodeWidthChar::width(ch).unwrap_or(0);
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
    println!(
        "{}",
        styled_box_title_line(
            copy(Language::current(), HEADER_TAGLINE, HEADER_TAGLINE_ZH),
            width
        )
    );
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
        config.profile,
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
    Empty,
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
            Self::Empty => String::new(),
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
            Self::Empty => String::new(),
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
    let visible_title = truncate_to_width(&title, inner_width);
    let title_width = display_width(&visible_title);
    let left = inner_width.saturating_sub(title_width) / 2;
    let right = inner_width.saturating_sub(title_width + left);
    let Rgb(red, green, blue) = theme::active_theme().border.rgb_fallback();

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
    let Rgb(red, green, blue) = theme::active_theme().border.rgb_fallback();
    let Rgb(version_red, version_green, version_blue) =
        theme::active_theme().version.rgb_fallback();
    let inner_width = width.saturating_sub(2);
    let title = format!(" {title} ");
    let visible_title = truncate_to_width(&title, inner_width);
    let title_width = display_width(&visible_title);
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
    let Rgb(red, green, blue) = theme::active_theme().border.rgb_fallback();
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
    rendered.push_str(&" ".repeat(width.saturating_sub(display_width(&visible))));
    rendered
}

fn logo_glass_color(_ch: char, column: usize, row: usize, width: usize) -> Rgb {
    logo_glass_color_for_theme(theme::active_theme(), column, row, width)
}

fn logo_glass_color_for_theme(
    palette: theme::CliTheme,
    column: usize,
    row: usize,
    width: usize,
) -> Rgb {
    if width <= 1 {
        return palette.wordmark_start;
    }

    let column_ratio = column as f32 / (width - 1) as f32;
    let row_height = OV_LOGO_LINES.len().saturating_sub(1).max(1);
    let row_ratio = row as f32 / row_height as f32;
    let ratio = (column_ratio * 0.4 + row_ratio * 0.6).clamp(0.0, 1.0);

    if ratio <= 0.46 {
        interpolate_rgb(palette.wordmark_start, palette.wordmark_mid, ratio / 0.46)
    } else {
        interpolate_rgb(
            palette.wordmark_mid,
            palette.logo_end,
            (ratio - 0.46) / 0.54,
        )
    }
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

fn display_width(text: &str) -> usize {
    UnicodeWidthStr::width(text)
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
                format!("Active: {} ({})", entry.name, kind_label(entry.kind))
            } else {
                format!(
                    "Active: unnamed ({})",
                    kind_label(ConfigKind::from_config(config))
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
    let mut url = DEFAULT_SELF_MANAGED_URL.to_string();
    let mut api_key: Option<String> = None;
    let mut account: Option<String> = None;
    let mut user: Option<String> = None;
    let mut identity_mode: Option<IdentityMode> = None;

    loop {
        match stage {
            Stage::Kind => match prompt_select(
                ui,
                section_add(),
                copy(
                    Language::current(),
                    "Where should this CLI connect?",
                    "CLI 要连接到哪里？",
                ),
                &provider_labels(Language::current()),
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
                    url = DEFAULT_SELF_MANAGED_URL.to_string();
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
            Stage::Name => {
                match prompt_add_config_name(ui, section_add(), add_config_name_label())? {
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
                        url = DEFAULT_SELF_MANAGED_URL.to_string();
                        stage = Stage::Kind;
                    }
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::Url => match prompt_text(
                ui,
                section_add(),
                copy(Language::current(), "Server URL", "服务器 URL"),
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
                    api_key_label(self_managed_allows_empty_api_key(&url))
                } else {
                    api_key_label(false)
                };
                let allow_empty_api_key =
                    kind == ConfigKind::SelfManaged && self_managed_allows_empty_api_key(&url);
                match prompt_text(
                    ui,
                    section_add(),
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
                    section_add(),
                    copy(Language::current(), "Account ID", "账户 ID"),
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
                match prompt_identity_value(
                    ui,
                    section_add(),
                    copy(Language::current(), "User ID", "用户 ID"),
                    mode,
                )? {
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
                match validate_draft(ui, section_add(), &draft).await {
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
                            section_add(),
                            copy(Language::current(), "Save config?", "保存配置？"),
                            SaveActionSet::Add,
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
                        let helper_lines = vec![
                            theme::error(localized_validation_error(kind, &error)).to_string(),
                        ];
                        let choices: Vec<&str> = if kind == ConfigKind::VolcengineCloud {
                            cloud_validation_failure_choices_for_language(Language::current())
                                .to_vec()
                        } else {
                            self_managed_validation_failure_choices_for_language(Language::current()).to_vec()
                        };
                        match prompt_select(
                            ui,
                            section_add(),
                            copy(
                                Language::current(),
                                "Validation failed. What next?",
                                "验证失败，下一步？",
                            ),
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
        let helper_lines = vec![
            theme::warning(copy(
                Language::current(),
                "No saved configs to edit.",
                "没有可编辑的配置。",
            ))
            .to_string(),
        ];
        let _ = prompt_select(
            ui,
            section_edit(),
            copy(Language::current(), "Nothing to edit.", "没有可编辑项。"),
            &[copy(Language::current(), "Back", "返回")],
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
                section_edit(),
                copy(Language::current(), "Config to edit", "要编辑的配置"),
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
                match prompt_config_name(
                    ui,
                    section_edit(),
                    copy(Language::current(), "Config name", "配置名称"),
                    Some(&name),
                )? {
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
                section_edit(),
                copy(Language::current(), "Server URL", "服务器 URL"),
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

                match prompt_select(
                    ui,
                    section_edit(),
                    api_key_label(false),
                    &edit_api_key_choice_labels_for_language(
                        kind,
                        has_existing,
                        Language::current(),
                    ),
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
                    api_key_label(true)
                } else {
                    api_key_label(false)
                };
                let helper_lines = if kind == ConfigKind::VolcengineCloud {
                    volcengine_api_key_helper_lines()
                } else {
                    self_managed_api_key_helper_lines(allow_empty)
                };
                match prompt_text(
                    ui,
                    section_edit(),
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
                match prompt_identity_value(
                    ui,
                    section_edit(),
                    copy(Language::current(), "Account ID", "账户 ID"),
                    mode,
                )? {
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
                match prompt_identity_value(
                    ui,
                    section_edit(),
                    copy(Language::current(), "User ID", "用户 ID"),
                    mode,
                )? {
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
                match validate_draft(ui, section_edit(), &draft).await {
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
                            section_edit(),
                            if configs[selected].is_active {
                                copy(
                                    Language::current(),
                                    "Save changes to active config?",
                                    "保存当前配置的更改？",
                                )
                            } else {
                                copy(Language::current(), "Save changes?", "保存更改？")
                            },
                            if configs[selected].is_active {
                                SaveActionSet::EditActive
                            } else {
                                SaveActionSet::EditInactive
                            },
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
                        let helper_lines = vec![
                            theme::error(localized_validation_error(kind, &error)).to_string(),
                        ];
                        let choices = if kind == ConfigKind::VolcengineCloud {
                            cloud_validation_failure_choices_for_language(Language::current())
                                .to_vec()
                        } else {
                            self_managed_validation_failure_choices_for_language(Language::current()).to_vec()
                        };
                        match prompt_select(
                            ui,
                            section_edit(),
                            copy(
                                Language::current(),
                                "Validation failed. What next?",
                                "验证失败，下一步？",
                            ),
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
        let helper_lines = vec![
            theme::warning(copy(
                Language::current(),
                "No saved configs to delete.",
                "没有可删除的配置。",
            ))
            .to_string(),
        ];
        let _ = prompt_select(
            ui,
            section_delete(),
            copy(Language::current(), "Nothing to delete.", "没有可删除项。"),
            &[copy(Language::current(), "Back", "返回")],
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
                section_delete(),
                copy(Language::current(), "Config to delete", "要删除的配置"),
                &configs,
            )? {
                PromptResult::Value(index) => {
                    selected = index;
                    if configs[index].is_active {
                        let helper_lines = active_delete_block_helper_lines();
                        let _ = prompt_select(
                            ui,
                            section_delete(),
                            copy(
                                Language::current(),
                                "Active config cannot be deleted.",
                                "不能删除当前配置。",
                            ),
                            &[copy(Language::current(), "Back", "返回")],
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
                match confirm(ui, section_delete(), &delete_confirm_prompt(name), false)? {
                    PromptResult::Value(true) => {
                        ui.clear()?;
                        store.delete_config(name)?;
                        println!();
                        println!(
                            "{} {}",
                            theme::success("✓"),
                            theme::success(deleted_config_message(name))
                        );
                        println!(
                            "{} {}",
                            theme::muted(copy(Language::current(), "Removed:", "已删除：")),
                            store
                                .saved_config_path(name)?
                                .display()
                                .to_string()
                                .magenta()
                        );
                        println!(
                            "{} {}",
                            theme::muted(copy(Language::current(), "Next:", "下一步：")),
                            next_step_copy()
                        );
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
    ui.render(&status_live_lines(
        section,
        copy(
            Language::current(),
            "Validating connection...",
            "正在验证连接...",
        ),
    ))?;
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
        SaveOutcome::Activated => saved_message_activated(name),
        SaveOutcome::SavedOnly => saved_message_only(name),
        SaveOutcome::UpdatedActive => saved_message_updated_active(name),
    };
    println!("{} {}", theme::success("✓"), theme::success(message));
    println!(
        "{} {}",
        theme::muted(copy(Language::current(), "Saved to:", "保存到：")),
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
                theme::muted(copy(Language::current(), "Active config:", "当前配置：")),
                store.active_path().display().to_string().magenta()
            );
        }
        SaveOutcome::SavedOnly => {
            println!(
                "{} {}",
                theme::muted(copy(Language::current(), "Activate later:", "稍后启用：")),
                theme::command("ov config switch")
            );
        }
    }
    println!(
        "{} {}",
        theme::muted(copy(Language::current(), "Next:", "下一步：")),
        next_step_copy()
    );
    Ok(())
}

fn next_step_copy() -> String {
    match Language::current() {
        Language::En => format!("Run {} to get started.", theme::command("ov --help").bold()),
        Language::ZhCn => format!("运行 {} 查看可用命令。", theme::command("ov --help").bold()),
    }
}

fn saved_message_activated(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Saved config '{name}' and made it active."),
        Language::ZhCn => format!("已保存配置 '{name}'，并设为当前配置。"),
    }
}

fn saved_message_only(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Saved config '{name}'."),
        Language::ZhCn => format!("已保存配置 '{name}'。"),
    }
}

fn saved_message_updated_active(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Saved active config '{name}'."),
        Language::ZhCn => format!("已保存当前配置 '{name}'。"),
    }
}

pub(crate) fn add_config_name_label() -> &'static str {
    copy(
        Language::current(),
        "Config name (optional)",
        "配置名称（可选）",
    )
}

fn add_config_name_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "Leave empty to generate one.",
            "留空将自动生成名称。",
        ))
        .to_string(),
    ]
}

pub(crate) fn volcengine_api_key_helper_lines() -> Vec<String> {
    let language = Language::current();
    vec![
        format!(
            "{} {}",
            theme::muted(copy(language, "Get your API key:", "获取 API Key：")),
            VOLCENGINE_API_KEY_URL
        ),
        theme::muted(copy(
            language,
            "Go to User Management → API Key to view and copy your key.",
            "进入用户管理 → API Key 查看并复制。",
        ))
        .to_string(),
    ]
}

pub(crate) fn self_managed_api_key_helper_lines(allow_empty: bool) -> Vec<String> {
    let copy = if allow_empty {
        copy(
            Language::current(),
            "Optional for local servers. Add one if auth is enabled.",
            "本地服务可不填；如果启用了认证，请填写。",
        )
    } else {
        copy(
            Language::current(),
            "Required for remote self-managed servers.",
            "远程自托管服务需要 API Key。",
        )
    };
    vec![theme::muted(copy).to_string()]
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
            helper_lines.push(theme::error(value).to_string());
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
            .map(|value| vec![theme::error(value).to_string()])
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
            vec![
                theme::muted(copy(
                    Language::current(),
                    "Local no-key identity.",
                    "本地无密钥身份。",
                ))
                .to_string(),
            ],
        ),
        IdentityMode::RootKey => (
            None,
            None,
            vec![
                theme::muted(copy(
                    Language::current(),
                    "Root API keys require an explicit account and user.",
                    "Root API Key 需要明确的账户和用户。",
                ))
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
    let label = format!("{} - {}", entry.name, kind_label(entry.kind));
    if entry.is_active {
        format!("{} {}", label, theme::error(active_badge()).bold())
    } else {
        label
    }
}

fn active_badge() -> &'static str {
    copy(Language::current(), "[Active]", "[当前]")
}

fn active_delete_block_helper_lines() -> Vec<String> {
    match Language::current() {
        Language::En => vec![
            theme::error("Deleting the active config is blocked.").to_string(),
            format!(
                "{} {} {}",
                theme::muted("Run"),
                theme::command("ov config switch").bold(),
                theme::muted("to choose another config, then delete this one.")
            ),
        ],
        Language::ZhCn => vec![
            theme::error("不能删除当前配置。").to_string(),
            format!(
                "{} {} {}",
                theme::muted("请先运行"),
                theme::command("ov config switch").bold(),
                theme::muted("切换到其他配置，然后再删除。")
            ),
        ],
    }
}

fn delete_confirm_prompt(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Delete config '{name}'?"),
        Language::ZhCn => format!("删除配置 '{name}'？"),
    }
}

fn localized_validation_error(kind: ConfigKind, error: &Error) -> String {
    match Language::current() {
        Language::En => validation_error_copy(kind, error),
        Language::ZhCn => match kind {
            ConfigKind::VolcengineCloud => "验证失败。请检查 API Key 后重试。".to_string(),
            ConfigKind::SelfManaged => {
                "验证失败。请检查服务器 URL，以及是否需要 API Key。".to_string()
            }
        },
    }
}

fn deleted_config_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Deleted config '{name}'."),
        Language::ZhCn => format!("已删除配置 '{name}'。"),
    }
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SaveActionSet {
    Add,
    EditActive,
    EditInactive,
}

impl SaveActionSet {
    fn labels(self, language: Language) -> Vec<&'static str> {
        match (self, language) {
            (Self::Add, Language::En) => add_save_action_labels(),
            (Self::Add, Language::ZhCn) => vec!["保存并设为当前配置", "仅保存", "取消"],
            (Self::EditActive, Language::En) => edit_save_action_labels(true),
            (Self::EditActive, Language::ZhCn) => vec!["保存更改", "取消"],
            (Self::EditInactive, Language::En) => edit_save_action_labels(false),
            (Self::EditInactive, Language::ZhCn) => {
                vec!["仅保存", "保存并设为当前配置", "取消"]
            }
        }
    }

    fn action(self, index: usize) -> SaveAction {
        match (self, index) {
            (Self::Add, 0) => SaveAction::SaveAndActivate,
            (Self::Add, 1) => SaveAction::SaveOnly,
            (Self::Add, _) => SaveAction::Cancel,
            (Self::EditActive, 0) => SaveAction::SaveActive,
            (Self::EditActive, _) => SaveAction::Cancel,
            (Self::EditInactive, 0) => SaveAction::SaveOnly,
            (Self::EditInactive, 1) => SaveAction::SaveAndActivate,
            (Self::EditInactive, _) => SaveAction::Cancel,
        }
    }
}

fn prompt_save_action(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    action_set: SaveActionSet,
    default: usize,
) -> Result<PromptResult<SaveAction>> {
    let items = action_set.labels(Language::current());
    match prompt_select(ui, section, prompt, &items, default, &[])? {
        PromptResult::Value(index) => Ok(PromptResult::Value(action_set.action(index))),
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
                            error = Some(
                                copy(
                                    Language::current(),
                                    "Value cannot be empty.",
                                    "内容不能为空。",
                                )
                                .to_string(),
                            );
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
                        if let Some(ch) = value.pop() {
                            raw_write(erase_sequence_for_char(ch, secret))?;
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

fn erase_sequence_for_char(ch: char, secret: bool) -> String {
    let width = if secret {
        1
    } else {
        UnicodeWidthChar::width(ch).unwrap_or(1).max(1)
    };
    "\x08 \x08".repeat(width)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InputValueLabel {
    Default,
    Current,
}

impl InputValueLabel {
    fn text(self) -> &'static str {
        match (self, Language::current()) {
            (Self::Default, Language::En) => "Default:",
            (Self::Default, Language::ZhCn) => "默认值：",
            (Self::Current, Language::En) => "Current:",
            (Self::Current, Language::ZhCn) => "当前值：",
        }
    }
}

fn confirm(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: bool,
) -> Result<PromptResult<bool>> {
    let items = match Language::current() {
        Language::En => ["Yes", "No"],
        Language::ZhCn => ["是", "否"],
    };
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
        format!(
            "{} {}",
            theme::section_marker("◆").bold(),
            theme::strong(section)
        ),
        String::new(),
        format!("{} {}", theme::prompt("?").bold(), theme::strong(prompt)),
        format!("  {}", theme::muted(nav_hint())),
    ];

    if !helper_lines.is_empty() {
        lines.push(String::new());
        lines.extend(helper_lines.iter().map(|line| format!("  {line}")));
    }

    lines.push(String::new());
    lines.extend(items.iter().enumerate().map(|(index, item)| {
        let item = item.to_string();
        if index == selected {
            theme::selection(format!("  › {item}")).bold().to_string()
        } else {
            format!("    {}", theme::body(item))
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
        format!(
            "{} {}",
            theme::section_marker("◆").bold(),
            theme::strong(section)
        ),
        String::new(),
        format!("{} {}", theme::prompt("?").bold(), theme::strong(prompt)),
        format!("  {}", theme::muted(input_hint())),
    ];

    if !helper_lines.is_empty() {
        lines.push(String::new());
        lines.extend(helper_lines.iter().map(|line| format!("  {line}")));
    }

    if let (Some(default_value), Some(value_label)) = (default, value_label) {
        let rendered_default = if secret {
            if default_value.trim().is_empty() {
                theme::muted("(empty)").to_string()
            } else {
                theme::muted("(existing value)").to_string()
            }
        } else {
            theme::muted(default_value).to_string()
        };
        lines.push(format!(
            "  {} {}",
            theme::muted(value_label.text()),
            rendered_default
        ));
    }

    if let Some(error) = error {
        lines.push(format!("  {}", theme::error(error)));
    }

    lines.push(String::new());
    lines
}

fn status_live_lines(section: &str, status: &str) -> Vec<String> {
    vec![
        format!(
            "{} {}",
            theme::section_marker("◆").bold(),
            theme::strong(section)
        ),
        String::new(),
        format!("{} {}", theme::command("…").bold(), theme::strong(status)),
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
        theme::warning(copy(
            Language::current(),
            "Cancelled. No partial configuration was written.",
            "已取消。未写入任何未完成配置。",
        ))
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
        IdentityMode, InputValueLabel, OV_LOGO_LINES, Rgb, StatusBoxRuntime,
        active_delete_block_helper_lines, active_summary_lines, active_summary_render_parts,
        add_config_name_label, add_save_action_labels, allocate_config_name, box_content_line,
        box_footer_line, box_title_line, cloud_validation_failure_choices, config_select_label,
        display_config_home, display_width, edit_api_key_choice_labels, edit_save_action_labels,
        erase_sequence_for_char, extract_models_from_status_payload, identity_prompt_parts,
        input_live_lines, logo_glass_color_for_theme, main_action_labels, next_step_copy,
        ov_logo_width, saved_summary_render_parts, select_live_lines,
        self_managed_api_key_helper_lines, self_managed_validation_failure_choices,
        should_prompt_root_identity, status_box_lines, status_box_lines_with_runtime,
        status_box_width, status_payload_is_healthy, tagline_ice_color_for_theme,
        validate_config_name, volcengine_api_key_helper_lines, wizard_header_lines,
        wordmark_gradient_color_for_theme, wordmark_lines, wordmark_width,
    };
    use crate::config::Config;
    use crate::config_wizard::store::{ApiKeyRole, ConfigEntry, ConfigKind, ConfigStore};
    use crate::theme::{self, ThemeColor};
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
    fn erase_sequence_matches_display_width() {
        assert_eq!(erase_sequence_for_char('a', false), "\x08 \x08");
        assert_eq!(erase_sequence_for_char('中', false), "\x08 \x08\x08 \x08");
        assert_eq!(erase_sequence_for_char('中', true), "\x08 \x08");
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
        assert_eq!(display_width(&lines[0]), status_box_width());
        assert!(
            lines
                .last()
                .expect("footer should render")
                .find(&version)
                .expect("version should render")
                > status_box_width() / 2
        );
        for line in &lines {
            assert_eq!(display_width(line), status_box_width(), "{line:?}");
        }
    }

    #[test]
    fn status_box_cjk_lines_align_to_display_width() {
        let width = status_box_width();
        let title = box_title_line("AI Agent 上下文数据库", width);
        let content = box_content_line("", "当前配置： VPS_ROOT (自托管)", width);
        let footer = box_footer_line("v0.0.0", width);

        for line in [title, content, footer] {
            assert_eq!(display_width(&line), width, "{line:?}");
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
        let width = display_width(wordmark[0]);

        for line in wordmark {
            assert_eq!(display_width(line), width, "{line:?} should match");
        }
    }

    #[test]
    fn wordmark_is_subtly_wider_for_viking_readability() {
        let width = wordmark_width();

        assert!(
            (79..=83).contains(&width),
            "wordmark should widen only enough to make VIKING readable; got {width}"
        );
        assert!(
            wordmark_lines()[0].contains("██╗   ██╗ ██╗ ██╗  ██╗ ██╗"),
            "VIKING should have subtle breathing room without obvious gaps"
        );
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
                display_width(line.trim_end()) >= width - 1,
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
        let palette = theme::active_theme();

        assert_eq!(
            wordmark_gradient_color_for_theme(palette, 0, width),
            palette.wordmark_start
        );
        assert_eq!(
            wordmark_gradient_color_for_theme(palette, width - 1, width),
            palette.wordmark_end
        );
        let middle = wordmark_gradient_color_for_theme(palette, width / 2, width);
        assert!(
            middle.0 < palette.wordmark_start.0 && middle.1 < palette.wordmark_start.1,
            "wordmark should visibly darken across the line"
        );
    }

    #[test]
    fn tagline_ice_color_runs_pearl_jade() {
        let width = display_width("Context Database for AI Agents");
        let palette = theme::active_theme();

        assert_eq!(
            tagline_ice_color_for_theme(palette, 0, width),
            palette.tagline_start
        );
        assert_eq!(
            tagline_ice_color_for_theme(palette, width / 2, width),
            palette.tagline_mid
        );
        assert_eq!(
            tagline_ice_color_for_theme(palette, width - 1, width),
            palette.tagline_end
        );
    }

    #[test]
    fn status_box_border_uses_pearl_jade() {
        assert_eq!(
            theme::active_theme().border,
            ThemeColor::TrueColor(Rgb(0, 128, 128))
        );
    }

    #[test]
    fn status_box_footer_version_uses_pearl_jade_accent() {
        assert_eq!(
            theme::active_theme().version,
            ThemeColor::TrueColor(Rgb(0, 128, 128))
        );
    }

    #[test]
    fn status_box_logo_uses_diagonal_pearl_jade_gradient() {
        let width = ov_logo_width();
        let palette = theme::active_theme();

        assert_eq!(
            logo_glass_color_for_theme(palette, 0, 0, width),
            palette.wordmark_start
        );
        assert_eq!(
            logo_glass_color_for_theme(palette, width - 1, 13, width),
            palette.logo_end
        );
        let middle = logo_glass_color_for_theme(palette, width / 2, 7, width);
        assert!(
            middle.1 > palette.logo_end.1 && middle.1 < palette.wordmark_start.1,
            "logo middle should sit between the light and dark gradient stops"
        );

        let upper = logo_glass_color_for_theme(palette, width / 2, 1, width);
        let lower = logo_glass_color_for_theme(palette, width / 2, 12, width);
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
        assert!(cloud.iter().any(|line| {
            line.contains("Go to User Management → API Key to view and copy your key.")
        }));
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
