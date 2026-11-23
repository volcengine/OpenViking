//! Root administration requests and account/user selection workflows.

use super::credentials::{
    IdentityMode, identity_prompt_parts, non_empty_string, prompt_identity_value,
};
use super::prompts::{
    LiveRegion, PromptResult, confirm, prompt_select, prompt_text, status_live_lines,
};
use crate::base_client::BaseClient;
use crate::config::Config;
use crate::config_wizard::store::{IdentityField, validate_identity_value};
use crate::error::{Error, Result};
use crate::i18n::{Language, copy};
use crate::theme;
use colored::Colorize;
use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RootAccountSummary {
    pub(super) account_id: String,
    pub(super) user_count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RootUserSummary {
    pub(super) user_id: String,
    pub(super) role: String,
    pub(super) api_key: Option<String>,
    pub(super) key_prefix: Option<String>,
}

pub(super) async fn select_user_with_root_key(
    ui: &mut LiveRegion,
    section: &str,
    config: &Config,
    root_api_key: &str,
) -> Result<PromptResult<Config>> {
    let client = root_admin_client(config, root_api_key);

    loop {
        ui.render(&status_live_lines(
            section,
            copy(
                Language::current(),
                "Loading accounts...",
                "正在加载账户...",
            ),
        ))?;
        let accounts = list_root_accounts(&client).await?;

        if accounts.is_empty() {
            match confirm(
                ui,
                section,
                copy(
                    Language::current(),
                    "No accounts found. Create an account and first user?",
                    "未找到任何账户。是否创建账户和首个用户？",
                ),
                true,
            )? {
                PromptResult::Value(true) => {
                    return create_account_and_user(ui, section, config, root_api_key, &client)
                        .await;
                }
                PromptResult::Value(false) | PromptResult::Back => return Ok(PromptResult::Back),
                PromptResult::Quit => return Ok(PromptResult::Quit),
            }
        }

        let labels = root_key_account_selection_labels(&accounts);
        let account_index = match prompt_select(
            ui,
            section,
            copy(Language::current(), "Choose account", "选择账户"),
            &labels,
            0,
            &account_select_helper_lines(),
        )? {
            PromptResult::Value(index) => index,
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        };

        if account_index == accounts.len() {
            return create_account_and_user(ui, section, config, root_api_key, &client).await;
        }

        let account = accounts[account_index].clone();
        match select_user_in_account(ui, section, config, root_api_key, &client, &account).await? {
            PromptResult::Value(config) => return Ok(PromptResult::Value(config)),
            PromptResult::Back => continue,
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

async fn select_user_in_account(
    ui: &mut LiveRegion,
    section: &str,
    config: &Config,
    root_api_key: &str,
    client: &BaseClient,
    account: &RootAccountSummary,
) -> Result<PromptResult<Config>> {
    loop {
        ui.render(&status_live_lines(
            section,
            copy(Language::current(), "Loading users...", "正在加载用户..."),
        ))?;
        let users = list_root_users(client, &account.account_id).await?;

        if users.is_empty() {
            match confirm(
                ui,
                section,
                &empty_account_create_user_prompt(&account.account_id),
                true,
            )? {
                PromptResult::Value(true) => {
                    return create_user_in_account(
                        ui,
                        section,
                        config,
                        root_api_key,
                        client,
                        &account.account_id,
                        &users,
                    )
                    .await;
                }
                PromptResult::Value(false) | PromptResult::Back => return Ok(PromptResult::Back),
                PromptResult::Quit => return Ok(PromptResult::Quit),
            }
        }

        let labels = root_key_user_selection_labels(&account.account_id, &users);
        let user_index = match prompt_select(
            ui,
            section,
            &choose_user_prompt(&account.account_id),
            &labels,
            0,
            &user_select_helper_lines(),
        )? {
            PromptResult::Value(index) => index,
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        };

        if user_index == users.len() {
            return create_user_in_account(
                ui,
                section,
                config,
                root_api_key,
                client,
                &account.account_id,
                &users,
            )
            .await;
        }

        let user = users[user_index].clone();
        let user_key = match user.api_key.as_deref().and_then(non_empty_string) {
            Some(key) => key,
            None => {
                let mut helper_lines = regenerate_user_key_helper_lines();
                if let Some(prefix) = user.key_prefix.as_deref().and_then(non_empty_string) {
                    helper_lines.push(format!(
                        "{} {}",
                        theme::muted(copy(
                            Language::current(),
                            "Current key prefix:",
                            "当前 Key 前缀："
                        )),
                        theme::value(prefix).bold()
                    ));
                }
                match prompt_select(
                    ui,
                    section,
                    &regenerate_user_key_prompt(&account.account_id, &user.user_id),
                    &regenerate_user_key_labels(),
                    1,
                    &helper_lines,
                )? {
                    PromptResult::Value(0) => {
                        ui.render(&status_live_lines(
                            section,
                            copy(
                                Language::current(),
                                "Generating user API key...",
                                "正在生成用户 API Key...",
                            ),
                        ))?;
                        let response =
                            regenerate_user_key(client, &account.account_id, &user.user_id).await?;
                        user_key_from_response(&response).ok_or_else(|| {
                            Error::Config(
                                "Server did not return a user API key for the selected user."
                                    .to_string(),
                            )
                        })?
                    }
                    PromptResult::Value(_) | PromptResult::Back => continue,
                    PromptResult::Quit => return Ok(PromptResult::Quit),
                }
            }
        };

        return Ok(PromptResult::Value(config_with_selected_user(
            config,
            root_api_key,
            &account.account_id,
            &user.user_id,
            &user_key,
        )));
    }
}

pub(super) async fn create_account_and_user(
    ui: &mut LiveRegion,
    section: &str,
    config: &Config,
    root_api_key: &str,
    client: &BaseClient,
) -> Result<PromptResult<Config>> {
    let account_id = match prompt_identity_value(
        ui,
        section,
        copy(Language::current(), "New account ID", "新账户 ID"),
        IdentityField::Account,
        IdentityMode::RootKey,
    )? {
        PromptResult::Value(value) => value,
        PromptResult::Back => return Ok(PromptResult::Back),
        PromptResult::Quit => return Ok(PromptResult::Quit),
    };
    let user_id = match prompt_identity_value(
        ui,
        section,
        copy(
            Language::current(),
            "First admin user ID",
            "首个管理员用户 ID",
        ),
        IdentityField::User,
        IdentityMode::RootKey,
    )? {
        PromptResult::Value(value) => value,
        PromptResult::Back => return Ok(PromptResult::Back),
        PromptResult::Quit => return Ok(PromptResult::Quit),
    };

    ui.render(&status_live_lines(
        section,
        copy(
            Language::current(),
            "Creating account and user...",
            "正在创建账户和用户...",
        ),
    ))?;
    let response = create_root_account(client, &account_id, &user_id).await?;
    let user_key = user_key_from_response(&response).ok_or_else(|| {
        Error::Config("Server did not return a user API key for the new account.".to_string())
    })?;
    Ok(PromptResult::Value(config_with_selected_user(
        config,
        root_api_key,
        &account_id,
        &user_id,
        &user_key,
    )))
}

pub(super) async fn create_user_in_account(
    ui: &mut LiveRegion,
    section: &str,
    config: &Config,
    root_api_key: &str,
    client: &BaseClient,
    account_id: &str,
    existing_users: &[RootUserSummary],
) -> Result<PromptResult<Config>> {
    let user_id = match prompt_new_user_id(
        ui,
        section,
        copy(Language::current(), "New user ID", "新用户 ID"),
        existing_users,
    )? {
        PromptResult::Value(value) => value,
        PromptResult::Back => return Ok(PromptResult::Back),
        PromptResult::Quit => return Ok(PromptResult::Quit),
    };

    ui.render(&status_live_lines(
        section,
        copy(Language::current(), "Creating user...", "正在创建用户..."),
    ))?;
    let response = create_root_user(client, account_id, &user_id).await?;
    let user_key = user_key_from_response(&response).ok_or_else(|| {
        Error::Config("Server did not return a user API key for the new user.".to_string())
    })?;
    Ok(PromptResult::Value(config_with_selected_user(
        config,
        root_api_key,
        account_id,
        &user_id,
        &user_key,
    )))
}

pub(super) fn root_admin_client(config: &Config, root_api_key: &str) -> BaseClient {
    BaseClient::new(
        &config.url,
        Some(root_api_key.to_string()),
        None,
        None,
        None,
        config.timeout.clamp(1.0, 60.0),
        config.profile,
        config.effective_extra_headers(),
    )
    .with_gateway_token(config.effective_gateway_token())
}

pub(super) async fn list_root_accounts(client: &BaseClient) -> Result<Vec<RootAccountSummary>> {
    let value: Value = client.get("/api/v1/admin/accounts", &[]).await?;
    Ok(root_accounts_from_value(&value))
}

pub(super) async fn list_root_users(
    client: &BaseClient,
    account_id: &str,
) -> Result<Vec<RootUserSummary>> {
    let path = format!("/api/v1/admin/accounts/{account_id}/users");
    let value: Value = client
        .get(&path, &[("limit".to_string(), "1000".to_string())])
        .await?;
    Ok(root_users_from_value(&value))
}

async fn create_root_account(
    client: &BaseClient,
    account_id: &str,
    admin_user_id: &str,
) -> Result<Value> {
    client
        .post(
            "/api/v1/admin/accounts",
            &serde_json::json!({
                "account_id": account_id,
                "admin_user_id": admin_user_id,
            }),
        )
        .await
}

async fn create_root_user(client: &BaseClient, account_id: &str, user_id: &str) -> Result<Value> {
    let path = format!("/api/v1/admin/accounts/{account_id}/users");
    client
        .post(
            &path,
            &serde_json::json!({
                "user_id": user_id,
                "role": "user",
            }),
        )
        .await
}

pub(super) async fn delete_root_account(client: &BaseClient, account_id: &str) -> Result<Value> {
    let path = format!("/api/v1/admin/accounts/{account_id}");
    client.delete(&path, &[]).await
}

pub(super) async fn delete_root_user(
    client: &BaseClient,
    account_id: &str,
    user_id: &str,
) -> Result<()> {
    let path = format!("/api/v1/admin/accounts/{account_id}/users/{user_id}");
    let _: Value = client.delete(&path, &[]).await?;
    Ok(())
}

pub(super) async fn regenerate_user_key(
    client: &BaseClient,
    account_id: &str,
    user_id: &str,
) -> Result<Value> {
    let path = format!("/api/v1/admin/accounts/{account_id}/users/{user_id}/key");
    client.post(&path, &serde_json::json!({})).await
}

fn root_accounts_from_value(value: &Value) -> Vec<RootAccountSummary> {
    let mut accounts = value
        .as_array()
        .into_iter()
        .flat_map(|items| items.iter())
        .filter_map(|item| {
            let account_id = item.get("account_id")?.as_str()?.trim();
            if account_id.is_empty()
                || item.get("status").and_then(Value::as_str) == Some("deleting")
            {
                return None;
            }
            Some(RootAccountSummary {
                account_id: account_id.to_string(),
                user_count: item.get("user_count").and_then(Value::as_u64).unwrap_or(0),
            })
        })
        .collect::<Vec<_>>();
    accounts.sort_by(|left, right| left.account_id.cmp(&right.account_id));
    accounts
}

fn root_users_from_value(value: &Value) -> Vec<RootUserSummary> {
    let mut users = value
        .as_array()
        .into_iter()
        .flat_map(|items| items.iter())
        .filter_map(|item| {
            let user_id = item.get("user_id")?.as_str()?.trim();
            if user_id.is_empty() {
                return None;
            }
            Some(RootUserSummary {
                user_id: user_id.to_string(),
                role: item
                    .get("role")
                    .and_then(Value::as_str)
                    .unwrap_or("user")
                    .to_string(),
                api_key: item
                    .get("api_key")
                    .and_then(Value::as_str)
                    .and_then(non_empty_string),
                key_prefix: item
                    .get("key_prefix")
                    .and_then(Value::as_str)
                    .and_then(non_empty_string),
            })
        })
        .collect::<Vec<_>>();
    users.sort_by(|left, right| left.user_id.cmp(&right.user_id));
    users
}

pub(super) fn user_key_from_response(value: &Value) -> Option<String> {
    ["user_key", "api_key"]
        .iter()
        .find_map(|field| {
            value
                .get(field)
                .and_then(Value::as_str)
                .and_then(non_empty_string)
        })
        .or_else(|| value.get("result").and_then(user_key_from_response))
}

pub(super) fn config_with_selected_user(
    config: &Config,
    root_api_key: &str,
    account_id: &str,
    user_id: &str,
    user_key: &str,
) -> Config {
    let mut updated = config.clone();
    updated.root_api_key = Some(root_api_key.to_string());
    updated.api_key = Some(user_key.to_string());
    updated.account = Some(account_id.to_string());
    updated.user = Some(user_id.to_string());
    updated
}

pub(super) fn account_tree_labels(accounts: &[RootAccountSummary]) -> Vec<String> {
    accounts
        .iter()
        .map(|account| {
            format!(
                "{}/  {}",
                account.account_id,
                user_count_label(account.user_count)
            )
        })
        .collect()
}

pub(super) fn user_tree_labels(account_id: &str, users: &[RootUserSummary]) -> Vec<String> {
    users
        .iter()
        .map(|user| format!("{account_id}/{}  {}", user.user_id, user.role))
        .collect()
}

fn root_key_account_selection_labels(accounts: &[RootAccountSummary]) -> Vec<String> {
    let mut labels = account_tree_labels(accounts);
    labels.push(create_account_label());
    labels
}

fn root_key_user_selection_labels(account_id: &str, users: &[RootUserSummary]) -> Vec<String> {
    let mut labels = user_tree_labels(account_id, users);
    labels.push(create_user_label(account_id));
    labels
}

fn user_count_label(count: u64) -> String {
    match (Language::current(), count) {
        (Language::En, 1) => "1 user".to_string(),
        (Language::En, _) => format!("{count} users"),
        (Language::ZhCn, _) => format!("{count} 个用户"),
    }
}

fn create_account_label() -> String {
    match Language::current() {
        Language::En => "+ Create new account",
        Language::ZhCn => "+ 创建新账户",
    }
    .to_string()
}

fn create_user_label(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("+ Create new user in {account_id}/"),
        Language::ZhCn => format!("+ 在 {account_id}/ 下创建新用户"),
    }
}

pub(super) fn root_api_key_helper_lines(server_url: &str) -> Vec<String> {
    vec![
        format!(
            "{} {}",
            theme::muted(copy(Language::current(), "Server URL:", "服务器 URL：")),
            theme::value(server_url).bold()
        ),
        theme::muted(copy(
            Language::current(),
            "Used only to list or create accounts/users and store a normal user key.",
            "仅用于列出或创建账户/用户，并保存普通用户 Key。",
        ))
        .to_string(),
    ]
}

fn account_select_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "Accounts are shown like directories.",
            "账户以类似目录的方式展示。",
        ))
        .to_string(),
    ]
}

fn user_select_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "Pick the user normal commands should run as.",
            "选择常规命令要使用的用户身份。",
        ))
        .to_string(),
    ]
}

fn regenerate_user_key_labels() -> [&'static str; 2] {
    match Language::current() {
        Language::En => ["Regenerate and use this user", "Back"],
        Language::ZhCn => ["重新生成并使用该用户", "返回"],
    }
}

fn regenerate_user_key_helper_lines() -> Vec<String> {
    vec![
        theme::warning(copy(
            Language::current(),
            "This server does not expose the existing user key. Regenerating invalidates the old key immediately.",
            "服务端未返回现有用户 Key。重新生成会立即使旧 Key 失效。",
        ))
        .to_string(),
    ]
}

fn choose_user_prompt(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Choose user in {account_id}/"),
        Language::ZhCn => format!("选择 {account_id}/ 下的用户"),
    }
}

fn empty_account_create_user_prompt(account_id: &str) -> String {
    match Language::current() {
        Language::En => format!("No users in {account_id}/. Create one?"),
        Language::ZhCn => format!("{account_id}/ 下没有用户。是否创建？"),
    }
}

fn regenerate_user_key_prompt(account_id: &str, user_id: &str) -> String {
    match Language::current() {
        Language::En => format!("Use {account_id}/{user_id} by generating a new user key?"),
        Language::ZhCn => format!("为 {account_id}/{user_id} 生成新用户 Key 并使用？"),
    }
}

fn prompt_new_user_id(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    existing_users: &[RootUserSummary],
) -> Result<PromptResult<String>> {
    let (_, _, helper_lines) = identity_prompt_parts(IdentityMode::RootKey);
    let mut error: Option<String> = None;
    loop {
        let mut lines = helper_lines.clone();
        if let Some(value) = error.as_ref() {
            lines.push(theme::error(value).to_string());
        }
        match prompt_text(ui, section, prompt, None, None, false, false, &lines)? {
            PromptResult::Value(value) => match validate_new_user_id(&value, existing_users) {
                Ok(()) => return Ok(PromptResult::Value(value)),
                Err(next_error) => error = Some(next_error.to_string()),
            },
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

fn validate_new_user_id(value: &str, existing_users: &[RootUserSummary]) -> Result<()> {
    validate_identity_value(value, IdentityField::User)?;
    if existing_users.iter().any(|user| user.user_id == value) {
        return Err(Error::Config(duplicate_user_id_error(value)));
    }
    Ok(())
}

fn duplicate_user_id_error(user_id: &str) -> String {
    match Language::current() {
        Language::En => format!("User '{user_id}' already exists. Enter another user ID."),
        Language::ZhCn => format!("用户 '{user_id}' 已存在。请输入另一个用户 ID。"),
    }
}

#[cfg(test)]
#[path = "tests/admin.rs"]
mod tests;
