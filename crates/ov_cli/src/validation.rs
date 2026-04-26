//! Validates that --user / --account CLI flags refer to a real user.
//!
//! See https://github.com/volcengine/OpenViking/issues/1697.

use crate::client::HttpClient;
use crate::error::{Error, Result};
use serde_json::Value;

/// Confirm `(account_id, user_id)` exists on the server. Errors with a
/// human-readable message when either is missing.
///
/// Cheap on the happy path: a single GET against
/// `/api/v1/admin/accounts/{account_id}/users` returns the full member list,
/// then we filter locally. Server-side the endpoint already enforces
/// per-account auth; we report 404/403 as a clear "account not found" rather
/// than the bare HTTP code.
pub async fn validate_user_account(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
) -> Result<()> {
    let body = match client.admin_list_users(account_id, 200, None, None).await {
        Ok(b) => b,
        Err(err) => {
            let msg = err.to_string();
            if msg.contains("404") || msg.to_lowercase().contains("not found") {
                return Err(Error::Client(format!(
                    "Account `{account_id}` not found. Run `ov admin list-accounts` to see valid accounts."
                )));
            }
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
    body.get("users")
        .and_then(Value::as_array)
        .map(|users| {
            users.iter().any(|u| {
                u.get("user_id").and_then(Value::as_str) == Some(user_id)
                    || u.get("id").and_then(Value::as_str) == Some(user_id)
            })
        })
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn finds_user_with_user_id_field() {
        let body = json!({"users": [{"user_id": "alice"}, {"user_id": "bob"}]});
        assert!(user_exists(&body, "alice"));
    }

    #[test]
    fn finds_user_with_id_field() {
        let body = json!({"users": [{"id": "carol"}]});
        assert!(user_exists(&body, "carol"));
    }

    #[test]
    fn missing_user_returns_false() {
        let body = json!({"users": [{"user_id": "alice"}]});
        assert!(!user_exists(&body, "bob"));
    }

    #[test]
    fn empty_users_array_returns_false() {
        let body = json!({"users": []});
        assert!(!user_exists(&body, "alice"));
    }
}
