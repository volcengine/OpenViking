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
    terminal::{self, Clear, ClearType, disable_raw_mode, enable_raw_mode},
};
use unicode_width::UnicodeWidthChar;
use uuid::Uuid;

use crate::{
    base_client::BaseClient,
    config::{Config, DEFAULT_CUSTOM_URL},
    error::{Error, Result},
    i18n::{self, Language, copy},
    terminal_ui::{RenderedRegion, display_width, rendered_line_rows, rendered_row_count},
    theme::{self, Rgb},
};
use serde_json::Value;

use super::store::{
    ApiKeyRole, ConfigDraft, ConfigEntry, ConfigKind, ConfigStore, IdentityField,
    OPENVIKING_SERVICE_URL, build_config, custom_allows_empty_api_key, validate_candidate_config,
    validate_candidate_config_with_role, validate_config, validate_config_name,
    validate_identity_value, validation_error_copy,
};

const OPENVIKING_SERVICE_API_KEY_URL: &str =
    "https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing";
const HEADER_TAGLINE: &str = "Context Database for AI Agents";
const HEADER_TAGLINE_ZH: &str = "AI Agent 上下文数据库";
const STATUS_BOX_PROBE_TIMEOUT_SECS: f64 = 3.0;
const TEXT_INPUT_PROMPT: &str = "  > ";

#[derive(Clone, Copy, PartialEq, Eq)]
enum IdentityMode {
    LocalNoKey,
    RootKey,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum CustomKeyMode {
    NoKey,
    UserKey,
    RootKey,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum CustomEditKeyAction {
    Keep,
    SetUserKey,
    SetRootKey,
    UseRootForNormal,
    ClearRootKey,
    ClearAllKeys,
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

// Full mode needs enough height for the standalone wordmark, logo art,
// status box, and prompts without wrapping into each other.
const FULL_STATUS_BOX_MIN_ROWS: u16 = 30;

pub async fn run_config_wizard() -> Result<()> {
    let store = ConfigStore::new()?;
    run_config_wizard_with_store(store).await
}

async fn run_config_wizard_with_store(store: ConfigStore) -> Result<()> {
    let mut ui = LiveRegion::default();
    if !ensure_language_selected(&mut ui)? {
        return Ok(());
    }
    print_full_header(live_status_box_frame().mode);
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
                if run_switch_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(2) => {
                if run_edit_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(3) => {
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

fn status_box_width() -> usize {
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

fn live_status_box_frame() -> StatusBoxFrame {
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
struct StatusBoxFrame {
    width: usize,
    mode: StatusBoxMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StatusBoxMode {
    Full,
    Compact,
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

fn section_switch() -> &'static str {
    copy(
        Language::current(),
        "Switch to a saved config.",
        "切换到已保存的配置。",
    )
}

fn section_delete() -> &'static str {
    copy(
        Language::current(),
        "Delete a saved config.",
        "删除已保存的配置。",
    )
}

fn compact_kind_label(kind: ConfigKind) -> &'static str {
    match Language::current() {
        Language::En => kind.compact_label(),
        Language::ZhCn => match kind {
            ConfigKind::OpenVikingService => "OpenViking 服务",
            ConfigKind::Custom => "自定义",
        },
    }
}

fn provider_labels(language: Language) -> [&'static str; 2] {
    match language {
        Language::En => [ConfigKind::OpenVikingService.label(), "Custom"],
        Language::ZhCn => ["OpenViking 服务（火山引擎云）", "自定义"],
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

fn custom_api_key_input_label(mode: CustomKeyMode) -> &'static str {
    match (Language::current(), mode) {
        (Language::En, CustomKeyMode::RootKey) => "Root API key",
        (Language::En, CustomKeyMode::UserKey) => "User API key",
        (Language::En, CustomKeyMode::NoKey) => "API key",
        (Language::ZhCn, CustomKeyMode::RootKey) => "Root API Key",
        (Language::ZhCn, CustomKeyMode::UserKey) => "User API Key",
        (Language::ZhCn, CustomKeyMode::NoKey) => "API Key",
    }
}

fn custom_api_key_input_helper_lines(mode: CustomKeyMode) -> Vec<String> {
    let copy = match (Language::current(), mode) {
        (Language::En, CustomKeyMode::RootKey) => {
            "For self-hosted admin setup and --sudo commands."
        }
        (Language::En, CustomKeyMode::UserKey) => "For normal OpenViking commands.",
        (Language::En, CustomKeyMode::NoKey) => {
            "Optional for local servers. Add one if auth is enabled."
        }
        (Language::ZhCn, CustomKeyMode::RootKey) => "用于自定义管理初始化和 --sudo 命令。",
        (Language::ZhCn, CustomKeyMode::UserKey) => "用于常规 OpenViking 命令。",
        (Language::ZhCn, CustomKeyMode::NoKey) => "本地服务可不填；如果启用了认证，请填写。",
    };
    vec![theme::muted(copy).to_string()]
}

#[cfg(test)]
fn custom_key_mode_labels(allow_empty: bool) -> Vec<&'static str> {
    custom_key_mode_labels_for_language(allow_empty, Language::En)
}

fn custom_key_mode_labels_for_language(allow_empty: bool, language: Language) -> Vec<&'static str> {
    match (allow_empty, language) {
        (true, Language::En) => vec!["No key / local dev", "User API key", "Root API key"],
        (false, Language::En) => vec!["User API key", "Root API key"],
        (true, Language::ZhCn) => vec!["无密钥 / 本地开发", "User API Key", "Root API Key"],
        (false, Language::ZhCn) => vec!["User API Key", "Root API Key"],
    }
}

fn custom_key_mode_for_selection(allow_empty: bool, index: usize) -> CustomKeyMode {
    match (allow_empty, index) {
        (true, 0) => CustomKeyMode::NoKey,
        (true, 1) | (false, 0) => CustomKeyMode::UserKey,
        _ => CustomKeyMode::RootKey,
    }
}

fn edit_custom_key_actions(
    has_normal_user_key: bool,
    has_root_key: bool,
) -> Vec<CustomEditKeyAction> {
    if !has_normal_user_key && !has_root_key {
        return vec![
            CustomEditKeyAction::SetUserKey,
            CustomEditKeyAction::SetRootKey,
        ];
    }

    let mut actions = vec![
        CustomEditKeyAction::Keep,
        CustomEditKeyAction::SetUserKey,
        CustomEditKeyAction::SetRootKey,
    ];
    if has_normal_user_key && has_root_key {
        actions.push(CustomEditKeyAction::UseRootForNormal);
    }
    if has_root_key {
        actions.push(CustomEditKeyAction::ClearRootKey);
    }
    actions.push(CustomEditKeyAction::ClearAllKeys);
    actions
}

#[cfg(test)]
fn edit_custom_key_action_labels(
    has_normal_user_key: bool,
    has_root_key: bool,
) -> Vec<&'static str> {
    edit_custom_key_actions(has_normal_user_key, has_root_key)
        .into_iter()
        .map(custom_edit_key_action_label)
        .collect()
}

fn custom_edit_key_action_label(action: CustomEditKeyAction) -> &'static str {
    match (Language::current(), action) {
        (Language::En, CustomEditKeyAction::Keep) => "Keep existing API keys",
        (Language::En, CustomEditKeyAction::SetUserKey) => "Set normal user API key",
        (Language::En, CustomEditKeyAction::SetRootKey) => "Set root API key",
        (Language::En, CustomEditKeyAction::UseRootForNormal) => "Use root key for normal commands",
        (Language::En, CustomEditKeyAction::ClearRootKey) => "Clear root API key",
        (Language::En, CustomEditKeyAction::ClearAllKeys) => "Clear all API keys",
        (Language::ZhCn, CustomEditKeyAction::Keep) => "保留现有 API Key",
        (Language::ZhCn, CustomEditKeyAction::SetUserKey) => "设置普通用户 API Key",
        (Language::ZhCn, CustomEditKeyAction::SetRootKey) => "设置 Root API Key",
        (Language::ZhCn, CustomEditKeyAction::UseRootForNormal) => "使用 Root Key 执行常规命令",
        (Language::ZhCn, CustomEditKeyAction::ClearRootKey) => "清除 Root API Key",
        (Language::ZhCn, CustomEditKeyAction::ClearAllKeys) => "清除所有 API Key",
    }
}

fn root_key_redirect_labels() -> [&'static str; 3] {
    match Language::current() {
        Language::En => ["Continue as root key", "Re-enter user key", "Cancel"],
        Language::ZhCn => ["作为 Root Key 继续", "重新输入用户 Key", "取消"],
    }
}

fn root_key_redirect_helper_lines() -> Vec<String> {
    vec![
        theme::warning(copy(
            Language::current(),
            "This key has root access. Root keys are for admin and --sudo commands.",
            "此 Key 拥有 Root 权限。Root Key 用于管理和 --sudo 命令。",
        ))
        .to_string(),
    ]
}

fn user_key_redirect_labels() -> [&'static str; 3] {
    match Language::current() {
        Language::En => ["Continue as user key", "Re-enter root key", "Cancel"],
        Language::ZhCn => ["作为用户 Key 继续", "重新输入 Root Key", "取消"],
    }
}

fn user_key_redirect_helper_lines() -> Vec<String> {
    vec![
        theme::warning(copy(
            Language::current(),
            "This key does not have root access. User keys are for normal commands.",
            "此 Key 没有 Root 权限。用户 Key 用于常规命令。",
        ))
        .to_string(),
    ]
}

fn should_confirm_detected_root_key(
    kind: ConfigKind,
    key_mode: Option<CustomKeyMode>,
    api_key_role: Option<ApiKeyRole>,
) -> bool {
    kind == ConfigKind::Custom
        && key_mode == Some(CustomKeyMode::UserKey)
        && api_key_role == Some(ApiKeyRole::Root)
}

fn should_confirm_detected_user_key(
    kind: ConfigKind,
    key_mode: Option<CustomKeyMode>,
    api_key_role: Option<ApiKeyRole>,
) -> bool {
    kind == ConfigKind::Custom
        && key_mode == Some(CustomKeyMode::RootKey)
        && api_key_role == Some(ApiKeyRole::Regular)
}

fn has_non_empty(value: Option<&str>) -> bool {
    value.is_some_and(|value| !value.trim().is_empty())
}

fn has_normal_user_key(api_key: Option<&str>, root_api_key: Option<&str>) -> bool {
    let Some(api_key) = api_key.filter(|value| !value.trim().is_empty()) else {
        return false;
    };
    root_api_key
        .filter(|value| !value.trim().is_empty())
        .is_none_or(|root_api_key| root_api_key != api_key)
}

pub(crate) fn main_action_labels() -> [&'static str; 4] {
    [
        "Add Config",
        "Switch Config",
        "Edit Config",
        "Delete Config",
    ]
}

fn main_action_labels_for_language(language: Language) -> [&'static str; 4] {
    match language {
        Language::En => main_action_labels(),
        Language::ZhCn => ["添加配置", "切换配置", "编辑配置", "删除配置"],
    }
}

pub(crate) fn openviking_service_validation_failure_choices() -> [&'static str; 2] {
    ["Retry API key", "Cancel"]
}

fn openviking_service_validation_failure_choices_for_language(
    language: Language,
) -> [&'static str; 2] {
    match language {
        Language::En => openviking_service_validation_failure_choices(),
        Language::ZhCn => ["重新输入 API Key", "取消"],
    }
}

pub(crate) fn custom_validation_failure_choices() -> [&'static str; 3] {
    ["Edit server URL", "Edit API key", "Cancel"]
}

fn custom_validation_failure_choices_for_language(language: Language) -> [&'static str; 3] {
    match language {
        Language::En => custom_validation_failure_choices(),
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
        ConfigKind::OpenVikingService => vec!["Keep existing API key", "Replace API key"],
        ConfigKind::Custom => {
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
        ConfigKind::OpenVikingService => vec!["保留现有 API Key", "替换 API Key"],
        ConfigKind::Custom => vec!["保留现有 API Key", "替换 API Key", "清除 API Key"],
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

fn print_full_header(mode: StatusBoxMode) {
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
    let width = wordmark_width().max(1);
    let mut rendered = String::new();

    for (column, ch) in line.chars().enumerate() {
        if ch.is_whitespace() {
            rendered.push(ch);
        } else {
            let rgb = header_display_rgb(wordmark_gradient_color(column, width), color_level);
            rendered.push_str(&theme::style_rgb_for_level(
                ch.to_string(),
                rgb,
                true,
                color_level,
            ));
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
    column: usize,
    row: usize,
    width: usize,
    logo_height: usize,
) -> Rgb {
    if width <= 1 {
        return palette.wordmark_start;
    }

    let column_ratio = column as f32 / (width - 1) as f32;
    let row_height = logo_height.saturating_sub(1).max(1);
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

async fn run_add_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Kind,
        Name,
        Url,
        KeyMode,
        ApiKey,
        Account,
        User,
        Validate,
    }

    let mut stage = Stage::Kind;
    let mut kind = ConfigKind::Custom;
    let mut name: Option<String> = None;
    let mut url = DEFAULT_CUSTOM_URL.to_string();
    let mut api_key: Option<String> = None;
    let mut root_api_key: Option<String> = None;
    let mut account: Option<String> = None;
    let mut user: Option<String> = None;
    let mut identity_mode: Option<IdentityMode> = None;
    let mut key_mode: Option<CustomKeyMode> = None;

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
                    kind = ConfigKind::OpenVikingService;
                    url = OPENVIKING_SERVICE_URL.to_string();
                    name = None;
                    api_key = None;
                    root_api_key = None;
                    account = None;
                    user = None;
                    identity_mode = None;
                    key_mode = None;
                    stage = Stage::Name;
                }
                PromptResult::Value(1) => {
                    kind = ConfigKind::Custom;
                    url = DEFAULT_CUSTOM_URL.to_string();
                    name = None;
                    api_key = None;
                    root_api_key = None;
                    account = None;
                    user = None;
                    identity_mode = None;
                    key_mode = None;
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
                        stage = if kind == ConfigKind::OpenVikingService {
                            Stage::ApiKey
                        } else {
                            Stage::Url
                        };
                    }
                    PromptResult::Back => {
                        name = None;
                        api_key = None;
                        root_api_key = None;
                        account = None;
                        user = None;
                        identity_mode = None;
                        key_mode = None;
                        url = DEFAULT_CUSTOM_URL.to_string();
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
                    stage = Stage::KeyMode;
                }
                PromptResult::Back => stage = Stage::Name,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::KeyMode => {
                let allow_empty_api_key = custom_allows_empty_api_key(&url);
                let labels =
                    custom_key_mode_labels_for_language(allow_empty_api_key, Language::current());
                match prompt_select(
                    ui,
                    section_add(),
                    copy(Language::current(), "API key type", "API Key 类型"),
                    &labels,
                    0,
                    &custom_api_key_helper_lines(allow_empty_api_key),
                )? {
                    PromptResult::Value(index) => {
                        let selected = custom_key_mode_for_selection(allow_empty_api_key, index);
                        key_mode = Some(selected);
                        api_key = None;
                        root_api_key = None;
                        account = None;
                        user = None;
                        match selected {
                            CustomKeyMode::NoKey => {
                                identity_mode = Some(IdentityMode::LocalNoKey);
                                stage = Stage::Account;
                            }
                            CustomKeyMode::UserKey | CustomKeyMode::RootKey => {
                                identity_mode = None;
                                stage = Stage::ApiKey;
                            }
                        }
                    }
                    PromptResult::Back => stage = Stage::Url,
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::ApiKey => {
                let helper_lines = if kind == ConfigKind::OpenVikingService {
                    openviking_service_api_key_helper_lines()
                } else {
                    custom_api_key_input_helper_lines(key_mode.unwrap_or(CustomKeyMode::UserKey))
                };

                let label = if kind == ConfigKind::Custom {
                    custom_api_key_input_label(key_mode.unwrap_or(CustomKeyMode::UserKey))
                } else {
                    api_key_label(false)
                };
                match prompt_text(
                    ui,
                    section_add(),
                    label,
                    None,
                    None,
                    false,
                    true,
                    &helper_lines,
                )? {
                    PromptResult::Value(value) => {
                        api_key = empty_to_none(value);
                        root_api_key = if kind == ConfigKind::Custom
                            && key_mode == Some(CustomKeyMode::RootKey)
                        {
                            api_key.clone()
                        } else {
                            None
                        };
                        account = None;
                        user = None;
                        identity_mode = None;
                        stage = Stage::Validate;
                    }
                    PromptResult::Back => {
                        stage = if kind == ConfigKind::OpenVikingService {
                            Stage::Name
                        } else {
                            Stage::KeyMode
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
                    IdentityField::Account,
                    mode,
                )? {
                    PromptResult::Value(value) => {
                        account = Some(value);
                        stage = Stage::User;
                    }
                    PromptResult::Back => {
                        account = None;
                        user = None;
                        stage = match identity_mode {
                            Some(IdentityMode::LocalNoKey) => Stage::KeyMode,
                            Some(IdentityMode::RootKey) | None => Stage::ApiKey,
                        };
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
                    IdentityField::User,
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
                    root_api_key: root_api_key.clone(),
                    account: account.clone(),
                    user: user.clone(),
                };
                match validate_draft(ui, section_add(), &draft).await {
                    Ok(ValidatedConfig {
                        config,
                        api_key_role,
                        root_api_key_role,
                    }) => {
                        if should_confirm_detected_root_key(kind, key_mode, api_key_role) {
                            match prompt_select(
                                ui,
                                section_add(),
                                copy(
                                    Language::current(),
                                    "This key has root access. Configure as root key?",
                                    "此 Key 拥有 Root 权限。配置为 Root Key？",
                                ),
                                &root_key_redirect_labels(),
                                0,
                                &root_key_redirect_helper_lines(),
                            )? {
                                PromptResult::Value(0) => {
                                    key_mode = Some(CustomKeyMode::RootKey);
                                    root_api_key = api_key.clone();
                                    identity_mode = Some(IdentityMode::RootKey);
                                    account = None;
                                    user = None;
                                    stage = Stage::Account;
                                    continue;
                                }
                                PromptResult::Value(1) | PromptResult::Back => {
                                    key_mode = Some(CustomKeyMode::UserKey);
                                    root_api_key = None;
                                    identity_mode = None;
                                    stage = Stage::ApiKey;
                                    continue;
                                }
                                PromptResult::Value(_) | PromptResult::Quit => {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                }
                            }
                        }
                        let root_key_candidate_role = root_api_key_role.or(api_key_role);
                        if should_confirm_detected_user_key(kind, key_mode, root_key_candidate_role)
                        {
                            match prompt_select(
                                ui,
                                section_add(),
                                copy(
                                    Language::current(),
                                    "This key is a user API key. Configure as user key?",
                                    "此 Key 是用户 API Key。配置为用户 Key？",
                                ),
                                &user_key_redirect_labels(),
                                0,
                                &user_key_redirect_helper_lines(),
                            )? {
                                PromptResult::Value(0) => {
                                    key_mode = Some(CustomKeyMode::UserKey);
                                    root_api_key = None;
                                    identity_mode = None;
                                    account = None;
                                    user = None;
                                    stage = Stage::Validate;
                                    continue;
                                }
                                PromptResult::Value(1) | PromptResult::Back => {
                                    key_mode = Some(CustomKeyMode::RootKey);
                                    api_key = None;
                                    root_api_key = None;
                                    identity_mode = None;
                                    account = None;
                                    user = None;
                                    stage = Stage::ApiKey;
                                    continue;
                                }
                                PromptResult::Value(_) | PromptResult::Quit => {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                }
                            }
                        }
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
                                } else if kind == ConfigKind::Custom {
                                    Stage::KeyMode
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
                        let choices: Vec<&str> = if kind == ConfigKind::OpenVikingService {
                            openviking_service_validation_failure_choices_for_language(
                                Language::current(),
                            )
                            .to_vec()
                        } else {
                            custom_validation_failure_choices_for_language(Language::current())
                                .to_vec()
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
                                stage = if kind == ConfigKind::OpenVikingService {
                                    Stage::ApiKey
                                } else {
                                    Stage::Url
                                };
                            }
                            PromptResult::Value(1) => {
                                stage = if kind == ConfigKind::OpenVikingService {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                } else {
                                    Stage::KeyMode
                                };
                            }
                            PromptResult::Value(2) if kind == ConfigKind::Custom => {
                                print_cancelled(ui)?;
                                return Ok(true);
                            }
                            PromptResult::Back => {
                                stage = if kind == ConfigKind::Custom {
                                    Stage::KeyMode
                                } else {
                                    Stage::ApiKey
                                };
                            }
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
        KeyMode,
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
    let mut kind = ConfigKind::Custom;
    let mut url = String::new();
    let mut api_key: Option<String> = None;
    let mut root_api_key: Option<String> = None;
    let mut account: Option<String> = None;
    let mut user: Option<String> = None;
    let mut identity_mode: Option<IdentityMode> = None;
    let mut key_mode: Option<CustomKeyMode> = None;
    let mut key_edit_action: Option<CustomEditKeyAction> = None;
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
                    root_api_key = config.config.root_api_key.clone();
                    if api_key.is_none() && root_api_key.is_some() {
                        api_key = root_api_key.clone();
                    }
                    account = config.config.account.clone();
                    user = config.config.user.clone();
                    identity_mode = None;
                    key_mode = if root_api_key.is_some() {
                        Some(CustomKeyMode::RootKey)
                    } else if api_key.is_some() {
                        Some(CustomKeyMode::UserKey)
                    } else {
                        None
                    };
                    key_edit_action = None;
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
                        stage = if kind == ConfigKind::OpenVikingService {
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
                if kind == ConfigKind::Custom {
                    let has_root_key = has_non_empty(root_api_key.as_deref());
                    let has_normal_user_key =
                        has_normal_user_key(api_key.as_deref(), root_api_key.as_deref());
                    let has_existing = has_root_key || has_normal_user_key;
                    if !has_existing {
                        stage = Stage::KeyMode;
                        continue;
                    }

                    let actions = edit_custom_key_actions(has_normal_user_key, has_root_key);
                    let labels: Vec<&str> = actions
                        .iter()
                        .copied()
                        .map(custom_edit_key_action_label)
                        .collect();
                    match prompt_select(
                        ui,
                        section_edit(),
                        copy(Language::current(), "API keys", "API Key"),
                        &labels,
                        0,
                        &custom_api_key_helper_lines(custom_allows_empty_api_key(&url)),
                    )? {
                        PromptResult::Value(index) => {
                            let action = actions[index];
                            key_edit_action = Some(action);
                            api_key_was_entered = false;
                            match action {
                                CustomEditKeyAction::Keep => {
                                    stage = Stage::Validate;
                                }
                                CustomEditKeyAction::SetUserKey => {
                                    key_mode = Some(CustomKeyMode::UserKey);
                                    identity_mode = None;
                                    stage = Stage::ApiKeyInput;
                                }
                                CustomEditKeyAction::SetRootKey => {
                                    key_mode = Some(CustomKeyMode::RootKey);
                                    identity_mode = None;
                                    stage = Stage::ApiKeyInput;
                                }
                                CustomEditKeyAction::UseRootForNormal => {
                                    api_key = root_api_key.clone();
                                    key_mode = Some(CustomKeyMode::RootKey);
                                    identity_mode = None;
                                    stage = Stage::Validate;
                                }
                                CustomEditKeyAction::ClearRootKey => {
                                    if api_key.as_deref() == root_api_key.as_deref() {
                                        api_key = None;
                                    }
                                    root_api_key = None;
                                    key_mode = if api_key.is_some() {
                                        Some(CustomKeyMode::UserKey)
                                    } else {
                                        None
                                    };
                                    identity_mode = None;
                                    stage = Stage::Validate;
                                }
                                CustomEditKeyAction::ClearAllKeys => {
                                    api_key = None;
                                    root_api_key = None;
                                    key_mode = None;
                                    if custom_allows_empty_api_key(&url) {
                                        identity_mode = Some(IdentityMode::LocalNoKey);
                                        stage = Stage::Account;
                                    } else {
                                        identity_mode = None;
                                        stage = Stage::Validate;
                                    }
                                }
                            }
                        }
                        PromptResult::Back => stage = Stage::Url,
                        PromptResult::Quit => {
                            print_cancelled(ui)?;
                            return Ok(true);
                        }
                    }
                    continue;
                }

                let helper_lines = if kind == ConfigKind::OpenVikingService {
                    openviking_service_api_key_helper_lines()
                } else {
                    custom_api_key_helper_lines(custom_allows_empty_api_key(&url))
                };

                let has_existing = api_key.as_deref().is_some_and(|value| !value.is_empty())
                    || root_api_key
                        .as_deref()
                        .is_some_and(|value| !value.is_empty());
                if !has_existing {
                    stage = if kind == ConfigKind::Custom {
                        Stage::KeyMode
                    } else {
                        Stage::ApiKeyInput
                    };
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
                    PromptResult::Value(1) => {
                        stage = if kind == ConfigKind::Custom {
                            Stage::KeyMode
                        } else {
                            Stage::ApiKeyInput
                        };
                    }
                    PromptResult::Value(_) => {
                        api_key = None;
                        root_api_key = None;
                        key_mode = None;
                        key_edit_action = None;
                        api_key_was_entered = false;
                        if kind == ConfigKind::Custom && custom_allows_empty_api_key(&url) {
                            identity_mode = Some(IdentityMode::LocalNoKey);
                            stage = Stage::Account;
                        } else {
                            identity_mode = None;
                            stage = Stage::Validate;
                        }
                    }
                    PromptResult::Back => {
                        stage = if kind == ConfigKind::OpenVikingService {
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
            Stage::KeyMode => {
                let allow_empty_api_key = custom_allows_empty_api_key(&url);
                let labels =
                    custom_key_mode_labels_for_language(allow_empty_api_key, Language::current());
                match prompt_select(
                    ui,
                    section_edit(),
                    copy(Language::current(), "API key type", "API Key 类型"),
                    &labels,
                    0,
                    &custom_api_key_helper_lines(allow_empty_api_key),
                )? {
                    PromptResult::Value(index) => {
                        let selected = custom_key_mode_for_selection(allow_empty_api_key, index);
                        key_mode = Some(selected);
                        api_key = None;
                        root_api_key = None;
                        key_edit_action = None;
                        api_key_was_entered = false;
                        match selected {
                            CustomKeyMode::NoKey => {
                                identity_mode = Some(IdentityMode::LocalNoKey);
                                stage = Stage::Account;
                            }
                            CustomKeyMode::UserKey | CustomKeyMode::RootKey => {
                                identity_mode = None;
                                stage = Stage::ApiKeyInput;
                            }
                        }
                    }
                    PromptResult::Back => stage = Stage::ApiKeyChoice,
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
            Stage::ApiKeyInput => {
                let has_existing = api_key.as_deref().is_some_and(|value| !value.is_empty())
                    || root_api_key
                        .as_deref()
                        .is_some_and(|value| !value.is_empty());
                let label = if kind == ConfigKind::Custom {
                    custom_api_key_input_label(key_mode.unwrap_or(CustomKeyMode::UserKey))
                } else {
                    api_key_label(false)
                };
                let helper_lines = if kind == ConfigKind::OpenVikingService {
                    openviking_service_api_key_helper_lines()
                } else {
                    custom_api_key_input_helper_lines(key_mode.unwrap_or(CustomKeyMode::UserKey))
                };
                match prompt_text(
                    ui,
                    section_edit(),
                    label,
                    api_key.as_deref(),
                    api_key.as_deref().map(|_| InputValueLabel::Current),
                    false,
                    true,
                    &helper_lines,
                )? {
                    PromptResult::Value(value) => {
                        let entered_key = empty_to_none(value);
                        match key_edit_action {
                            Some(CustomEditKeyAction::SetUserKey) => {
                                api_key = entered_key;
                                api_key_was_entered = api_key.is_some();
                            }
                            Some(CustomEditKeyAction::SetRootKey) => {
                                let keep_normal_user_key = has_normal_user_key(
                                    api_key.as_deref(),
                                    root_api_key.as_deref(),
                                );
                                root_api_key = entered_key.clone();
                                if !keep_normal_user_key {
                                    api_key = entered_key;
                                }
                                api_key_was_entered = root_api_key.is_some();
                            }
                            _ => {
                                api_key = entered_key;
                                root_api_key = if kind == ConfigKind::Custom
                                    && key_mode == Some(CustomKeyMode::RootKey)
                                {
                                    api_key.clone()
                                } else {
                                    None
                                };
                                api_key_was_entered = api_key.is_some();
                            }
                        }
                        identity_mode = None;
                        stage = Stage::Validate;
                    }
                    PromptResult::Back => {
                        stage = if has_existing {
                            Stage::ApiKeyChoice
                        } else if kind == ConfigKind::OpenVikingService {
                            Stage::Name
                        } else {
                            Stage::KeyMode
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
                    IdentityField::Account,
                    mode,
                )? {
                    PromptResult::Value(value) => {
                        account = Some(value);
                        stage = Stage::User;
                    }
                    PromptResult::Back => {
                        account = None;
                        user = None;
                        stage = match identity_mode {
                            Some(IdentityMode::LocalNoKey) => Stage::KeyMode,
                            Some(IdentityMode::RootKey) | None => Stage::ApiKeyInput,
                        };
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
                    IdentityField::User,
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
                    root_api_key: root_api_key.clone(),
                    account: account.clone(),
                    user: user.clone(),
                };
                match validate_draft(ui, section_edit(), &draft).await {
                    Ok(ValidatedConfig {
                        config,
                        api_key_role,
                        root_api_key_role,
                    }) => {
                        if should_confirm_detected_root_key(kind, key_mode, api_key_role) {
                            match prompt_select(
                                ui,
                                section_edit(),
                                copy(
                                    Language::current(),
                                    "This key has root access. Configure as root key?",
                                    "此 Key 拥有 Root 权限。配置为 Root Key？",
                                ),
                                &root_key_redirect_labels(),
                                0,
                                &root_key_redirect_helper_lines(),
                            )? {
                                PromptResult::Value(0) => {
                                    key_mode = Some(CustomKeyMode::RootKey);
                                    key_edit_action = Some(CustomEditKeyAction::SetRootKey);
                                    root_api_key = api_key.clone();
                                    identity_mode = Some(IdentityMode::RootKey);
                                    account = None;
                                    user = None;
                                    stage = Stage::Account;
                                    continue;
                                }
                                PromptResult::Value(1) | PromptResult::Back => {
                                    key_mode = Some(CustomKeyMode::UserKey);
                                    api_key = None;
                                    identity_mode = None;
                                    stage = Stage::ApiKeyInput;
                                    continue;
                                }
                                PromptResult::Value(_) | PromptResult::Quit => {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                }
                            }
                        }
                        let root_key_candidate_role = root_api_key_role.or(api_key_role);
                        if should_confirm_detected_user_key(kind, key_mode, root_key_candidate_role)
                        {
                            match prompt_select(
                                ui,
                                section_edit(),
                                copy(
                                    Language::current(),
                                    "This key is a user API key. Configure as user key?",
                                    "此 Key 是用户 API Key。配置为用户 Key？",
                                ),
                                &user_key_redirect_labels(),
                                0,
                                &user_key_redirect_helper_lines(),
                            )? {
                                PromptResult::Value(0) => {
                                    let entered_user_key = root_api_key.clone().or(api_key.clone());
                                    api_key = entered_user_key;
                                    root_api_key = configs[selected]
                                        .config
                                        .root_api_key
                                        .clone()
                                        .filter(|existing_root_key| {
                                            api_key.as_deref() != Some(existing_root_key.as_str())
                                                && !existing_root_key.trim().is_empty()
                                        });
                                    key_mode = Some(CustomKeyMode::UserKey);
                                    key_edit_action = Some(CustomEditKeyAction::SetUserKey);
                                    api_key_was_entered = api_key.is_some();
                                    identity_mode = None;
                                    account = None;
                                    user = None;
                                    stage = Stage::Validate;
                                    continue;
                                }
                                PromptResult::Value(1) | PromptResult::Back => {
                                    key_mode = Some(CustomKeyMode::RootKey);
                                    key_edit_action = Some(CustomEditKeyAction::SetRootKey);
                                    if api_key.as_deref() == root_api_key.as_deref() {
                                        api_key = None;
                                    }
                                    root_api_key = None;
                                    api_key_was_entered = false;
                                    identity_mode = None;
                                    account = None;
                                    user = None;
                                    stage = Stage::ApiKeyInput;
                                    continue;
                                }
                                PromptResult::Value(_) | PromptResult::Quit => {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                }
                            }
                        }
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
                                print_saved(store, &name, SaveOutcome::UpdatedActive, &config)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveOnly) => {
                                ui.clear()?;
                                let original = configs[selected].name.clone();
                                store.save_edited_config(&original, &name, &config)?;
                                print_saved(store, &name, SaveOutcome::SavedOnly, &config)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveAndActivate) => {
                                ui.clear()?;
                                let original = configs[selected].name.clone();
                                store.save_edited_config(&original, &name, &config)?;
                                store.activate_config(&name)?;
                                print_saved(store, &name, SaveOutcome::Activated, &config)?;
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
                        let choices = if kind == ConfigKind::OpenVikingService {
                            openviking_service_validation_failure_choices_for_language(
                                Language::current(),
                            )
                            .to_vec()
                        } else {
                            custom_validation_failure_choices_for_language(Language::current())
                                .to_vec()
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
                                stage = if kind == ConfigKind::OpenVikingService {
                                    Stage::ApiKeyInput
                                } else {
                                    Stage::Url
                                };
                            }
                            PromptResult::Value(1) => {
                                stage = if kind == ConfigKind::OpenVikingService {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                } else {
                                    Stage::ApiKeyChoice
                                };
                            }
                            PromptResult::Value(2) if kind == ConfigKind::Custom => {
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

async fn run_switch_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Select,
        Confirm,
    }

    let configs = store.list_configs()?;
    if configs.is_empty() {
        let helper_lines = vec![
            theme::warning(copy(
                Language::current(),
                "No saved configs to switch.",
                "没有可切换的配置。",
            ))
            .to_string(),
        ];
        let _ = prompt_select(
            ui,
            section_switch(),
            copy(Language::current(), "Nothing to switch.", "没有可切换项。"),
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
                section_switch(),
                copy(Language::current(), "Config to switch to", "要切换到的配置"),
                &configs,
            )? {
                PromptResult::Value(index) => {
                    selected = index;
                    if configs[index].is_active {
                        let helper_lines = vec![
                            theme::muted(config_already_active_message(&configs[index].name))
                                .to_string(),
                        ];
                        let _ = prompt_select(
                            ui,
                            section_switch(),
                            copy(
                                Language::current(),
                                "Config already active.",
                                "配置已是当前配置。",
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
                let selected_config = &configs[selected];
                match confirm(
                    ui,
                    section_switch(),
                    &switch_confirm_prompt(&selected_config.name),
                    true,
                )? {
                    PromptResult::Value(true) => {
                        ui.render(&status_live_lines(
                            section_switch(),
                            copy(
                                Language::current(),
                                "Validating target config...",
                                "正在验证目标配置...",
                            ),
                        ))?;
                        if let Err(error) = validate_config(&selected_config.config).await {
                            ui.clear()?;
                            print_switch_validation_error(
                                &selected_config.name,
                                selected_config.kind,
                                &error,
                            );
                            return Ok(true);
                        }

                        ui.clear()?;
                        store.activate_config(&selected_config.name)?;
                        println!();
                        println!(
                            "{} {}",
                            theme::success("✓"),
                            theme::success(switched_config_message(&selected_config.name))
                        );
                        println!(
                            "{} {}",
                            theme::muted(copy(Language::current(), "Next:", "下一步：")),
                            theme::body(copy(
                                Language::current(),
                                "Run ov status to inspect it.",
                                "运行 ov status 查看状态。",
                            ))
                        );
                        return Ok(true);
                    }
                    PromptResult::Value(false) => stage = Stage::Select,
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
    root_api_key_role: Option<ApiKeyRole>,
}

async fn validate_draft(
    ui: &mut LiveRegion,
    section: &str,
    draft: &ConfigDraft,
) -> Result<ValidatedConfig> {
    let mut config = build_config(draft)?;
    let require_api_key = draft.kind == ConfigKind::OpenVikingService
        || (draft.kind == ConfigKind::Custom && !custom_allows_empty_api_key(&draft.url));
    ui.render(&status_live_lines(
        section,
        copy(
            Language::current(),
            "Validating connection...",
            "正在验证连接...",
        ),
    ))?;
    let mut root_api_key_role = None;
    let api_key_role = if config
        .api_key
        .as_deref()
        .is_some_and(|key| !key.trim().is_empty())
    {
        let configured_root_api_key = config
            .root_api_key
            .as_deref()
            .filter(|key| !key.trim().is_empty())
            .map(str::to_string);
        let mut detection_config = config.clone();
        detection_config.account = None;
        detection_config.user = None;
        let role = validate_candidate_config_with_role(&detection_config, require_api_key).await?;
        if config.root_api_key.as_deref() == config.api_key.as_deref() {
            root_api_key_role = role;
        }
        if role == Some(ApiKeyRole::Root) {
            if configured_root_api_key.is_none() {
                config.root_api_key = config.api_key.clone();
                root_api_key_role = role;
            }
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
            if let Some(root_api_key) = configured_root_api_key.as_deref() {
                if config.api_key.as_deref() == Some(root_api_key) {
                    config.root_api_key = None;
                    root_api_key_role = role;
                } else {
                    root_api_key_role = Some(
                        validate_root_api_key_role(&config, root_api_key, require_api_key).await?,
                    );
                    if root_api_key_role != Some(ApiKeyRole::Root) {
                        config.root_api_key = None;
                    }
                }
            }
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
        root_api_key_role,
    })
}

async fn validate_root_api_key_role(
    config: &Config,
    root_api_key: &str,
    require_api_key: bool,
) -> Result<ApiKeyRole> {
    let mut root_config = config.clone();
    root_config.api_key = Some(root_api_key.to_string());
    root_config.root_api_key = Some(root_api_key.to_string());
    root_config.account = None;
    root_config.user = None;

    match validate_candidate_config_with_role(&root_config, require_api_key).await? {
        Some(role) => Ok(role),
        None => Err(Error::Config(
            "Expected a root API key, but the server rejected root access.".to_string(),
        )),
    }
}

fn save_config(store: &ConfigStore, name: &str, config: &Config, activate: bool) -> Result<()> {
    if activate {
        store.save_and_activate(name, config)?;
        print_saved(store, name, SaveOutcome::Activated, config)
    } else {
        store.save_named_config(name, config)?;
        print_saved(store, name, SaveOutcome::SavedOnly, config)
    }
}

fn print_saved(
    store: &ConfigStore,
    name: &str,
    outcome: SaveOutcome,
    config: &Config,
) -> Result<()> {
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
    for line in root_key_normal_command_notice_lines(config) {
        println!("{line}");
    }
    println!(
        "{} {}",
        theme::muted(copy(Language::current(), "Next:", "下一步：")),
        next_step_copy()
    );
    Ok(())
}

fn root_key_normal_command_notice_lines(config: &Config) -> Vec<String> {
    if !root_key_used_for_normal_commands(config) {
        return Vec::new();
    }

    vec![
        root_key_configured_notice(),
        normal_commands_root_key_notice(),
        least_privilege_user_key_notice(),
    ]
}

fn root_key_configured_notice() -> String {
    match Language::current() {
        Language::En => format!(
            "{}{}",
            theme::warning("Root key").bold(),
            theme::body(" configured.")
        ),
        Language::ZhCn => format!(
            "{}{}",
            theme::warning("Root Key").bold(),
            theme::body(" 已配置。")
        ),
    }
}

fn normal_commands_root_key_notice() -> String {
    match Language::current() {
        Language::En => format!(
            "{}{}{}{}",
            theme::strong("Normal commands"),
            theme::body(" will use this key until you set a "),
            theme::warning("separate user API key").bold(),
            theme::body("."),
        ),
        Language::ZhCn => format!(
            "{}{}{}{}{}",
            theme::body("在设置"),
            theme::warning("单独的用户 API Key").bold(),
            theme::body("前，"),
            theme::strong("常规命令"),
            theme::body("会使用此 Key。"),
        ),
    }
}

fn least_privilege_user_key_notice() -> String {
    match Language::current() {
        Language::En => format!(
            "{}{}{}{}{}{}{}{}{}",
            theme::body("For "),
            theme::warning("least privilege").bold(),
            theme::body(", run "),
            theme::command("ov config").bold(),
            theme::body(" -> "),
            theme::strong("Edit Config"),
            theme::body(" -> "),
            theme::strong("Set normal user API key"),
            theme::body("."),
        ),
        Language::ZhCn => format!(
            "{}{}{}{}{}{}{}",
            theme::body("为遵循最小权限原则，请运行 "),
            theme::command("ov config").bold(),
            theme::body(" -> "),
            theme::strong("Edit Config"),
            theme::body(" -> "),
            theme::strong("Set normal user API key"),
            theme::body("。"),
        ),
    }
}

fn root_key_used_for_normal_commands(config: &Config) -> bool {
    if ConfigKind::from_config(config) != ConfigKind::Custom {
        return false;
    }

    let Some(api_key) = config.api_key.as_deref().map(str::trim) else {
        return false;
    };
    let Some(root_api_key) = config.root_api_key.as_deref().map(str::trim) else {
        return false;
    };

    !api_key.is_empty() && api_key == root_api_key
}

fn next_step_copy() -> String {
    match Language::current() {
        Language::En => format!(
            "{}{}{}",
            theme::body("Run "),
            theme::command("ov --help").bold(),
            theme::body(" to get started.")
        ),
        Language::ZhCn => format!(
            "{}{}{}",
            theme::body("运行 "),
            theme::command("ov --help").bold(),
            theme::body(" 查看可用命令。")
        ),
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

pub(crate) fn openviking_service_api_key_helper_lines() -> Vec<String> {
    let language = Language::current();
    vec![
        format!(
            "{} {}",
            theme::muted(copy(language, "Get your API key:", "获取 API Key：")),
            OPENVIKING_SERVICE_API_KEY_URL
        ),
        theme::muted(copy(
            language,
            "Go to User Management → API Key to view and copy your key.",
            "进入用户管理 → API Key 查看并复制。",
        ))
        .to_string(),
    ]
}

pub(crate) fn custom_api_key_helper_lines(allow_empty: bool) -> Vec<String> {
    let copy = if allow_empty {
        copy(
            Language::current(),
            "Optional for local servers. Add one if auth is enabled.",
            "本地服务可不填；如果启用了认证，请填写。",
        )
    } else {
        copy(
            Language::current(),
            "Required for remote custom servers.",
            "远程自定义服务需要 API Key。",
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
        ConfigKind::OpenVikingService => "ov-service",
        ConfigKind::Custom => "custom",
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
    field: IdentityField,
    mode: IdentityMode,
) -> Result<PromptResult<String>> {
    let (default, value_label, helper_lines) = identity_prompt_parts(mode);
    let mut error: Option<String> = None;
    loop {
        let mut lines = helper_lines.clone();
        if let Some(value) = error.as_ref() {
            lines.push(theme::error(value).to_string());
        }
        match prompt_text(
            ui,
            section,
            prompt,
            default,
            value_label,
            false,
            false,
            &lines,
        )? {
            PromptResult::Value(value) => match validate_identity_value(&value, field) {
                Ok(()) => return Ok(PromptResult::Value(value)),
                Err(next_error) => error = Some(next_error.to_string()),
            },
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
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
    let label = format!("{} - {}", entry.name, compact_kind_label(entry.kind));
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

fn switch_confirm_prompt(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Switch active config to '{name}'?"),
        Language::ZhCn => format!("切换当前配置为 '{name}'？"),
    }
}

fn localized_validation_error(kind: ConfigKind, error: &Error) -> String {
    match Language::current() {
        Language::En => validation_error_copy(kind, error),
        Language::ZhCn => match kind {
            ConfigKind::OpenVikingService => "验证失败。请检查 API Key 后重试。".to_string(),
            ConfigKind::Custom => "验证失败。请检查服务器 URL，以及是否需要 API Key。".to_string(),
        },
    }
}

fn deleted_config_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Deleted config '{name}'."),
        Language::ZhCn => format!("已删除配置 '{name}'。"),
    }
}

fn switched_config_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Switched active config to '{name}'."),
        Language::ZhCn => format!("已切换当前配置为 '{name}'。"),
    }
}

fn config_already_active_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Config '{name}' is already active."),
        Language::ZhCn => format!("配置 '{name}' 已是当前配置。"),
    }
}

fn switch_validation_error_lines(name: &str, kind: ConfigKind, error: &Error) -> Vec<String> {
    vec![
        String::new(),
        format!(
            "{} {}",
            theme::error("✗"),
            theme::error(match Language::current() {
                Language::En => format!("Target config '{name}' failed validation."),
                Language::ZhCn => format!("目标配置 '{name}' 验证失败。"),
            })
        ),
        format!(
            "  {}",
            theme::muted(localized_validation_error(kind, error))
        ),
        format!(
            "{} {}",
            theme::muted(copy(Language::current(), "Next:", "下一步：")),
            theme::body(copy(
                Language::current(),
                "Run ov config and edit this config before switching.",
                "请运行 ov config 编辑该配置后再切换。",
            ))
        ),
    ]
}

fn print_switch_validation_error(name: &str, kind: ConfigKind, error: &Error) {
    for line in switch_validation_error_lines(name, kind, error) {
        println!("{line}");
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

        match event::read()? {
            Event::Key(key) => match key.code {
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
            },
            Event::Resize(_, _) => {
                // Redraw on the next loop using the new terminal width.
            }
            _ => {}
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
        render_text_prompt(
            ui,
            TextPromptView {
                section,
                prompt,
                default,
                value_label,
                secret,
                helper_lines,
                error: error.as_deref(),
                value: &value,
            },
        )?;

        let raw = RawPrompt::enter(false)?;
        loop {
            match event::read()? {
                Event::Key(key) => match key.code {
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
                            ui.set_input_prompt(input_prompt_with_value(&value, secret));
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
                            ui.set_input_prompt(input_prompt_with_value(&value, secret));
                            io::stdout().flush()?;
                        }
                    }
                    _ => {}
                },
                Event::Resize(_, _) => {
                    render_text_prompt(
                        ui,
                        TextPromptView {
                            section,
                            prompt,
                            default,
                            value_label,
                            secret,
                            helper_lines,
                            error: error.as_deref(),
                            value: &value,
                        },
                    )?;
                }
                _ => {}
            }
        }
    }
}

struct TextPromptView<'a> {
    section: &'a str,
    prompt: &'a str,
    default: Option<&'a str>,
    value_label: Option<InputValueLabel>,
    secret: bool,
    helper_lines: &'a [String],
    error: Option<&'a str>,
    value: &'a str,
}

fn render_text_prompt(ui: &mut LiveRegion, view: TextPromptView<'_>) -> Result<()> {
    ui.render_input(
        &input_live_lines(
            view.section,
            view.prompt,
            view.default,
            view.value_label,
            view.secret,
            view.helper_lines,
            view.error,
        ),
        TEXT_INPUT_PROMPT,
    )?;
    let input_prompt = input_prompt_with_value(view.value, view.secret);
    if input_prompt.len() > TEXT_INPUT_PROMPT.len() {
        if view.secret {
            raw_write("*".repeat(view.value.chars().count()))?;
        } else {
            raw_write(view.value)?;
        }
    }
    ui.set_input_prompt(input_prompt);
    io::stdout().flush()?;
    Ok(())
}

fn input_prompt_with_value(value: &str, secret: bool) -> String {
    if value.is_empty() {
        return TEXT_INPUT_PROMPT.to_string();
    }
    let rendered_value = if secret {
        "*".repeat(value.chars().count())
    } else {
        value.to_string()
    };
    format!("{TEXT_INPUT_PROMPT}{rendered_value}")
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
    lines: Vec<String>,
    input_prompt: Option<String>,
    rows_drawn: usize,
    cursor_on_last_line: bool,
}

impl LiveRegion {
    fn render(&mut self, lines: &[String]) -> Result<()> {
        self.clear()?;
        for line in lines {
            raw_line(line)?;
        }
        io::stdout().flush()?;
        self.lines = lines.to_vec();
        self.input_prompt = None;
        self.rows_drawn = rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            live_region_columns(),
        );
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
        self.lines = lines.to_vec();
        self.input_prompt = Some(prompt.to_string());
        self.rows_drawn = rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            live_region_columns(),
        );
        self.cursor_on_last_line = true;
        Ok(())
    }

    fn clear(&mut self) -> Result<()> {
        clear_live_region(
            self.rows_to_clear(live_region_columns()),
            self.cursor_on_last_line,
        )?;
        self.lines.clear();
        self.input_prompt = None;
        self.rows_drawn = 0;
        self.cursor_on_last_line = false;
        Ok(())
    }

    fn set_input_prompt(&mut self, prompt: String) {
        self.input_prompt = Some(prompt);
        self.rows_drawn = self.rows_drawn.max(rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            live_region_columns(),
        ));
    }

    fn rows_to_clear(&self, columns: usize) -> usize {
        self.rows_drawn.max(rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            columns,
        ))
    }
}

fn live_region_columns() -> usize {
    terminal::size()
        .map(|(columns, _)| usize::from(columns).saturating_sub(1).max(1))
        .unwrap_or_else(|_| status_box_width())
}

fn rendered_live_region_rows(
    lines: &[String],
    input_prompt: Option<&str>,
    columns: usize,
) -> usize {
    rendered_row_count(lines, columns)
        + input_prompt
            .map(|prompt| rendered_line_rows(prompt, columns))
            .unwrap_or(0)
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
        COMPACT_STATUS_DETAIL_WIDTH, CustomKeyMode, FULL_STATUS_BOX_MIN_ROWS, IdentityMode,
        InputValueLabel, LiveRegion, OV_LOGO_LINES, RenderedRegion, Rgb, StatusBoxFrame,
        StatusBoxMode, StatusBoxRuntime, active_delete_block_helper_lines, active_summary_lines,
        active_summary_render_parts, add_config_name_label, add_save_action_labels,
        allocate_config_name, box_content_line, box_footer_line, box_title_line,
        compact_status_box_width, config_select_label, custom_api_key_helper_lines,
        custom_api_key_input_helper_lines, custom_key_mode_labels,
        custom_validation_failure_choices, display_config_home, display_width,
        edit_api_key_choice_labels, edit_custom_key_action_labels, edit_save_action_labels,
        erase_sequence_for_char, extract_models_from_status_payload, identity_prompt_parts,
        input_live_lines, input_prompt_with_value, logo_glass_color_for_theme, main_action_labels,
        next_step_copy, openviking_service_api_key_helper_lines,
        openviking_service_validation_failure_choices, ov_logo_width, provider_labels,
        rendered_live_region_rows, rendered_row_count, root_key_normal_command_notice_lines,
        root_key_redirect_labels, saved_summary_render_parts, select_live_lines,
        should_confirm_detected_user_key, should_prompt_root_identity, status_box_frame_for_size,
        status_box_lines, status_box_lines_with_runtime, status_box_lines_with_runtime_width,
        status_box_width, status_payload_is_healthy, styled_logo_to_width_for_color_level,
        styled_wordmark_line_for_color_level, switch_validation_error_lines,
        tagline_ice_color_for_theme, user_key_redirect_labels, validate_config_name,
        validate_draft, wizard_header_lines, wordmark_gradient_color_for_theme, wordmark_lines,
        wordmark_width,
    };
    use crate::config::Config;
    use crate::config_wizard::store::{
        ApiKeyRole, ConfigDraft, ConfigEntry, ConfigKind, ConfigStore, OPENVIKING_SERVICE_URL,
        validate_account_id_value, validate_user_id_value,
    };
    use crate::error::Error;
    use crate::i18n::Language;
    use crate::theme::{self, ThemeColor};
    use colored::Colorize;
    use serde_json::json;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn unique_dir(name: &str) -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should be valid")
            .as_nanos();
        std::env::temp_dir().join(format!("openviking-wizard-{name}-{suffix}"))
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
    fn wizard_header_contains_standalone_wordmark() {
        let header = wizard_header_lines().join("\n");

        assert!(header.contains("██████╗"));
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
    fn input_prompt_tracks_visible_or_secret_value() {
        assert_eq!(input_prompt_with_value("", false), "  > ");
        assert_eq!(input_prompt_with_value("abc", false), "  > abc");
        assert_eq!(input_prompt_with_value("secret", true), "  > ******");
    }

    #[test]
    fn switch_validation_error_uses_selected_config_kind() {
        let lines = switch_validation_error_lines(
            "cloud",
            ConfigKind::OpenVikingService,
            &Error::Config("invalid".to_string()),
        );
        let plain = strip_ansi(&lines.join("\n"));

        assert!(plain.contains("Target config 'cloud' failed validation."));
        assert!(plain.contains("Check your API key"));
        assert!(!plain.contains("server URL"));
    }

    #[test]
    fn wizard_header_has_no_standalone_spaced_bands() {
        let lines = wizard_header_lines();

        assert_eq!(lines.len(), wordmark_lines().len() + 1);
        assert_eq!(lines.last().map(String::as_str), Some(""));
    }

    #[test]
    fn header_omits_tagline_and_version() {
        let lines = wizard_header_lines();
        let header = lines.join("\n");

        assert!(!header.contains("Context Database for AI Agents"));
        assert!(!header.contains("OpenViking CLI v"));
    }

    #[test]
    fn full_status_box_puts_motto_in_border_without_wordmark() {
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
                kind: ConfigKind::Custom,
            }],
            "~/.openviking",
        );
        let version = format!("v{}", env!("OPENVIKING_CLI_VERSION"));

        assert!(lines.len() >= 6);
        assert!(lines[0].contains("Context Database for AI Agents"));
        assert!(!lines.iter().any(|line| line.contains("██████╗")));
        assert!(lines.iter().any(|line| line.contains('⣿')));
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
    fn status_box_empty_title_uses_continuous_top_border() {
        let width = 48;
        let title = box_title_line("", width);

        assert_eq!(title, format!("╭{}╮", "─".repeat(width - 2)));
        assert_eq!(display_width(&title), width);
    }

    #[test]
    fn compact_status_box_omits_logo_art_and_fits_status_details() {
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let width = compact_status_box_width();
        let lines = status_box_lines_with_runtime_width(
            Some(&config),
            &[ConfigEntry {
                name: "compact".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::Custom,
            }],
            "~/.openviking",
            &StatusBoxRuntime::unknown(),
            StatusBoxFrame {
                width,
                mode: StatusBoxMode::Compact,
            },
        );
        let text = lines.join("\n");

        assert!(width >= COMPACT_STATUS_DETAIL_WIDTH + 4);
        assert!(width <= status_box_width());
        assert!(lines[0].contains("OpenViking | Context Database for AI Agents"));
        assert!(text.contains("Active:"));
        assert!(!text.contains('⣿'));
        assert!(!text.contains('⣶'));
        assert!(!text.contains("██████╗"));
        assert!(!text.contains("╚═════╝"));
        assert!(!text.contains("OPENVIKING"));
        assert!(!text.contains("⠠⣶⣾⣿⣿⣿⣶⣤⣀ ⠾⠟⠛⠉⠉   ⣀⣤⣾⡿⠃"));
        for line in &lines {
            assert_eq!(display_width(line), width, "{line:?}");
        }
    }

    #[test]
    fn status_box_frame_uses_compact_mode_when_full_art_would_not_fit() {
        let compact_width = compact_status_box_width();

        assert_eq!(
            status_box_frame_for_size(compact_width as u16 + 1, FULL_STATUS_BOX_MIN_ROWS - 1),
            StatusBoxFrame {
                width: compact_width,
                mode: StatusBoxMode::Compact,
            }
        );
        assert_eq!(
            status_box_frame_for_size(status_box_width() as u16 - 1, FULL_STATUS_BOX_MIN_ROWS).mode,
            StatusBoxMode::Compact
        );
        assert_eq!(
            status_box_frame_for_size(status_box_width() as u16 + 1, FULL_STATUS_BOX_MIN_ROWS).mode,
            StatusBoxMode::Full
        );
    }

    #[test]
    fn rendered_region_clears_render_time_rows_after_width_changes() {
        let lines = vec!["x".repeat(90)];
        let region = RenderedRegion::from_lines(&lines, 30);

        assert_eq!(rendered_row_count(&lines, 30), 3);
        assert_eq!(rendered_row_count(&lines, 90), 1);
        assert_eq!(region.rows_to_clear(90), 3);
    }

    #[test]
    fn live_region_recomputes_clear_rows_after_resize() {
        let lines = vec!["x".repeat(90)];
        let ui = LiveRegion {
            lines: lines.clone(),
            input_prompt: None,
            rows_drawn: rendered_live_region_rows(&lines, None, 90),
            cursor_on_last_line: false,
        };

        assert_eq!(ui.rows_to_clear(30), 3);
    }

    #[test]
    fn live_region_keeps_largest_input_rows_until_clear() {
        let mut ui = LiveRegion {
            lines: vec!["Prompt".to_string()],
            input_prompt: Some(format!("  > {}", "x".repeat(90))),
            rows_drawn: 10,
            cursor_on_last_line: true,
        };

        ui.set_input_prompt("  > x".to_string());

        assert_eq!(ui.rows_to_clear(120), 10);
    }

    #[test]
    fn status_box_cjk_lines_align_to_display_width() {
        let width = status_box_width();
        let title = box_title_line("AI Agent 上下文数据库", width);
        let content = box_content_line(
            "",
            "当前配置： VPS_ROOT (自定义)",
            width,
            StatusBoxMode::Full,
        );
        let footer = box_footer_line("v0.0.0", width);

        for line in [title, content, footer] {
            assert_eq!(display_width(&line), width, "{line:?}");
        }
    }

    #[test]
    fn status_box_content_line_respects_narrow_terminal_width() {
        let width = 40;
        let content = box_content_line(
            OV_LOGO_LINES[8],
            "Context Database for AI Agents",
            width,
            StatusBoxMode::Full,
        );

        assert_eq!(display_width(&content), width, "{content:?}");
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
                kind: ConfigKind::Custom,
            }],
            "~/.openviking",
        );
        let text = lines.join("\n");

        assert!(text.contains("Active:"));
        assert!(text.contains("orange (Custom)"));
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
                kind: ConfigKind::Custom,
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
                kind: ConfigKind::Custom,
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
        assert!(lines[active_index].contains("Active: orange (Custom)"));
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
    fn wordmark_preserves_original_standalone_block_art() {
        let width = wordmark_width();

        assert_eq!(
            width, 81,
            "wordmark should preserve the original standalone width"
        );
        assert!(
            wordmark_lines().iter().any(|line| line.contains("████")),
            "wordmark should preserve block-art styling"
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
        let wordmark = wordmark_lines();

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
    fn wordmark_uses_stable_teal_ansi256_fallback_when_truecolor_is_unavailable() {
        let rendered =
            styled_wordmark_line_for_color_level(wordmark_lines()[0], theme::ColorLevel::Ansi256);

        assert!(rendered.contains("\u{1b}[1;38;5;"));
        assert!(!rendered.contains("38;2;"));
        assert_eq!(ansi256_indexes(&rendered), vec![30]);
    }

    #[test]
    fn logo_uses_stable_teal_ansi256_fallback_when_truecolor_is_unavailable() {
        let rendered = styled_logo_to_width_for_color_level(
            OV_LOGO_LINES[8],
            ov_logo_width(),
            8,
            theme::ColorLevel::Ansi256,
        );

        assert!(rendered.contains("\u{1b}[1;38;5;"));
        assert!(!rendered.contains("38;2;"));
        assert_eq!(ansi256_indexes(&rendered), vec![30]);
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

    fn ansi256_indexes(rendered: &str) -> Vec<u8> {
        let mut indexes = Vec::new();
        let mut rest = rendered;
        while let Some(start) = rest.find("38;5;") {
            let value_start = start + "38;5;".len();
            let digits: String = rest[value_start..]
                .chars()
                .take_while(|ch| ch.is_ascii_digit())
                .collect();
            if let Ok(index) = digits.parse::<u8>()
                && !indexes.contains(&index)
            {
                indexes.push(index);
            }
            rest = &rest[value_start..];
        }
        indexes
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
        let config = Config {
            url: "http://127.0.0.1:1933".to_string(),
            ..Config::default()
        };
        let lines = active_summary_lines(
            Some(&config),
            &[ConfigEntry {
                name: "local".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::Custom,
            }],
        );

        assert_eq!(lines[0], "Active: local (Custom)");
        assert_eq!(lines[1], "Saved configs: 1");
        assert!(!lines[0].contains("127.0.0.1"));
        assert!(!lines[0].starts_with(' '));
        assert!(!lines[1].starts_with(' '));
    }

    #[test]
    fn active_summary_uses_compact_openviking_service_kind() {
        let config = Config {
            url: OPENVIKING_SERVICE_URL.to_string(),
            ..Config::default()
        };
        let lines = active_summary_lines(
            Some(&config),
            &[ConfigEntry {
                name: "serverless".to_string(),
                config: config.clone(),
                is_active: true,
                kind: ConfigKind::OpenVikingService,
            }],
        );

        assert_eq!(lines[0], "Active: serverless (OpenViking Service)");
        assert!(!lines[0].contains("VolcEngine Cloud"));

        let active = active_summary_render_parts(&lines[0])
            .expect("compact OpenViking Service summary should split");
        assert_eq!(active.name, "serverless");
        assert_eq!(active.kind.as_deref(), Some("(OpenViking Service)"));
    }

    #[test]
    fn summary_render_parts_split_config_name_type_and_count() {
        let active = active_summary_render_parts("Active: test (Custom)")
            .expect("active summary should split");
        let saved =
            saved_summary_render_parts("Saved configs: 2").expect("saved summary should split");

        assert_eq!(active.label, "Active:");
        assert_eq!(active.name, "test");
        assert_eq!(active.kind.as_deref(), Some("(Custom)"));
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

        let name = allocate_config_name(&store, ConfigKind::Custom)
            .expect("generated name should be available");

        assert!(name.starts_with("custom-"));
        assert_eq!(name.len(), "custom-".len() + 6);
        validate_config_name(&name).expect("generated name should be valid");
    }

    #[test]
    fn provider_helper_copy_is_minimal_and_custom_is_clear() {
        let cloud = openviking_service_api_key_helper_lines();
        let local_custom = custom_api_key_helper_lines(true);
        let remote_custom = custom_api_key_helper_lines(false);
        let local_custom_plain: Vec<String> =
            local_custom.iter().map(|line| strip_ansi(line)).collect();
        let remote_custom_plain: Vec<String> =
            remote_custom.iter().map(|line| strip_ansi(line)).collect();

        assert_eq!(
            provider_labels(Language::En)[0],
            "OpenViking Service (VolcEngine Cloud)"
        );
        assert!(cloud.iter().any(|line| line.contains("Get your API key:")));
        assert!(cloud.iter().any(|line| {
            line.contains("Go to User Management → API Key to view and copy your key.")
        }));
        assert!(!cloud.iter().any(|line| line.contains("Server URL")));
        assert!(
            local_custom.iter().any(
                |line| line.contains("Optional for local servers. Add one if auth is enabled.")
            )
        );
        assert!(
            remote_custom
                .iter()
                .any(|line| line.contains("Required for remote custom servers."))
        );
        assert!(
            !remote_custom
                .iter()
                .any(|line| line.contains("Usually not needed locally"))
        );
        assert!(
            !local_custom_plain
                .iter()
                .chain(remote_custom_plain.iter())
                .any(|line| line.contains(';'))
        );
    }

    #[test]
    fn custom_key_mode_choices_distinguish_no_key_user_key_and_root_key() {
        assert_eq!(
            custom_key_mode_labels(true),
            ["No key / local dev", "User API key", "Root API key"]
        );
        assert_eq!(
            custom_key_mode_labels(false),
            ["User API key", "Root API key"]
        );
    }

    #[test]
    fn custom_key_input_copy_explains_storage_target() {
        let user_key = custom_api_key_input_helper_lines(CustomKeyMode::UserKey);
        let root_key = custom_api_key_input_helper_lines(CustomKeyMode::RootKey);

        assert!(user_key.iter().any(|line| line.contains("normal")));
        assert!(!user_key.iter().any(|line| line.contains("api_key")));
        assert!(!root_key.iter().any(|line| line.contains("root_api_key")));
        assert!(!root_key.iter().any(|line| line.contains("api_key")));
        assert!(root_key.iter().any(|line| line.contains("--sudo")));
        assert!(!root_key.iter().any(|line| line.contains("remote")));
    }

    #[test]
    fn add_custom_api_key_rendering_has_no_existing_value_placeholder() {
        let lines = input_live_lines(
            "Create a new OpenViking config.",
            "API key (optional)",
            None,
            None,
            true,
            &custom_api_key_helper_lines(true),
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
            openviking_service_validation_failure_choices(),
            ["Retry API key", "Cancel"]
        );
        assert_eq!(
            custom_validation_failure_choices(),
            ["Edit server URL", "Edit API key", "Cancel"]
        );
        assert!(!openviking_service_validation_failure_choices().contains(&"Edit config name"));
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
            kind: ConfigKind::Custom,
        };
        let inactive = ConfigEntry {
            name: "local".to_string(),
            config,
            is_active: false,
            kind: ConfigKind::Custom,
        };

        let active_label = config_select_label(&active);
        assert!(active_label.contains("VPS - Custom"));
        assert!(active_label.contains("[Active]"));
        assert!(!active_label.contains("* "));

        let inactive_label = config_select_label(&inactive);
        assert_eq!(inactive_label, "local - Custom");
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
    fn root_key_notice_points_to_edit_flow_when_root_key_is_reused() {
        let config = Config {
            api_key: Some("root-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            ..Config::default()
        };

        let text = strip_ansi(&root_key_normal_command_notice_lines(&config).join("\n"));

        assert!(text.contains("Root key configured."));
        assert!(text.contains("Normal commands will use this key"));
        assert!(text.contains("ov config -> Edit Config -> Set normal user API key"));
    }

    #[test]
    fn root_key_notice_styles_sentence_text_with_body_color() {
        colored::control::set_override(true);
        let config = Config {
            api_key: Some("root-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            ..Config::default()
        };
        let expected = vec![
            format!(
                "{}{}",
                theme::warning("Root key").bold(),
                theme::body(" configured.")
            ),
            format!(
                "{}{}{}{}",
                theme::strong("Normal commands"),
                theme::body(" will use this key until you set a "),
                theme::warning("separate user API key").bold(),
                theme::body(".")
            ),
            format!(
                "{}{}{}{}{}{}{}{}{}",
                theme::body("For "),
                theme::warning("least privilege").bold(),
                theme::body(", run "),
                theme::command("ov config").bold(),
                theme::body(" -> "),
                theme::strong("Edit Config"),
                theme::body(" -> "),
                theme::strong("Set normal user API key"),
                theme::body(".")
            ),
        ];
        let lines = root_key_normal_command_notice_lines(&config);
        colored::control::unset_override();

        assert_eq!(lines, expected);
    }

    #[test]
    fn next_step_copy_styles_sentence_text_with_body_color() {
        colored::control::set_override(true);
        let expected = format!(
            "{}{}{}",
            theme::body("Run "),
            theme::command("ov --help").bold(),
            theme::body(" to get started.")
        );
        let rendered = next_step_copy();
        colored::control::unset_override();

        assert_eq!(rendered, expected);
    }

    #[test]
    fn root_key_notice_is_hidden_for_separate_user_key() {
        let config = Config {
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            ..Config::default()
        };

        assert!(root_key_normal_command_notice_lines(&config).is_empty());
    }

    #[test]
    fn edit_api_key_choices_match_kind_and_existing_key_state() {
        assert!(edit_api_key_choice_labels(ConfigKind::Custom, false).is_empty());
        assert!(edit_api_key_choice_labels(ConfigKind::OpenVikingService, false).is_empty());
        assert_eq!(
            edit_api_key_choice_labels(ConfigKind::Custom, true),
            ["Keep existing API key", "Replace API key", "Clear API key"]
        );
        assert_eq!(
            edit_api_key_choice_labels(ConfigKind::OpenVikingService, true),
            ["Keep existing API key", "Replace API key"]
        );
    }

    #[test]
    fn edit_custom_key_actions_preserve_separate_credential_roles() {
        assert_eq!(
            edit_custom_key_action_labels(false, false),
            ["Set normal user API key", "Set root API key"]
        );
        assert_eq!(
            edit_custom_key_action_labels(true, true),
            [
                "Keep existing API keys",
                "Set normal user API key",
                "Set root API key",
                "Use root key for normal commands",
                "Clear root API key",
                "Clear all API keys"
            ]
        );
        assert_eq!(
            edit_custom_key_action_labels(true, false),
            [
                "Keep existing API keys",
                "Set normal user API key",
                "Set root API key",
                "Clear all API keys"
            ]
        );
    }

    #[test]
    fn root_key_redirect_copy_is_outcome_based() {
        assert_eq!(
            root_key_redirect_labels(),
            ["Continue as root key", "Re-enter user key", "Cancel"]
        );
    }

    #[test]
    fn user_key_redirect_copy_is_outcome_based() {
        assert_eq!(
            user_key_redirect_labels(),
            ["Continue as user key", "Re-enter root key", "Cancel"]
        );
    }

    #[test]
    fn root_key_route_detects_regular_user_key() {
        assert!(should_confirm_detected_user_key(
            ConfigKind::Custom,
            Some(CustomKeyMode::RootKey),
            Some(ApiKeyRole::Regular),
        ));
        assert!(!should_confirm_detected_user_key(
            ConfigKind::Custom,
            Some(CustomKeyMode::UserKey),
            Some(ApiKeyRole::Regular),
        ));
        assert!(!should_confirm_detected_user_key(
            ConfigKind::Custom,
            Some(CustomKeyMode::RootKey),
            Some(ApiKeyRole::Root),
        ));
        assert!(!should_confirm_detected_user_key(
            ConfigKind::OpenVikingService,
            Some(CustomKeyMode::RootKey),
            Some(ApiKeyRole::Regular),
        ));
    }

    #[test]
    fn identity_validation_rejects_spaces_before_server_validation() {
        assert!(validate_user_id_value("alice").is_ok());
        assert!(validate_user_id_value("alice@example.com").is_ok());
        let error = validate_user_id_value("alice smith").expect_err("spaces should be rejected");
        assert_eq!(
            error.to_string(),
            "Configuration error: User ID can only contain letters, numbers, '_', '-', '.', and '@'"
        );
        assert!(validate_account_id_value("_system").is_err());
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

    #[tokio::test]
    async fn validated_root_key_config_populates_root_api_key_for_sudo() {
        let url = spawn_root_probe_server("root-key").await;
        let draft = ConfigDraft {
            name: "local".to_string(),
            kind: ConfigKind::Custom,
            url,
            api_key: Some("root-key".to_string()),
            root_api_key: None,
            account: Some("admin".to_string()),
            user: Some("default".to_string()),
        };
        let mut ui = super::LiveRegion::default();

        let validated = validate_draft(&mut ui, "test", &draft)
            .await
            .expect("root key should validate");

        assert_eq!(validated.api_key_role, Some(ApiKeyRole::Root));
        assert_eq!(validated.config.api_key.as_deref(), Some("root-key"));
        assert_eq!(validated.config.root_api_key.as_deref(), Some("root-key"));
    }

    #[tokio::test]
    async fn validated_user_key_config_preserves_separate_root_api_key_for_sudo() {
        let url = spawn_dual_key_probe_server("user-key", "root-key").await;
        let draft = ConfigDraft {
            name: "local".to_string(),
            kind: ConfigKind::Custom,
            url,
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("admin".to_string()),
            user: Some("default".to_string()),
        };
        let mut ui = super::LiveRegion::default();

        let validated = validate_draft(&mut ui, "test", &draft)
            .await
            .expect("separate user and root keys should validate");

        assert_eq!(validated.api_key_role, Some(ApiKeyRole::Regular));
        assert_eq!(validated.config.api_key.as_deref(), Some("user-key"));
        assert_eq!(validated.config.root_api_key.as_deref(), Some("root-key"));
        assert_eq!(validated.config.account, None);
        assert_eq!(validated.config.user, None);
    }

    #[tokio::test]
    async fn selected_root_key_regular_user_key_returns_regular_role_without_root_key() {
        let url = spawn_dual_key_probe_server("user-key", "root-key").await;
        let draft = ConfigDraft {
            name: "local".to_string(),
            kind: ConfigKind::Custom,
            url,
            api_key: Some("user-key".to_string()),
            root_api_key: Some("user-key".to_string()),
            account: None,
            user: None,
        };
        let mut ui = super::LiveRegion::default();

        let validated = validate_draft(&mut ui, "test", &draft)
            .await
            .expect("regular key should validate so the wizard can redirect");

        assert_eq!(validated.api_key_role, Some(ApiKeyRole::Regular));
        assert_eq!(validated.config.api_key.as_deref(), Some("user-key"));
        assert_eq!(validated.config.root_api_key, None);
        assert_eq!(validated.config.account, None);
        assert_eq!(validated.config.user, None);
    }

    #[tokio::test]
    async fn separate_root_key_candidate_regular_user_key_returns_regular_root_candidate_role() {
        let url =
            spawn_two_user_key_probe_server("existing-user-key", "entered-user-key", "root-key")
                .await;
        let draft = ConfigDraft {
            name: "local".to_string(),
            kind: ConfigKind::Custom,
            url,
            api_key: Some("existing-user-key".to_string()),
            root_api_key: Some("entered-user-key".to_string()),
            account: None,
            user: None,
        };
        let mut ui = super::LiveRegion::default();

        let validated = validate_draft(&mut ui, "test", &draft)
            .await
            .expect("regular root-key candidate should validate so the wizard can redirect");

        assert_eq!(validated.api_key_role, Some(ApiKeyRole::Regular));
        assert_eq!(validated.root_api_key_role, Some(ApiKeyRole::Regular));
        assert_eq!(
            validated.config.api_key.as_deref(),
            Some("existing-user-key")
        );
        assert_eq!(validated.config.root_api_key, None);
    }

    async fn spawn_root_probe_server(root_api_key: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..6 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let lower_request = request.to_ascii_lowercase();
                let root_header = format!("x-api-key: {root_api_key}");
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    if lower_request.contains(&root_header) {
                        http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                    } else {
                        http_response(
                            401,
                            r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                        )
                    }
                } else if request.starts_with("GET /api/v1/admin/accounts ") {
                    if lower_request.contains(&root_header) {
                        http_response(200, r#"{"accounts":[]}"#)
                    } else {
                        http_response(
                            403,
                            r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                        )
                    }
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    async fn spawn_dual_key_probe_server(
        user_api_key: &'static str,
        root_api_key: &'static str,
    ) -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..12 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let lower_request = request.to_ascii_lowercase();
                let user_header = format!("x-api-key: {user_api_key}");
                let root_header = format!("x-api-key: {root_api_key}");
                let has_user_key = lower_request.contains(&user_header);
                let has_root_key = lower_request.contains(&root_header);
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    if has_user_key || has_root_key {
                        http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                    } else {
                        http_response(
                            401,
                            r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                        )
                    }
                } else if request.starts_with("GET /api/v1/admin/accounts ") {
                    if has_root_key {
                        http_response(200, r#"{"accounts":[]}"#)
                    } else {
                        http_response(
                            403,
                            r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                        )
                    }
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    async fn spawn_two_user_key_probe_server(
        first_user_api_key: &'static str,
        second_user_api_key: &'static str,
        root_api_key: &'static str,
    ) -> String {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        tokio::spawn(async move {
            for _ in 0..12 {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut buffer = vec![0; 4096];
                let Ok(read) = stream.read(&mut buffer).await else {
                    return;
                };
                let request = String::from_utf8_lossy(&buffer[..read]);
                let lower_request = request.to_ascii_lowercase();
                let first_user_header = format!("x-api-key: {first_user_api_key}");
                let second_user_header = format!("x-api-key: {second_user_api_key}");
                let root_header = format!("x-api-key: {root_api_key}");
                let has_regular_key = lower_request.contains(&first_user_header)
                    || lower_request.contains(&second_user_header);
                let has_root_key = lower_request.contains(&root_header);
                let response = if request.starts_with("GET /health ") {
                    http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
                } else if request.starts_with("GET /api/v1/system/status ") {
                    if has_regular_key || has_root_key {
                        http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                    } else {
                        http_response(
                            401,
                            r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                        )
                    }
                } else if request.starts_with("GET /api/v1/admin/accounts ") {
                    if has_root_key {
                        http_response(200, r#"{"accounts":[]}"#)
                    } else {
                        http_response(
                            403,
                            r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                        )
                    }
                } else {
                    http_response(404, r#"{"error":{"message":"not found"}}"#)
                };
                let _ = stream.write_all(response.as_bytes()).await;
            }
        });
        format!("http://{addr}")
    }

    fn http_response(status: u16, body: &str) -> String {
        let reason = match status {
            200 => "OK",
            401 => "Unauthorized",
            403 => "Forbidden",
            404 => "Not Found",
            _ => "Error",
        };
        format!(
            "HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        )
    }

    #[test]
    fn live_select_lines_are_current_step_only() {
        let lines = select_live_lines(
            "What would you like to configure?",
            "Choose action",
            &[
                "Add Config",
                "Switch Config",
                "Edit Config",
                "Delete Config",
            ],
            1,
            &[],
        );

        assert!(lines[0].contains("What would you like to configure?"));
        assert!(lines.iter().any(|line| line.contains("› Switch Config")));
        assert!(!lines.iter().any(|line| line.contains("✓ Choose action")));
        assert!(!lines.iter().any(|line| line.contains("Back")));
        assert_eq!(lines.len(), 9);
    }

    #[test]
    fn wizard_main_actions_include_switch_config() {
        assert_eq!(
            main_action_labels().as_slice(),
            [
                "Add Config",
                "Switch Config",
                "Edit Config",
                "Delete Config"
            ]
            .as_slice()
        );
    }
}
