// Watch management subcommand handlers (RFC #2104).
//
// Mirrors the REST `/watches` endpoints with full parity. Each handler
// auto-detects whether `key` is a viking:// URI or a task_id and routes to
// the appropriate `*_by_uri` or `*_by_id` HTTP client method.

use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde_json::json;

/// How a positional `<key>` arg should be routed.
#[derive(Debug, PartialEq, Eq)]
enum KeyKind {
    /// Routes to `*_by_uri` HTTP method.
    Uri,
    /// Routes to `*_by_id` HTTP method (treated as opaque task_id).
    TaskId,
}

/// Classify a positional key. Case-insensitive `viking://` matches as a URI;
/// any other `://`-bearing string is rejected as a likely typo (avoids the
/// silent "task_id not found" experience when the user meant a URI but used
/// the wrong scheme / capitalization / single-slash).
fn classify_key(key: &str) -> Result<KeyKind> {
    if key.starts_with("viking://") {
        return Ok(KeyKind::Uri);
    }
    if key.to_ascii_lowercase().starts_with("viking://") {
        return Err(Error::Parse(format!(
            "URI scheme is case-sensitive — use lowercase `viking://` (got {key:?})"
        )));
    }
    if key.contains("://") {
        return Err(Error::Parse(format!(
            "Key {key:?} looks like a URI but does not start with `viking://`. \
             If you meant a task_id, drop the scheme."
        )));
    }
    Ok(KeyKind::TaskId)
}

/// Validate `ov watch set-interval` minutes. Rejects 0, negatives, and NaN.
fn validate_interval_minutes(minutes: f64) -> Result<()> {
    if !(minutes > 0.0) {
        return Err(Error::Parse(format!(
            "minutes must be > 0 (got {minutes}). To pause a watch task, use `ov watch pause`."
        )));
    }
    Ok(())
}

pub async fn ls(
    client: &HttpClient,
    active_only: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.list_watches(active_only).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn show(
    client: &HttpClient,
    key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = match classify_key(key)? {
        KeyKind::Uri => client.get_watch_by_uri(key).await?,
        KeyKind::TaskId => client.get_watch_by_id(key).await?,
    };
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn rm(
    client: &HttpClient,
    key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = match classify_key(key)? {
        KeyKind::Uri => client.delete_watch_by_uri(key).await?,
        KeyKind::TaskId => client.delete_watch_by_id(key).await?,
    };
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn pause(
    client: &HttpClient,
    key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let body = json!({"is_active": false});
    let response = match classify_key(key)? {
        KeyKind::Uri => client.patch_watch_by_uri(key, &body).await?,
        KeyKind::TaskId => client.patch_watch_by_id(key, &body).await?,
    };
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn resume(
    client: &HttpClient,
    key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let body = json!({"is_active": true});
    let response = match classify_key(key)? {
        KeyKind::Uri => client.patch_watch_by_uri(key, &body).await?,
        KeyKind::TaskId => client.patch_watch_by_id(key, &body).await?,
    };
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn set_interval(
    client: &HttpClient,
    key: &str,
    minutes: f64,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    validate_interval_minutes(minutes)?;
    let body = json!({"watch_interval": minutes});
    let response = match classify_key(key)? {
        KeyKind::Uri => client.patch_watch_by_uri(key, &body).await?,
        KeyKind::TaskId => client.patch_watch_by_id(key, &body).await?,
    };
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn trigger(
    client: &HttpClient,
    key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = match classify_key(key)? {
        KeyKind::Uri => client.trigger_watch_by_uri(key).await?,
        KeyKind::TaskId => client.trigger_watch_by_id(key).await?,
    };
    output_success(&response, output_format, compact);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{KeyKind, classify_key, validate_interval_minutes};
    use crate::error::Error;

    #[test]
    fn classify_viking_uri_as_uri() {
        assert_eq!(
            classify_key("viking://resources/foo/bar").unwrap(),
            KeyKind::Uri
        );
        assert_eq!(classify_key("viking://resources").unwrap(), KeyKind::Uri);
    }

    #[test]
    fn classify_plain_id_as_task_id() {
        assert_eq!(
            classify_key("550e8400-e29b-41d4-a716-446655440000").unwrap(),
            KeyKind::TaskId
        );
        assert_eq!(classify_key("abc-123").unwrap(), KeyKind::TaskId);
        assert_eq!(classify_key("").unwrap(), KeyKind::TaskId);
    }

    #[test]
    fn classify_rejects_uppercase_viking_scheme() {
        let err = classify_key("Viking://resources/foo").expect_err("should reject");
        match err {
            Error::Parse(msg) => assert!(msg.contains("case-sensitive"), "got: {msg}"),
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn classify_rejects_other_schemes() {
        for bad in ["http://example.com", "https://example.com", "file:///etc"] {
            let err = classify_key(bad).expect_err("should reject");
            match err {
                Error::Parse(msg) => assert!(msg.contains("looks like a URI"), "got: {msg}"),
                other => panic!("expected Parse error, got {other:?}"),
            }
        }
    }

    #[test]
    fn validate_interval_accepts_positive() {
        for ok in [0.0001_f64, 1.0, 60.0, 1440.0, 1e9] {
            assert!(validate_interval_minutes(ok).is_ok(), "should accept: {ok}");
        }
    }

    #[test]
    fn validate_interval_rejects_zero_negative_nan() {
        for bad in [0.0_f64, -1.0, -42.5, f64::NAN] {
            let err = validate_interval_minutes(bad).expect_err("should reject");
            match err {
                Error::Parse(msg) => assert!(msg.contains("minutes must be > 0"), "got: {msg}"),
                other => panic!("expected Parse error, got {other:?}"),
            }
        }
    }
}
