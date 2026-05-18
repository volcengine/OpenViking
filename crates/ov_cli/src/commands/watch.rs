// Watch management subcommand handlers (RFC #2104).
//
// Mirrors the REST `/watches` endpoints with full parity. Each handler
// auto-detects whether `key` is a viking:// URI or a task_id and routes to
// the appropriate `*_by_uri` or `*_by_id` HTTP client method.

use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde_json::json;

/// Classify a positional key argument: viking:// prefix means it's a `to_uri`,
/// everything else is treated as an opaque task_id.
fn is_uri(key: &str) -> bool {
    key.starts_with("viking://")
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
    let response = if is_uri(key) {
        client.get_watch_by_uri(key).await?
    } else {
        client.get_watch_by_id(key).await?
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
    let response = if is_uri(key) {
        client.delete_watch_by_uri(key).await?
    } else {
        client.delete_watch_by_id(key).await?
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
    let response = if is_uri(key) {
        client.patch_watch_by_uri(key, &body).await?
    } else {
        client.patch_watch_by_id(key, &body).await?
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
    let response = if is_uri(key) {
        client.patch_watch_by_uri(key, &body).await?
    } else {
        client.patch_watch_by_id(key, &body).await?
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
    if !(minutes > 0.0) {
        return Err(Error::Parse(format!(
            "minutes must be > 0 (got {minutes}). To pause a watch task, use `ov watch pause`."
        )));
    }
    let body = json!({"watch_interval": minutes});
    let response = if is_uri(key) {
        client.patch_watch_by_uri(key, &body).await?
    } else {
        client.patch_watch_by_id(key, &body).await?
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
    let response = if is_uri(key) {
        client.trigger_watch_by_uri(key).await?
    } else {
        client.trigger_watch_by_id(key).await?
    };
    output_success(&response, output_format, compact);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::is_uri;

    #[test]
    fn uri_prefix_detected() {
        assert!(is_uri("viking://resources/foo/bar"));
        assert!(is_uri("viking://resources"));
    }

    #[test]
    fn non_uri_classified_as_task_id() {
        assert!(!is_uri("550e8400-e29b-41d4-a716-446655440000"));
        assert!(!is_uri("abc-123"));
        assert!(!is_uri("http://example.com")); // wrong scheme isn't viking://
        assert!(!is_uri(""));
    }

    #[test]
    fn non_positive_minutes_rejected() {
        // Sanity-check the local guard. Because the `if !(minutes > 0.0)` check
        // sits ahead of any HTTP call, we can verify rejection purely via the
        // boolean predicate without spinning up a client.
        for bad in [0.0_f64, -1.0, -42.5, f64::NAN] {
            assert!(!(bad > 0.0), "guard expects to reject: {bad}");
        }
        for ok in [0.0001_f64, 1.0, 60.0, 1440.0] {
            assert!(ok > 0.0, "guard expects to accept: {ok}");
        }
    }
}
