use crate::client::HttpClient;
use crate::config::Config;
use crate::config_wizard::ConfigStore;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use colored::Colorize;
use serde_json::{Value, json};

const SECRET_MASK_NOTICE: &str = "Note: API keys are masked by default. Pass --reveal-api-keys to reveal plaintext (ROOT sees every key; ADMIN/USER only its own).";

pub async fn create_account(
    client: &HttpClient,
    account_id: &str,
    admin_user_id: &str,
    seed: Option<&str>,
    user_config_json: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let user_config = parse_user_config_json(user_config_json, "--user-config-json")?;
    let response = client
        .admin_create_account(account_id, admin_user_id, seed, user_config.as_ref())
        .await?;
    output_success(&response, output_format, compact);
    print_admin_user_key_notice(&response, output_format, false);
    Ok(())
}

pub async fn list_accounts(
    client: &HttpClient,
    name: Option<String>,
    limit: Option<u32>,
    page: u32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_list_accounts(name, limit, page).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn delete_account(
    client: &HttpClient,
    account_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_delete_account(account_id).await?;
    let result =
        if response.is_null() || response.as_object().map(|o| o.is_empty()).unwrap_or(false) {
            json!({"account_id": account_id})
        } else {
            response
        };
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn register_user(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    role: &str,
    seed: Option<&str>,
    user_config_json: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let user_config = parse_user_config_json(user_config_json, "--user-config-json")?;
    let response = client
        .admin_register_user(account_id, user_id, role, seed, user_config.as_ref())
        .await?;
    output_success(&response, output_format, compact);
    print_admin_user_key_notice(&response, output_format, false);
    Ok(())
}

fn parse_user_config_json(value: Option<&str>, flag: &str) -> Result<Option<Value>> {
    let Some(value) = value else {
        return Ok(None);
    };
    let parsed: Value = serde_json::from_str(value)
        .map_err(|err| Error::Client(format!("{flag} must be valid JSON: {err}")))?;
    if !parsed.is_object() {
        return Err(Error::Client(format!("{flag} must be a JSON object")));
    }
    Ok(Some(parsed))
}

pub async fn list_users(
    client: &HttpClient,
    account_id: &str,
    limit: Option<u32>,
    name: Option<String>,
    role: Option<String>,
    page: u32,
    reveal_api_keys: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client
        .admin_list_users(account_id, limit, name, role, page)
        .await?;
    let (mut response, masked) = list_users_response_for_output(response, reveal_api_keys);
    if matches!(output_format, OutputFormat::Table) {
        merge_key_prefix_into_api_key(&mut response);
    }
    if masked {
        eprintln!("{SECRET_MASK_NOTICE}");
    }
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn remove_user(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_remove_user(account_id, user_id).await?;
    let result =
        if response.is_null() || response.as_object().map(|o| o.is_empty()).unwrap_or(false) {
            json!({"account_id": account_id, "user_id": user_id})
        } else {
            response
        };
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn create_group(
    client: &HttpClient,
    account_id: &str,
    group_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client.admin_create_group(account_id, group_id).await?,
        output_format,
        compact,
    )
}

pub async fn list_groups(
    client: &HttpClient,
    account_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client.admin_list_groups(account_id).await?,
        output_format,
        compact,
    )
}

pub async fn list_group_members(
    client: &HttpClient,
    account_id: &str,
    group_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client
            .admin_list_group_members(account_id, group_id)
            .await?,
        output_format,
        compact,
    )
}

pub async fn add_group_member(
    client: &HttpClient,
    account_id: &str,
    group_id: &str,
    user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client
            .admin_add_group_member(account_id, group_id, user_id)
            .await?,
        output_format,
        compact,
    )
}

pub async fn remove_group_member(
    client: &HttpClient,
    account_id: &str,
    group_id: &str,
    user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client
            .admin_remove_group_member(account_id, group_id, user_id)
            .await?,
        output_format,
        compact,
    )
}

pub async fn delete_group(
    client: &HttpClient,
    account_id: &str,
    group_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client.admin_delete_group(account_id, group_id).await?,
        output_format,
        compact,
    )
}

fn show_admin(value: Value, output_format: OutputFormat, compact: bool) -> Result<()> {
    output_success(&value, output_format, compact);
    Ok(())
}

pub async fn set_role(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    role: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_set_role(account_id, user_id, role).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn regenerate_key(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    seed: Option<&str>,
    active_config: Option<&Config>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let request_key = client.request_api_key();
    let update = if let Some(config) = active_config.filter(|config| {
        config.api_key.as_deref() == client.api_key()
            && request_key
                .as_deref()
                .is_some_and(|key| key_belongs_to(key, account_id, user_id))
    }) {
        let update = ConfigStore::new()?.prepare_api_key_update(config, request_key.as_deref().unwrap())?;
        eprintln!(
            "Rotating your API key will update api_key in {} and any matching authentication headers.",
            update.path().display()
        );
        Some(update)
    } else {
        None
    };
    let response = client
        .admin_regenerate_key(account_id, user_id, seed)
        .await?;
    if let Some(update) = update {
        let path = update.path().display().to_string();
        let Some(new_key) = response
            .get("user_key")
            .and_then(Value::as_str)
            .filter(|key| key_belongs_to(key, account_id, user_id))
        else {
            output_success(&response, output_format, compact);
            return Err(Error::Config(format!(
                "Server reported key rotation without a valid user_key for {account_id}/{user_id}; {path} was not updated. Check the command output and recover the credential before retrying."
            )));
        };
        let saved = update.commit(new_key).map_err(|error| Error::Config(format!(
            "API key was rotated, but could not update {path}: {error}. Save the new user_key from the command output to api_key manually."
        )));
        // Persist before writing stdout (which can fail on a closed pipe), but
        // retain the returned credential in the output even if saving failed.
        output_success(&response, output_format, compact);
        saved?;
        if request_key.as_deref() == Some(new_key) {
            eprintln!(
                "Updated api_key in {path} and any matching authentication headers. The generated key is unchanged; use a new seed or omit --seed to rotate it."
            );
        } else {
            eprintln!(
                "Updated api_key in {path} and any matching authentication headers. The old key is invalid; update other clients using it."
            );
        }
    } else {
        output_success(&response, output_format, compact);
        print_admin_user_key_notice(&response, output_format, true);
    }
    Ok(())
}

fn key_belongs_to(key: &str, account_id: &str, user_id: &str) -> bool {
    let parts: Vec<_> = key.split('.').collect();
    parts.len() == 3
        && !parts[2].is_empty()
        && URL_SAFE_NO_PAD.decode(parts[0]).ok().as_deref() == Some(account_id.as_bytes())
        && URL_SAFE_NO_PAD.decode(parts[1]).ok().as_deref() == Some(user_id.as_bytes())
}

pub async fn set_account_settings(
    client: &HttpClient,
    account_id: &str,
    acl_enabled: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show_admin(
        client
            .admin_set_account_acl_enabled(account_id, acl_enabled)
            .await?,
        output_format,
        compact,
    )
}

fn print_admin_user_key_notice(
    response: &Value,
    output_format: OutputFormat,
    invalidates_old: bool,
) {
    let lines = admin_user_key_notice_lines(response, output_format, invalidates_old);
    if lines.is_empty() {
        return;
    }

    println!();
    for line in lines {
        println!("{line}");
    }
}

fn admin_user_key_notice_lines(
    response: &Value,
    output_format: OutputFormat,
    invalidates_old: bool,
) -> Vec<String> {
    if !matches!(output_format, OutputFormat::Table) || !contains_non_empty_user_key(response) {
        return Vec::new();
    }

    let mut lines = vec![
        format!(
            "{} {}",
            theme::warning("New user key generated.").bold(),
            theme::body("Copy and store it securely now.")
        ),
        format!(
            "{} {}{}",
            theme::body("This key may not be shown again. If lost, run"),
            theme::command("ov admin regenerate-key"),
            theme::body(".")
        ),
        format!(
            "{} {} {} {} {} {}{}",
            theme::body("To use this key for normal commands, run"),
            theme::command("ov config"),
            theme::body("->"),
            theme::heading("Edit config"),
            theme::body("->"),
            theme::heading("Set normal user API key"),
            theme::body(".")
        ),
    ];

    if invalidates_old {
        lines.push(
            theme::warning("The old user key is invalidated immediately.")
                .bold()
                .to_string(),
        );
    }

    lines
}

fn contains_non_empty_user_key(value: &Value) -> bool {
    match value {
        Value::Object(object) => {
            object
                .get("user_key")
                .and_then(Value::as_str)
                .is_some_and(|key| !key.trim().is_empty())
                || object.values().any(contains_non_empty_user_key)
        }
        Value::Array(items) => items.iter().any(contains_non_empty_user_key),
        _ => false,
    }
}

fn list_users_response_for_output(mut response: Value, reveal_api_keys: bool) -> (Value, bool) {
    if reveal_api_keys {
        return (response, false);
    }
    let masked = mask_plaintext_api_keys(&mut response);
    (response, masked)
}

/// Collapse the server's `key_prefix`/`api_key` fields into a single `api_key`
/// table column; the JSON response shape is left untouched.
fn merge_key_prefix_into_api_key(value: &mut Value) {
    fn normalize_row(row: &mut Value) {
        let Some(object) = row.as_object_mut() else {
            return;
        };
        if !object.contains_key("api_key") {
            if let Some(prefix) = object.remove("key_prefix") {
                object.insert("api_key".to_string(), prefix);
            }
        } else {
            object.remove("key_prefix");
        }
    }
    match value {
        Value::Array(users) => users.iter_mut().for_each(normalize_row),
        Value::Object(object) => {
            if let Some(users) = object.get_mut("users").and_then(Value::as_array_mut) {
                users.iter_mut().for_each(normalize_row);
            }
        }
        _ => {}
    }
}

fn mask_plaintext_api_keys(value: &mut Value) -> bool {
    match value {
        Value::Object(object) => {
            let mut masked = false;
            for (key, child) in object {
                if key == "api_key" {
                    if let Some(api_key) = child.as_str() {
                        if !api_key.trim().is_empty() {
                            *child = Value::String(mask_api_key(api_key));
                            masked = true;
                        }
                    }
                } else if mask_plaintext_api_keys(child) {
                    masked = true;
                }
            }
            masked
        }
        Value::Array(items) => {
            let mut masked = false;
            for item in items {
                if mask_plaintext_api_keys(item) {
                    masked = true;
                }
            }
            masked
        }
        _ => false,
    }
}

/// Match `openviking/server/api_keys/key_mgr.py:mask_api_key` so CLI and API output look the same.
fn mask_api_key(api_key: &str) -> String {
    let parts: Vec<&str> = api_key.split('.').collect();
    if api_key.is_empty() || parts.len() != 3 {
        return "*".repeat(api_key.chars().count());
    }

    let secret_chars: Vec<char> = parts[2].chars().collect();
    let visible_length = if secret_chars.len() > 4 { 4 } else { 0 };
    let hidden_length = secret_chars.len() - visible_length;

    let mut masked = format!("{}.{}.", parts[0], parts[1]);
    masked.push_str(&"*".repeat(hidden_length));
    masked.push_str(&secret_chars[hidden_length..].iter().collect::<String>());
    masked
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

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
    fn user_key_notice_appears_for_table_key_generation_response() {
        let response = json!({
            "account_id": "acme",
            "admin_user_id": "alice",
            "user_key": "user-secret"
        });

        let lines = admin_user_key_notice_lines(&response, OutputFormat::Table, false);
        let rendered = strip_ansi(&lines.join("\n"));

        assert!(rendered.contains("New user key generated. Copy and store it securely now."));
        assert!(
            rendered
                .contains("This key may not be shown again. If lost, run ov admin regenerate-key.")
        );
        assert!(rendered.contains("To use this key for normal commands, run ov config -> Edit config -> Set normal user API key."));
        assert!(!rendered.contains("old user key is invalidated"));
    }

    #[test]
    fn user_key_notice_detects_nested_result_response() {
        let response = json!({
            "status": "ok",
            "result": {
                "account_id": "acme",
                "user_key": "user-secret"
            }
        });

        assert!(!admin_user_key_notice_lines(&response, OutputFormat::Table, false).is_empty());
    }

    #[test]
    fn user_key_notice_is_hidden_without_non_empty_user_key() {
        let no_key = json!({"account_id": "acme", "admin_user_id": "alice"});
        let empty_key = json!({"account_id": "acme", "user_key": "   "});

        assert!(admin_user_key_notice_lines(&no_key, OutputFormat::Table, false).is_empty());
        assert!(admin_user_key_notice_lines(&empty_key, OutputFormat::Table, false).is_empty());
    }

    #[test]
    fn user_key_notice_is_hidden_for_json_output() {
        let response = json!({"account_id": "acme", "user_key": "user-secret"});

        assert!(admin_user_key_notice_lines(&response, OutputFormat::Json, false).is_empty());
    }

    #[test]
    fn regenerate_key_notice_mentions_old_key_invalidation() {
        let response = json!({"user_key": "new-user-secret"});

        let rendered = admin_user_key_notice_lines(&response, OutputFormat::Table, true).join("\n");

        assert!(rendered.contains("The old user key is invalidated immediately."));
    }

    #[test]
    fn mask_api_key_matches_server_format() {
        let cases = [
            (
                "YWNtZQ.YWxpY2U.abcdefghijklmnop",
                "YWNtZQ.YWxpY2U.************mnop",
            ),
            ("YWNtZQ.YWxpY2U.abcdefgh", "YWNtZQ.YWxpY2U.****efgh"),
            ("YWNtZQ.YWxpY2U.abcde", "YWNtZQ.YWxpY2U.*bcde"),
            ("YWNtZQ.YWxpY2U.abcd", "YWNtZQ.YWxpY2U.****"),
            ("YWNtZQ.YWxpY2U.a", "YWNtZQ.YWxpY2U.*"),
            ("legacy-secret", "*************"),
            ("two.parts", "*********"),
            ("abcd", "****"),
            ("a", "*"),
        ];

        for (key, expected) in cases {
            assert_eq!(mask_api_key(key), expected, "masking {key}");
        }
    }

    #[test]
    fn list_users_table_output_masks_plaintext_api_keys() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.abcdefgh"},
            {"user_id": "bob", "role": "user", "key_prefix": "prefix123"}
        ]);

        let (sanitized, masked) = list_users_response_for_output(response, false);

        assert!(masked);
        assert_eq!(
            sanitized,
            json!([
                {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.****efgh"},
                {"user_id": "bob", "role": "user", "key_prefix": "prefix123"}
            ])
        );
    }

    #[test]
    fn list_users_json_output_uses_the_same_masking() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.abcdefgh"}
        ]);

        let (sanitized, masked) = list_users_response_for_output(response, false);

        assert!(masked);
        assert_eq!(
            sanitized,
            json!([
                {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.****efgh"}
            ])
        );
    }

    #[test]
    fn list_users_reveal_flag_preserves_plaintext_api_keys() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.abcdefgh"}
        ]);

        let (sanitized, masked) = list_users_response_for_output(response.clone(), true);

        assert!(!masked);
        assert_eq!(sanitized, response);
    }

    #[test]
    fn list_users_admin_prefixes_are_not_remasked() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "key_prefix": "YWNtZQ.YWxpY2U.****efgh"}
        ]);

        let (sanitized, masked) = list_users_response_for_output(response.clone(), false);

        assert!(!masked);
        assert_eq!(sanitized, response);
    }

    #[test]
    fn list_users_table_merges_key_prefix_into_api_key_column() {
        let mut response = json!([
            {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.plaintext-secret"},
            {"user_id": "bob", "role": "user", "key_prefix": "YWNtZQ.Ym9i.****wxyz"},
            {"user_id": "carol", "role": "user"}
        ]);

        merge_key_prefix_into_api_key(&mut response);

        assert_eq!(
            response,
            json!([
                {"user_id": "alice", "role": "admin", "api_key": "YWNtZQ.YWxpY2U.plaintext-secret"},
                {"user_id": "bob", "role": "user", "api_key": "YWNtZQ.Ym9i.****wxyz"},
                {"user_id": "carol", "role": "user"}
            ])
        );
    }

    #[test]
    fn list_users_table_merges_key_prefix_inside_summary_object() {
        let mut response = json!({
            "total": 1,
            "users": [
                {"user_id": "bob", "role": "user", "key_prefix": "YWNtZQ.Ym9i.****wxyz"}
            ]
        });

        merge_key_prefix_into_api_key(&mut response);

        assert_eq!(
            response,
            json!({
                "total": 1,
                "users": [
                    {"user_id": "bob", "role": "user", "api_key": "YWNtZQ.Ym9i.****wxyz"}
                ]
            })
        );
    }

    #[test]
    fn list_users_masking_preserves_empty_api_keys() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": ""},
            {"user_id": "bob", "role": "user", "api_key": "   "}
        ]);

        let (sanitized, masked) = list_users_response_for_output(response.clone(), false);

        assert!(!masked);
        assert_eq!(sanitized, response);
    }
}
