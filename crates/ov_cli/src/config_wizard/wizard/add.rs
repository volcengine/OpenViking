//! New configuration workflow and its backtracking state machine.

use super::admin::select_user_with_root_key;
use super::credentials::{
    CustomKeyMode, IdentityMode, ValidatedConfig, api_key_label, custom_api_key_helper_lines,
    custom_api_key_input_helper_lines, custom_api_key_input_label, custom_key_mode_for_selection,
    custom_key_mode_labels_for_language, custom_validation_failure_choices_for_language,
    empty_to_none, openviking_service_api_key_helper_lines,
    openviking_service_validation_failure_choices_for_language, prompt_identity_value,
    provider_labels, root_key_redirect_helper_lines, root_key_redirect_labels,
    root_key_used_for_normal_commands, should_confirm_detected_root_key,
    should_confirm_detected_user_key, should_prompt_root_identity, user_key_redirect_helper_lines,
    user_key_redirect_labels, validate_draft,
};
use super::profiles::{
    SaveAction, SaveActionSet, add_config_name_label, allocate_config_name,
    current_server_url_default, ensure_add_config_save_name, localized_validation_error,
    prompt_add_config_name, prompt_save_action, save_config, section_add,
};
use super::prompts::{
    InputValueLabel, LiveRegion, PromptResult, print_cancelled, prompt_select, prompt_text,
};
use crate::config_wizard::store::{
    ApiKeyRole, ConfigDraft, ConfigKind, ConfigStore, IdentityField, OPENVIKING_SERVICE_URL,
    custom_allows_empty_api_key,
};
use crate::error::Result;
use crate::i18n::{Language, copy};
use crate::theme;

pub(super) async fn run_add_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
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
    let default_server_url = current_server_url_default(store)?;
    let mut url = default_server_url.clone();
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
                    url = default_server_url.clone();
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
                match prompt_add_config_name(ui, section_add(), add_config_name_label(), store)? {
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
                        url = default_server_url.clone();
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
                        mut config,
                        mut api_key_role,
                        root_api_key_role,
                    }) => {
                        if root_key_used_for_normal_commands(&config) {
                            let root_key = config.root_api_key.clone().unwrap_or_default();
                            match select_user_with_root_key(ui, section_add(), &config, &root_key)
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
                                    api_key_role = Some(ApiKeyRole::Regular);
                                }
                                PromptResult::Back => {
                                    stage = if kind == ConfigKind::Custom {
                                        Stage::KeyMode
                                    } else {
                                        Stage::ApiKey
                                    };
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
                        let save_name =
                            match ensure_add_config_save_name(store, ui, &mut name, &draft_name)? {
                                PromptResult::Value(name) => name,
                                PromptResult::Back => {
                                    stage = if identity_mode.is_some() {
                                        Stage::User
                                    } else if kind == ConfigKind::Custom {
                                        Stage::KeyMode
                                    } else {
                                        Stage::ApiKey
                                    };
                                    continue;
                                }
                                PromptResult::Quit => {
                                    print_cancelled(ui)?;
                                    return Ok(true);
                                }
                            };
                        match prompt_save_action(
                            ui,
                            section_add(),
                            copy(Language::current(), "Save config?", "保存配置？"),
                            SaveActionSet::Add,
                            0,
                        )? {
                            PromptResult::Value(SaveAction::SaveAndActivate) => {
                                ui.clear()?;
                                save_config(store, &save_name, &config, true)?;
                                return Ok(true);
                            }
                            PromptResult::Value(SaveAction::SaveOnly) => {
                                ui.clear()?;
                                save_config(store, &save_name, &config, false)?;
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
