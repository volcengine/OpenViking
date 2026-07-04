//! Validates that --user / --account CLI flags refer to a real user.
//!
//! See https://github.com/volcengine/OpenViking/issues/1697.

use crate::client::HttpClient;
use crate::error::{Error, Result};
use serde_json::Value;

/// Confirm `(account_id, user_id)` exists on the server. Errors with a
/// human-readable message when either is missing.
///
/// Cheap on the happy path: a single filtered GET against
/// `/api/v1/admin/accounts/{account_id}/users` asks the server for the target
/// user before applying the endpoint's limit. Server-side the endpoint already
/// enforces per-account auth; we report 404 as a clear "account not found"
/// rather than the bare HTTP code.
pub async fn validate_user_account(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
) -> Result<()> {
    let body = match client
        .admin_list_users(account_id, 1, Some(user_id.to_string()), None)
        .await
    {
        Ok(b) => b,
        Err(err) => {
            if is_not_found_error(&err) {
                return Err(Error::Client(format!(
                    "Account `{account_id}` not found. Run `ov admin list-accounts` to see valid accounts."
                )));
            }
            if is_permission_denied_error(&err) {
                return Ok(());
            }
            let msg = err.to_string();
            return Err(Error::Client(format!(
                "Could not validate `--user {user_id} --account {account_id}`: {msg}"
            )));
        }
    };

    if !user_exists(&body, user_id) {
        return Err(Error::Client(format!(
            "User `{user_id}` not found in account `{account_id}`. Run `ov admin list-users --account {account_id}` to see valid users."
        )));
    }

    Ok(())
}

fn user_exists(body: &Value, user_id: &str) -> bool {
    user_list(body)
        .map(|users| {
            users.iter().any(|u| {
                u.get("user_id").and_then(Value::as_str) == Some(user_id)
                    || u.get("id").and_then(Value::as_str) == Some(user_id)
            })
        })
        .unwrap_or(false)
}

fn user_list(body: &Value) -> Option<&[Value]> {
    body.as_array().map(Vec::as_slice).or_else(|| {
        body.get("users")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
    })
}

fn is_not_found_error(err: &Error) -> bool {
    let msg = err.to_string();
    msg.contains("404") || msg.to_lowercase().contains("not found")
}

fn is_permission_denied_error(err: &Error) -> bool {
    let msg = err.to_string().to_lowercase();
    msg.contains("401")
        || msg.contains("403")
        || msg.contains("permission_denied")
        || msg.contains("unauthenticated")
        || msg.contains("permission denied")
        || msg.contains("requires role")
        || msg.contains("authentication required")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn finds_user_with_user_id_field() {
        let body = json!([{"user_id": "alice"}, {"user_id": "bob"}]);
        assert!(user_exists(&body, "alice"));
    }

    #[test]
    fn finds_user_with_id_field() {
        let body = json!([{"id": "carol"}]);
        assert!(user_exists(&body, "carol"));
    }

    #[test]
    fn missing_user_returns_false() {
        let body = json!([{"user_id": "alice"}]);
        assert!(!user_exists(&body, "bob"));
    }

    #[test]
    fn empty_users_array_returns_false() {
        let body = json!([]);
        assert!(!user_exists(&body, "alice"));
    }

    #[test]
    fn supports_legacy_users_field() {
        let body = json!({"users": [{"user_id": "alice"}]});
        assert!(user_exists(&body, "alice"));
    }

    #[test]
    fn permission_denied_validation_errors_are_skipped() {
        let err = Error::Api("[PERMISSION_DENIED] Requires role: root, admin".to_string());
        assert!(is_permission_denied_error(&err));
    }

    #[test]
    fn unauthenticated_validation_errors_are_skipped() {
        let err = Error::Api("[UNAUTHENTICATED] Missing API Key".to_string());
        assert!(is_permission_denied_error(&err));
    }

    #[test]
    fn not_found_validation_errors_are_not_skipped() {
        let err = Error::Api("[NOT_FOUND] Account not found: acme".to_string());
        assert!(is_not_found_error(&err));
        assert!(!is_permission_denied_error(&err));
    }
}
