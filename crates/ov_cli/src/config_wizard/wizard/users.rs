//! Account and user management menus and local selection persistence.

use super::admin::{
    RootAccountSummary, RootUserSummary, account_tree_labels, config_with_selected_user,
    create_account_and_user, create_user_in_account, delete_root_account, delete_root_user,
    list_root_accounts, list_root_users, regenerate_user_key, root_admin_client,
    root_api_key_helper_lines, user_key_from_response, user_tree_labels,
};
use super::credentials::{non_empty_str, non_empty_string};
use super::profiles::{
    active_config_index, allocate_config_name, current_server_url_default,
    is_legacy_user_management_config_name, local_config_name_choice_labels,
    prompt_unique_config_name, prompt_validation_error,
};
use super::prompts::{
    InputValueLabel, LiveRegion, PromptResult, confirm, print_cancelled, prompt_select,
    prompt_text, status_live_lines,
};
use crate::config::Config;
use crate::config_wizard::store::{ConfigKind, ConfigStore, normalize_custom_url, validate_config};
use crate::error::{Error, Result};
use crate::i18n::{Language, copy};
use crate::theme;
use colored::Colorize;

fn section_user_management() -> &'static str {
    copy(
        Language::current(),
        "Manage account and user credentials.",
        "管理账户和用户凭证。",
    )
}

pub(super) async fn run_user_management(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    let UserManagementContext {
        config_name,
        mut config,
    } = match user_management_config_context(store)? {
        Some(context) => context,
        None => match prompt_user_management_server_url(store, ui)? {
            PromptResult::Value(context) => context,
            PromptResult::Back => return Ok(false),
            PromptResult::Quit => {
                print_cancelled(ui)?;
                return Ok(true);
            }
        },
    };

    let current_root_api_key = config.root_api_key.as_deref().and_then(non_empty_str);
    let root_api_key = match prompt_text(
        ui,
        section_user_management(),
        copy(Language::current(), "Root API key", "Root API Key"),
        current_root_api_key,
        current_root_api_key.map(|_| InputValueLabel::Current),
        false,
        true,
        &root_api_key_helper_lines(&config.url),
    )? {
        PromptResult::Value(value) => value,
        PromptResult::Back => return Ok(false),
        PromptResult::Quit => {
            print_cancelled(ui)?;
            return Ok(true);
        }
    };
    config.root_api_key = Some(root_api_key.clone());

    let mut config_name = config_name;
    run_user_management_menu(store, ui, &mut config_name, &mut config, &root_api_key).await
}

struct UserManagementContext {
    config_name: Option<String>,
    config: Config,
}

fn user_management_config_context(store: &ConfigStore) -> Result<Option<UserManagementContext>> {
    let configs = store.list_configs()?;
    if let Some(index) = active_config_index(&configs) {
        let entry = &configs[index];
        let config_name = if is_legacy_user_management_config_name(&entry.name) {
            None
        } else {
            Some(entry.name.clone())
        };
        return Ok(Some(UserManagementContext {
            config_name,
            config: entry.config.clone(),
        }));
    }

    if let Some(config) = store.load_active()? {
        return Ok(Some(UserManagementContext {
            config_name: None,
            config,
        }));
    }

    Ok(None)
}

fn prompt_user_management_config_name(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    current_name: Option<&str>,
    config: &Config,
) -> Result<PromptResult<String>> {
    let default_name = match current_name {
        Some(name) => name.to_string(),
        None => allocate_config_name(store, ConfigKind::from_config(config))?,
    };
    let labels = user_management_config_name_choice_labels(current_name, &default_name);
    match prompt_select(
        ui,
        section_user_management(),
        copy(
            Language::current(),
            "Save managed config name",
            "保存管理配置名称",
        ),
        &labels,
        0,
        &user_management_config_name_helper_lines(current_name),
    )? {
        PromptResult::Value(0) => prompt_unique_config_name(
            ui,
            section_user_management(),
            store,
            &default_name,
            current_name,
        ),
        PromptResult::Value(1) => Ok(PromptResult::Value(default_name)),
        PromptResult::Value(_) | PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
    }
}

fn prompt_user_management_server_url(
    store: &ConfigStore,
    ui: &mut LiveRegion,
) -> Result<PromptResult<UserManagementContext>> {
    let default = current_server_url_default(store)?;
    let mut error: Option<String> = None;
    loop {
        let mut helper_lines = user_management_server_url_helper_lines();
        if let Some(value) = error.as_ref() {
            helper_lines.push(theme::error(value).to_string());
        }
        match prompt_text(
            ui,
            section_user_management(),
            copy(Language::current(), "Server URL", "服务器 URL"),
            Some(&default),
            Some(InputValueLabel::Default),
            false,
            false,
            &helper_lines,
        )? {
            PromptResult::Value(value) => match user_management_config_from_server_url(&value) {
                Ok(config) => {
                    return Ok(PromptResult::Value(UserManagementContext {
                        config_name: None,
                        config,
                    }));
                }
                Err(next_error) => error = Some(prompt_validation_error(next_error)),
            },
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

fn user_management_config_from_server_url(value: &str) -> Result<Config> {
    let url = normalize_custom_url(value);
    if url.trim().is_empty() {
        return Err(Error::Config("Server URL cannot be empty.".to_string()));
    }
    Ok(Config {
        url: url.trim_end_matches('/').to_string(),
        ..Config::default()
    })
}

async fn run_user_management_menu(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    config_name: &mut Option<String>,
    config: &mut Config,
    root_api_key: &str,
) -> Result<bool> {
    let mut notice: Option<String> = None;

    loop {
        let client = root_admin_client(config, root_api_key);
        ui.render(&status_live_lines(
            section_user_management(),
            copy(
                Language::current(),
                "Loading accounts...",
                "正在加载账户...",
            ),
        ))?;
        let accounts = list_root_accounts(&client).await?;

        let mut labels = account_tree_labels(&accounts);
        labels.push(add_account_label());
        labels.push(back_label());
        let helper_lines =
            user_management_menu_helper_lines(config_name.as_deref(), config, notice.as_deref());
        notice = None;

        let account_index = match prompt_select(
            ui,
            section_user_management(),
            copy(
                Language::current(),
                "Select account or action",
                "选择账户或操作",
            ),
            &labels,
            0,
            &helper_lines,
        )? {
            PromptResult::Value(index) => index,
            PromptResult::Back => return Ok(false),
            PromptResult::Quit => {
                print_cancelled(ui)?;
                return Ok(true);
            }
        };

        if account_index == accounts.len() {
            match create_account_and_user(
                ui,
                section_user_management(),
                config,
                root_api_key,
                &client,
            )
            .await?
            {
                PromptResult::Value(updated) => {
                    notice = created_identity_notice(&updated);
                }
                PromptResult::Back => continue,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            }
            continue;
        }

        if account_index == accounts.len() + 1 {
            return Ok(false);
        }

        if run_account_management_menu(
            store,
            ui,
            config_name,
            config,
            root_api_key,
            &accounts[account_index],
            &mut notice,
        )
        .await?
        {
            return Ok(true);
        }
    }
}

async fn run_account_management_menu(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    config_name: &mut Option<String>,
    config: &mut Config,
    root_api_key: &str,
    account: &RootAccountSummary,
    account_list_notice: &mut Option<String>,
) -> Result<bool> {
    let mut notice: Option<String> = None;

    loop {
        let client = root_admin_client(config, root_api_key);
        ui.render(&status_live_lines(
            section_user_management(),
            copy(Language::current(), "Loading users...", "正在加载用户..."),
        ))?;
        let users = list_root_users(&client, &account.account_id).await?;

        let mut labels = user_tree_labels(&account.account_id, &users);
        labels.push(add_user_label(&account.account_id));
        labels.push(delete_account_label(&account.account_id));
        labels.push(back_label());
        let helper_lines = account_management_menu_helper_lines(
            config_name.as_deref(),
            config,
            &account.account_id,
            notice.as_deref(),
        );
        notice = None;

        let selected = match prompt_select(
            ui,
            section_user_management(),
            &manage_account_prompt(&account.account_id),
            &labels,
            0,
            &helper_lines,
        )? {
            PromptResult::Value(index) => index,
            PromptResult::Back => return Ok(false),
            PromptResult::Quit => {
                print_cancelled(ui)?;
                return Ok(true);
            }
        };

        if selected < users.len() {
            if run_user_action_menu(
                store,
                ui,
                config_name,
                config,
                root_api_key,
                &account.account_id,
                &users[selected],
                &mut notice,
            )
            .await?
            {
                return Ok(true);
            }
            continue;
        }

        let add_user_index = users.len();
        let delete_account_index = users.len() + 1;
        let back_index = users.len() + 2;

        if selected == add_user_index {
            match create_user_in_account(
                ui,
                section_user_management(),
                config,
                root_api_key,
                &client,
                &account.account_id,
                &users,
            )
            .await?
            {
                PromptResult::Value(updated) => {
                    notice = created_identity_notice(&updated);
                }
                PromptResult::Back => continue,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            }
            continue;
        }

        if selected == delete_account_index {
            match confirm(
                ui,
                section_user_management(),
                &delete_account_prompt(&account.account_id),
                false,
            )? {
                PromptResult::Value(true) => {
                    ui.render(&status_live_lines(
                        section_user_management(),
                        copy(
                            Language::current(),
                            "Deleting account...",
                            "正在删除账户...",
                        ),
                    ))?;
                    let task = delete_root_account(&client, &account.account_id).await?;
                    if clear_config_account_selection(config, root_api_key, &account.account_id) {
                        save_user_management_local_config(store, config_name.as_deref(), config)?;
                    }
                    let task_id = task["task_id"].as_str().ok_or_else(|| {
                        Error::Parse("Account deletion response has no task_id".to_string())
                    })?;
                    *account_list_notice =
                        Some(account_deletion_notice(&account.account_id, task_id));
                    return Ok(false);
                }
                PromptResult::Value(false) | PromptResult::Back => continue,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            }
        }

        if selected == back_index {
            return Ok(false);
        }
    }
}

async fn run_user_action_menu(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    config_name: &mut Option<String>,
    config: &mut Config,
    root_api_key: &str,
    account_id: &str,
    user: &RootUserSummary,
    account_notice: &mut Option<String>,
) -> Result<bool> {
    #[derive(Clone, Copy)]
    enum UserAction {
        UseExisting,
        RegenerateAndUse,
        Delete,
        Back,
    }

    loop {
        let mut actions = Vec::new();
        let mut labels = Vec::new();
        if user.api_key.as_deref().and_then(non_empty_string).is_some() {
            actions.push(UserAction::UseExisting);
            labels.push(use_user_label());
        }
        actions.push(UserAction::RegenerateAndUse);
        labels.push(regenerate_and_use_user_label());
        actions.push(UserAction::Delete);
        labels.push(delete_user_label(account_id, &user.user_id));
        actions.push(UserAction::Back);
        labels.push(back_label());

        let helper_lines =
            user_action_menu_helper_lines(config_name.as_deref(), config, account_id, user);
        let selected = match prompt_select(
            ui,
            section_user_management(),
            &manage_user_prompt(account_id, &user.user_id),
            &labels,
            0,
            &helper_lines,
        )? {
            PromptResult::Value(index) => actions[index],
            PromptResult::Back => return Ok(false),
            PromptResult::Quit => {
                print_cancelled(ui)?;
                return Ok(true);
            }
        };

        let (user_key, save_action) = match selected {
            UserAction::UseExisting => (
                user.api_key
                    .as_deref()
                    .and_then(non_empty_string)
                    .ok_or_else(|| {
                        Error::Config(
                            "Server did not return a user API key for the selected user."
                                .to_string(),
                        )
                    })?,
                UserKeySaveAction::Selected,
            ),
            UserAction::RegenerateAndUse => {
                let client = root_admin_client(config, root_api_key);
                ui.render(&status_live_lines(
                    section_user_management(),
                    copy(
                        Language::current(),
                        "Generating user API key...",
                        "正在生成用户 API Key...",
                    ),
                ))?;
                let response = regenerate_user_key(&client, account_id, &user.user_id).await?;
                let user_key = user_key_from_response(&response).ok_or_else(|| {
                    Error::Config(
                        "Server did not return a user API key for the selected user.".to_string(),
                    )
                })?;
                (user_key, UserKeySaveAction::Regenerated)
            }
            UserAction::Delete => match confirm(
                ui,
                section_user_management(),
                &delete_user_prompt(account_id, &user.user_id),
                false,
            )? {
                PromptResult::Value(true) => {
                    let client = root_admin_client(config, root_api_key);
                    ui.render(&status_live_lines(
                        section_user_management(),
                        copy(Language::current(), "Deleting user...", "正在删除用户..."),
                    ))?;
                    delete_root_user(&client, account_id, &user.user_id).await?;
                    if clear_config_user_selection(config, root_api_key, account_id, &user.user_id)
                    {
                        save_user_management_local_config(store, config_name.as_deref(), config)?;
                    }
                    *account_notice = Some(deleted_user_notice(account_id, &user.user_id));
                    return Ok(false);
                }
                PromptResult::Value(false) | PromptResult::Back => continue,
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            UserAction::Back => return Ok(false),
        };

        let updated =
            config_with_selected_user(config, root_api_key, account_id, &user.user_id, &user_key);
        let saved_name = match save_selected_user_to_config(
            store,
            ui,
            section_user_management(),
            config_name,
            &updated,
        )
        .await?
        {
            PromptResult::Value(name) => name,
            PromptResult::Back => continue,
            PromptResult::Quit => {
                print_cancelled(ui)?;
                return Ok(true);
            }
        };
        *account_notice = selected_user_notice(&saved_name, &updated, save_action);
        *config = updated;
        return Ok(false);
    }
}

async fn save_selected_user_to_config(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    section: &str,
    config_name: &mut Option<String>,
    config: &Config,
) -> Result<PromptResult<String>> {
    ui.render(&status_live_lines(
        section,
        copy(
            Language::current(),
            "Validating selected user...",
            "正在验证所选用户...",
        ),
    ))?;
    validate_config(config).await?;
    let config_name = match ensure_user_management_config_name(store, ui, config_name, config)? {
        PromptResult::Value(name) => name,
        PromptResult::Back => return Ok(PromptResult::Back),
        PromptResult::Quit => return Ok(PromptResult::Quit),
    };
    ui.render(&status_live_lines(
        section,
        copy(
            Language::current(),
            "Saving user selection...",
            "正在保存用户选择...",
        ),
    ))?;
    store.save_and_activate(&config_name, config)?;
    Ok(PromptResult::Value(config_name))
}

fn ensure_user_management_config_name(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    config_name: &mut Option<String>,
    config: &Config,
) -> Result<PromptResult<String>> {
    match prompt_user_management_config_name(store, ui, config_name.as_deref(), config)? {
        PromptResult::Value(name) => {
            *config_name = Some(name.clone());
            Ok(PromptResult::Value(name))
        }
        PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
    }
}

fn save_user_management_local_config(
    store: &ConfigStore,
    config_name: Option<&str>,
    config: &Config,
) -> Result<()> {
    if let Some(name) = config_name {
        store.save_and_activate(name, config)
    } else {
        store.save_active_config(config)
    }
}

fn clear_config_account_selection(
    config: &mut Config,
    root_api_key: &str,
    account_id: &str,
) -> bool {
    if config.account.as_deref() != Some(account_id) {
        return false;
    }
    clear_config_user_fields(config, root_api_key);
    true
}

fn clear_config_user_selection(
    config: &mut Config,
    root_api_key: &str,
    account_id: &str,
    user_id: &str,
) -> bool {
    if config.account.as_deref() != Some(account_id) || config.user.as_deref() != Some(user_id) {
        return false;
    }
    clear_config_user_fields(config, root_api_key);
    true
}

fn clear_config_user_fields(config: &mut Config, root_api_key: &str) {
    config.root_api_key = Some(root_api_key.to_string());
    config.api_key = None;
    config.account = None;
    config.user = None;
}

fn user_management_menu_helper_lines(
    config_name: Option<&str>,
    config: &Config,
    notice: Option<&str>,
) -> Vec<String> {
    let mut lines = Vec::new();
    push_notice_helper_line(&mut lines, notice);
    push_config_context_helper_lines(&mut lines, config_name, config);
    lines.push(
        theme::muted(copy(
            Language::current(),
            "Select an account to manage its users.",
            "选择账户以管理其用户。",
        ))
        .to_string(),
    );
    lines
}

fn account_management_menu_helper_lines(
    config_name: Option<&str>,
    config: &Config,
    account_id: &str,
    notice: Option<&str>,
) -> Vec<String> {
    let mut lines = Vec::new();
    push_notice_helper_line(&mut lines, notice);
    push_config_context_helper_lines(&mut lines, config_name, config);
    lines.push(format!(
        "{} {}",
        theme::muted(copy(Language::current(), "Account:", "账户：")),
        theme::value(account_id).bold()
    ));
    lines
}

fn user_action_menu_helper_lines(
    config_name: Option<&str>,
    config: &Config,
    account_id: &str,
    user: &RootUserSummary,
) -> Vec<String> {
    let mut lines = Vec::new();
    push_config_context_helper_lines(&mut lines, config_name, config);
    lines.push(format!(
        "{} {}",
        theme::muted(copy(Language::current(), "User:", "用户：")),
        theme::value(format!("{account_id}/{}", user.user_id)).bold()
    ));
    if let Some(prefix) = user.key_prefix.as_deref().and_then(non_empty_string) {
        lines.push(format!(
            "{} {}",
            theme::muted(copy(
                Language::current(),
                "Current key prefix:",
                "当前 Key 前缀："
            )),
            theme::value(prefix).bold()
        ));
    }
    lines.push(
        theme::warning(copy(
            Language::current(),
            "Regenerating a user key invalidates the old key immediately.",
            "重新生成用户 Key 会立即使旧 Key 失效。",
        ))
        .to_string(),
    );
    lines
}

fn push_notice_helper_line(lines: &mut Vec<String>, notice: Option<&str>) {
    if let Some(notice) = notice {
        lines.push(format!(
            "{} {}",
            theme::success("✓"),
            theme::success(notice).bold()
        ));
    }
}

fn push_config_context_helper_lines(
    lines: &mut Vec<String>,
    config_name: Option<&str>,
    config: &Config,
) {
    let config_name = config_name
        .map(ToString::to_string)
        .unwrap_or_else(|| unnamed_active_config_label().to_string());
    lines.push(format!(
        "{} {}",
        theme::muted(copy(Language::current(), "Config:", "配置：")),
        theme::value(config_name).bold()
    ));
    lines.push(format!(
        "{} {}",
        theme::muted(copy(Language::current(), "Server:", "服务端：")),
        theme::value(config.url.as_str()).bold()
    ));
    if let (Some(account), Some(user)) = (
        config.account.as_deref().and_then(non_empty_str),
        config.user.as_deref().and_then(non_empty_str),
    ) {
        lines.push(format!(
            "{} {}",
            theme::muted(copy(Language::current(), "Selected user:", "已选用户：")),
            theme::value(format!("{account}/{user}")).bold()
        ));
    }
}

fn unnamed_active_config_label() -> &'static str {
    copy(
        Language::current(),
        "unnamed active config",
        "未命名当前配置",
    )
}

fn created_identity_notice(config: &Config) -> Option<String> {
    let account = config.account.as_deref().and_then(non_empty_str)?;
    let user = config.user.as_deref().and_then(non_empty_str)?;
    Some(match Language::current() {
        Language::En => {
            format!("Created {account}/{user}. Choose that user and use it to save CLI config.")
        }
        Language::ZhCn => {
            format!("已创建 {account}/{user}。选择该用户并使用它，才会保存 CLI 配置。")
        }
    })
}

#[derive(Clone, Copy)]
enum UserKeySaveAction {
    Selected,
    Regenerated,
}

fn selected_user_notice(
    config_name: &str,
    config: &Config,
    action: UserKeySaveAction,
) -> Option<String> {
    let account = config.account.as_deref().and_then(non_empty_str)?;
    let user = config.user.as_deref().and_then(non_empty_str)?;
    let target = user_management_save_target(config_name);
    Some(match (Language::current(), action) {
        (Language::En, UserKeySaveAction::Selected) => {
            format!("Selected {account}/{user}; saved user key to {target}.")
        }
        (Language::En, UserKeySaveAction::Regenerated) => {
            format!("Regenerated key for {account}/{user}; saved new key to {target}.")
        }
        (Language::ZhCn, UserKeySaveAction::Selected) => {
            format!("已选择 {account}/{user}；用户 Key 已保存到 {target}。")
        }
        (Language::ZhCn, UserKeySaveAction::Regenerated) => {
            format!("已为 {account}/{user} 重新生成 Key；新 Key 已保存到 {target}。")
        }
    })
}

fn user_management_save_target(config_name: &str) -> String {
    match Language::current() {
        Language::En => format!("ovcli.conf and ovcli.conf.{config_name}"),
        Language::ZhCn => format!("ovcli.conf 和 ovcli.conf.{config_name}"),
    }
}

fn account_deletion_notice(account_id: &str, task_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Account {account_id}/ disabled. Cleanup task: {task_id}"),
        Language::ZhCn => format!("账户 {account_id}/ 已停用，后台清理任务：{task_id}"),
    }
}

fn deleted_user_notice(account_id: &str, user_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Deleted user {account_id}/{user_id}."),
        Language::ZhCn => format!("已删除用户 {account_id}/{user_id}。"),
    }
}

fn add_account_label() -> String {
    match Language::current() {
        Language::En => "+ Add account",
        Language::ZhCn => "+ 添加账户",
    }
    .to_string()
}

fn add_user_label(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("+ Add user in {account_id}/"),
        Language::ZhCn => format!("+ 在 {account_id}/ 下添加用户"),
    }
}

fn user_management_config_name_choice_labels(
    current_name: Option<&str>,
    default_name: &str,
) -> Vec<String> {
    if let Some(name) = current_name {
        return match Language::current() {
            Language::En => vec![
                "Set custom local name".to_string(),
                format!("Use current name ({name})"),
                "Back".to_string(),
            ],
            Language::ZhCn => vec![
                "设置自定义本地名称".to_string(),
                format!("使用当前名称（{name}）"),
                "返回".to_string(),
            ],
        };
    }
    local_config_name_choice_labels(default_name)
}

fn user_management_config_name_helper_lines(current_name: Option<&str>) -> Vec<String> {
    let mut lines = Vec::new();
    if current_name.is_none() {
        lines.push(
            theme::warning(copy(
                Language::current(),
                "The active config needs a saved profile name.",
                "当前配置需要一个保存配置名称。",
            ))
            .to_string(),
        );
    }
    lines.push(
        theme::muted(copy(
            Language::current(),
            "User Management saves both ovcli.conf and ovcli.conf.<name>; 'active' is reserved.",
            "用户管理会同时保存 ovcli.conf 和 ovcli.conf.<name>；'active' 已保留。",
        ))
        .to_string(),
    );
    lines
}

fn delete_account_label(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("- Delete {account_id}/"),
        Language::ZhCn => format!("- 删除 {account_id}/"),
    }
}

fn delete_user_label(account_id: &str, user_id: &str) -> String {
    match Language::current() {
        Language::En => format!("- Delete {account_id}/{user_id}"),
        Language::ZhCn => format!("- 删除 {account_id}/{user_id}"),
    }
}

fn use_user_label() -> String {
    match Language::current() {
        Language::En => "Use this user",
        Language::ZhCn => "使用该用户",
    }
    .to_string()
}

fn regenerate_and_use_user_label() -> String {
    match Language::current() {
        Language::En => "Regenerate key and use this user",
        Language::ZhCn => "重新生成 Key 并使用该用户",
    }
    .to_string()
}

fn back_label() -> String {
    match Language::current() {
        Language::En => "Back",
        Language::ZhCn => "返回",
    }
    .to_string()
}

fn manage_account_prompt(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Manage {account_id}/"),
        Language::ZhCn => format!("管理 {account_id}/"),
    }
}

fn manage_user_prompt(account_id: &str, user_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Manage {account_id}/{user_id}"),
        Language::ZhCn => format!("管理 {account_id}/{user_id}"),
    }
}

fn delete_account_prompt(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Delete account {account_id}/ and all of its users?"),
        Language::ZhCn => format!("删除账户 {account_id}/ 及其所有用户？"),
    }
}

fn delete_user_prompt(account_id: &str, user_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Delete user {account_id}/{user_id}?"),
        Language::ZhCn => format!("删除用户 {account_id}/{user_id}？"),
    }
}

fn user_management_server_url_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "User Management only needs the server URL and a Root API key.",
            "用户管理只需要服务器 URL 和 Root API Key。",
        ))
        .to_string(),
        theme::muted(copy(
            Language::current(),
            "Press Enter to use the local server default.",
            "按 Enter 使用本地服务默认地址。",
        ))
        .to_string(),
    ]
}

#[cfg(test)]
#[path = "tests/users.rs"]
mod tests;
