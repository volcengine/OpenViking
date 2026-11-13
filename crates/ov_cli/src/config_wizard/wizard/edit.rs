//! Existing configuration workflow and its backtracking state machine.

use super::admin::select_user_with_root_key;
use super::credentials::{
    CustomEditKeyAction, CustomKeyMode, IdentityMode, ValidatedConfig, api_key_label,
    custom_api_key_helper_lines, custom_api_key_input_helper_lines, custom_api_key_input_label,
    custom_edit_key_action_label, custom_key_mode_for_selection,
    custom_key_mode_labels_for_language, custom_validation_failure_choices_for_language,
    edit_api_key_choice_labels_for_language, edit_custom_key_actions, empty_to_none, has_non_empty,
    has_normal_user_key, openviking_service_api_key_helper_lines,
    openviking_service_validation_failure_choices_for_language, prompt_identity_value,
    root_key_redirect_helper_lines, root_key_redirect_labels, root_key_used_for_normal_commands,
    should_confirm_detected_root_key, should_confirm_detected_user_key,
    should_prompt_root_identity, user_key_redirect_helper_lines, user_key_redirect_labels,
    validate_draft,
};
use super::profiles::{
    SaveAction, SaveActionSet, SaveOutcome, localized_validation_error, print_saved,
    prompt_config_name, prompt_config_select, prompt_save_action, section_edit,
    validate_config_name_change,
};
use super::prompts::{
    InputValueLabel, LiveRegion, PromptResult, print_cancelled, prompt_select, prompt_text,
};
use crate::config_wizard::store::{
    ApiKeyRole, ConfigDraft, ConfigKind, ConfigStore, IdentityField, custom_allows_empty_api_key,
};
use crate::error::Result;
use crate::i18n::{Language, copy};
use crate::theme;

pub(super) async fn run_edit_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
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
                let original_name = configs[selected].name.clone();
                match prompt_config_name(
                    ui,
                    section_edit(),
                    copy(Language::current(), "Config name", "配置名称"),
                    Some(&name),
                    |value| validate_config_name_change(store, &original_name, value),
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
                        mut config,
                        mut api_key_role,
                        root_api_key_role,
                    }) => {
                        if root_key_used_for_normal_commands(&config) {
                            let root_key = config.root_api_key.clone().unwrap_or_default();
                            match select_user_with_root_key(ui, section_edit(), &config, &root_key)
                                .await?
                            {
                                PromptResult::Value(updated_config) => {
                                    config = updated_config;
                                    api_key = config.api_key.clone();
                                    root_api_key = config.root_api_key.clone();
                                    account = config.account.clone();
                                    user = config.user.clone();
                                    identity_mode = None;
                                    key_mode = Some(CustomKeyMode::UserKey);
                                    key_edit_action = Some(CustomEditKeyAction::SetUserKey);
                                    api_key_was_entered = true;
                                    api_key_role = Some(ApiKeyRole::Regular);
                                }
                                PromptResult::Back => {
                                    stage = Stage::ApiKeyChoice;
                                    continue;
                                }
                                PromptResult::Quit => {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                }
                            }
                        }
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
