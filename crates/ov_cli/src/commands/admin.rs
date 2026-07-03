use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use colored::Colorize;
use serde_json::{Value, json};

const SECRET_PLACEHOLDER: &str = "********";

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
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_list_accounts().await?;
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

pub async fn migrate(
    client: &HttpClient,
    cleanup: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_migrate(cleanup).await?;
    output_success(&response, output_format, compact);
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
    limit: u32,
    name: Option<String>,
    role: Option<String>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client
        .admin_list_users(account_id, limit, name, role)
        .await?;
    let response = list_users_response_for_output(response, output_format);
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
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client
        .admin_regenerate_key(account_id, user_id, seed)
        .await?;
    output_success(&response, output_format, compact);
    print_admin_user_key_notice(&response, output_format, true);
    Ok(())
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

fn list_users_response_for_output(mut response: Value, output_format: OutputFormat) -> Value {
    if matches!(output_format, OutputFormat::Table) {
        redact_plaintext_api_keys(&mut response);
    }
    response
}

fn redact_plaintext_api_keys(value: &mut Value) {
    match value {
        Value::Object(object) => {
            for (key, child) in object {
                if key == "api_key"
                    && child
                        .as_str()
                        .is_some_and(|api_key| !api_key.trim().is_empty())
                {
                    *child = Value::String(SECRET_PLACEHOLDER.to_string());
                } else {
                    redact_plaintext_api_keys(child);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                redact_plaintext_api_keys(item);
            }
        }
        _ => {}
    }
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
    fn list_users_table_output_redacts_plaintext_api_keys() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": "plain-secret"},
            {"user_id": "bob", "role": "user", "key_prefix": "prefix123"}
        ]);

        let sanitized = list_users_response_for_output(response, OutputFormat::Table);

        assert_eq!(
            sanitized,
            json!([
                {"user_id": "alice", "role": "admin", "api_key": "********"},
                {"user_id": "bob", "role": "user", "key_prefix": "prefix123"}
            ])
        );
    }

    #[test]
    fn list_users_table_output_preserves_empty_api_keys() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": ""},
            {"user_id": "bob", "role": "user", "api_key": "   "}
        ]);

        let sanitized = list_users_response_for_output(response.clone(), OutputFormat::Table);

        assert_eq!(sanitized, response);
    }

    #[test]
    fn list_users_json_output_preserves_plaintext_api_keys() {
        let response = json!([
            {"user_id": "alice", "role": "admin", "api_key": "plain-secret"}
        ]);

        let sanitized = list_users_response_for_output(response.clone(), OutputFormat::Json);

        assert_eq!(sanitized, response);
    }
}
