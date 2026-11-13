//! Credential choices, identity prompts, role validation, and key guidance.

use super::prompts::{InputValueLabel, LiveRegion, PromptResult, prompt_text, status_live_lines};
use crate::config::Config;
use crate::config_wizard::store::{
    ApiKeyRole, ConfigDraft, ConfigKind, IdentityField, build_config, custom_allows_empty_api_key,
    validate_candidate_config, validate_candidate_config_with_role, validate_identity_value,
};
use crate::error::{Error, Result};
use crate::i18n::{Language, copy};
use crate::theme;
use colored::Colorize;

const OPENVIKING_SERVICE_API_KEY_URL: &str =
    "https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing";

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum IdentityMode {
    LocalNoKey,
    RootKey,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum CustomKeyMode {
    NoKey,
    UserKey,
    RootKey,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum CustomEditKeyAction {
    Keep,
    SetUserKey,
    SetRootKey,
    UseRootForNormal,
    ClearRootKey,
    ClearAllKeys,
}

pub(super) fn provider_labels(language: Language) -> [&'static str; 2] {
    match language {
        Language::En => [ConfigKind::OpenVikingService.label(), "Custom"],
        Language::ZhCn => ["OpenViking 服务（火山引擎云）", "自定义"],
    }
}

pub(super) fn api_key_label(optional: bool) -> &'static str {
    match (Language::current(), optional) {
        (Language::En, true) => "API key (optional)",
        (Language::En, false) => "API key",
        (Language::ZhCn, true) => "API Key（可选）",
        (Language::ZhCn, false) => "API Key",
    }
}

pub(super) fn custom_api_key_input_label(mode: CustomKeyMode) -> &'static str {
    match (Language::current(), mode) {
        (Language::En, CustomKeyMode::RootKey) => "Root API key",
        (Language::En, CustomKeyMode::UserKey) => "User API key",
        (Language::En, CustomKeyMode::NoKey) => "API key",
        (Language::ZhCn, CustomKeyMode::RootKey) => "Root API Key",
        (Language::ZhCn, CustomKeyMode::UserKey) => "User API Key",
        (Language::ZhCn, CustomKeyMode::NoKey) => "API Key",
    }
}

pub(super) fn custom_api_key_input_helper_lines(mode: CustomKeyMode) -> Vec<String> {
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

pub(super) fn custom_key_mode_labels_for_language(
    allow_empty: bool,
    language: Language,
) -> Vec<&'static str> {
    match (allow_empty, language) {
        (true, Language::En) => vec!["No key / local dev", "User API key", "Root API key"],
        (false, Language::En) => vec!["User API key", "Root API key"],
        (true, Language::ZhCn) => vec!["无密钥 / 本地开发", "User API Key", "Root API Key"],
        (false, Language::ZhCn) => vec!["User API Key", "Root API Key"],
    }
}

pub(super) fn custom_key_mode_for_selection(allow_empty: bool, index: usize) -> CustomKeyMode {
    match (allow_empty, index) {
        (true, 0) => CustomKeyMode::NoKey,
        (true, 1) | (false, 0) => CustomKeyMode::UserKey,
        _ => CustomKeyMode::RootKey,
    }
}

pub(super) fn edit_custom_key_actions(
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

pub(super) fn custom_edit_key_action_label(action: CustomEditKeyAction) -> &'static str {
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

pub(super) fn root_key_redirect_labels() -> [&'static str; 3] {
    match Language::current() {
        Language::En => ["Continue as root key", "Re-enter user key", "Cancel"],
        Language::ZhCn => ["作为 Root Key 继续", "重新输入用户 Key", "取消"],
    }
}

pub(super) fn root_key_redirect_helper_lines() -> Vec<String> {
    vec![
        theme::warning(copy(
            Language::current(),
            "This key has root access. Root keys are for admin and --sudo commands.",
            "此 Key 拥有 Root 权限。Root Key 用于管理和 --sudo 命令。",
        ))
        .to_string(),
    ]
}

pub(super) fn user_key_redirect_labels() -> [&'static str; 3] {
    match Language::current() {
        Language::En => ["Continue as user key", "Re-enter root key", "Cancel"],
        Language::ZhCn => ["作为用户 Key 继续", "重新输入 Root Key", "取消"],
    }
}

pub(super) fn user_key_redirect_helper_lines() -> Vec<String> {
    vec![
        theme::warning(copy(
            Language::current(),
            "This key does not have root access. User keys are for normal commands.",
            "此 Key 没有 Root 权限。用户 Key 用于常规命令。",
        ))
        .to_string(),
    ]
}

pub(super) fn should_confirm_detected_root_key(
    kind: ConfigKind,
    key_mode: Option<CustomKeyMode>,
    api_key_role: Option<ApiKeyRole>,
) -> bool {
    kind == ConfigKind::Custom
        && key_mode == Some(CustomKeyMode::UserKey)
        && api_key_role == Some(ApiKeyRole::Root)
}

pub(super) fn should_confirm_detected_user_key(
    kind: ConfigKind,
    key_mode: Option<CustomKeyMode>,
    api_key_role: Option<ApiKeyRole>,
) -> bool {
    kind == ConfigKind::Custom
        && key_mode == Some(CustomKeyMode::RootKey)
        && api_key_role == Some(ApiKeyRole::Regular)
}

pub(super) fn has_non_empty(value: Option<&str>) -> bool {
    value.is_some_and(|value| !value.trim().is_empty())
}

pub(super) fn has_normal_user_key(api_key: Option<&str>, root_api_key: Option<&str>) -> bool {
    let Some(api_key) = api_key.filter(|value| !value.trim().is_empty()) else {
        return false;
    };
    root_api_key
        .filter(|value| !value.trim().is_empty())
        .is_none_or(|root_api_key| root_api_key != api_key)
}

pub(crate) fn openviking_service_validation_failure_choices() -> [&'static str; 2] {
    ["Retry API key", "Cancel"]
}

pub(super) fn openviking_service_validation_failure_choices_for_language(
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

pub(super) fn custom_validation_failure_choices_for_language(
    language: Language,
) -> [&'static str; 3] {
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

pub(super) fn edit_api_key_choice_labels_for_language(
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

pub(super) struct ValidatedConfig {
    pub(super) config: Config,
    pub(super) api_key_role: Option<ApiKeyRole>,
    pub(super) root_api_key_role: Option<ApiKeyRole>,
}

pub(super) async fn validate_draft(
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

pub(super) fn root_key_normal_command_notice_lines(config: &Config) -> Vec<String> {
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

pub(super) fn root_key_used_for_normal_commands(config: &Config) -> bool {
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

pub(super) fn prompt_identity_value(
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

pub(super) fn identity_prompt_parts(
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

pub(super) fn empty_to_none(value: String) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

pub(super) fn non_empty_string(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

pub(super) fn non_empty_str(value: &str) -> Option<&str> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

fn is_blank(value: Option<&str>) -> bool {
    value.is_none_or(|value| value.trim().is_empty())
}

#[cfg(test)]
#[path = "tests/credentials.rs"]
mod tests;
