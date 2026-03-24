use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};
use serde_json::{Value, json};

fn normalize_source_filter(source: &str) -> String {
    match source.trim().to_lowercase().as_str() {
        "session" | "sessions" => "sessions".to_string(),
        "skill" | "skills" => "skill".to_string(),
        "memory" | "memories" => "memory".to_string(),
        "resource" | "resources" => "resource".to_string(),
        other => other.replace('-', "_").replace(' ', "_"),
    }
}

fn source_root_uri(source: &str) -> Option<String> {
    match normalize_source_filter(source).as_str() {
        "agent" => Some("viking://resources/sources/agent".to_string()),
        "calendar" => Some("viking://resources/sources/calendar".to_string()),
        "contacts" => Some("viking://resources/sources/contacts".to_string()),
        "desktop" => Some("viking://resources/sources/desktop".to_string()),
        "documents" => Some("viking://resources/sources/documents".to_string()),
        "email" => Some("viking://resources/sources/email".to_string()),
        "gist" => Some("viking://resources/sources/gist".to_string()),
        "imessages" => Some("viking://resources/sources/imessages".to_string()),
        "notion" => Some("viking://resources/sources/notion".to_string()),
        "slack" => Some("viking://resources/sources/slack".to_string()),
        "taildrive" => Some("viking://resources/sources/taildrive".to_string()),
        "telegram" => Some("viking://resources/sources/telegram".to_string()),
        "skill" => Some("viking://agent/skills".to_string()),
        "memory" => Some("viking://user/memories".to_string()),
        "resource" => Some("viking://resources".to_string()),
        _ => None,
    }
}

fn source_filter(source: Option<&str>) -> Option<Value> {
    source.map(|value| {
        json!({
            "op": "must",
            "field": "source",
            "conds": [normalize_source_filter(value)],
        })
    })
}

pub async fn find(
    client: &HttpClient,
    query: &str,
    uri: &str,
    node_limit: i32,
    threshold: Option<f64>,
    source: Option<&str>,
    after: Option<&str>,
    before: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let effective_uri = if uri.is_empty() {
        source
            .and_then(source_root_uri)
            .unwrap_or_else(|| uri.to_string())
    } else {
        uri.to_string()
    };
    let result = client
        .find(
            query.to_string(),
            effective_uri,
            node_limit,
            threshold,
            source_filter(source),
            after.map(|s| s.to_string()),
            before.map(|s| s.to_string()),
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn search(
    client: &HttpClient,
    query: &str,
    uri: &str,
    session_id: Option<String>,
    node_limit: i32,
    threshold: Option<f64>,
    source: Option<&str>,
    after: Option<&str>,
    before: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let effective_uri = if uri.is_empty() {
        source
            .and_then(source_root_uri)
            .unwrap_or_else(|| uri.to_string())
    } else {
        uri.to_string()
    };
    let result = client
        .search(
            query.to_string(),
            effective_uri,
            session_id,
            node_limit,
            threshold,
            source_filter(source),
            after.map(|s| s.to_string()),
            before.map(|s| s.to_string()),
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{normalize_source_filter, source_filter, source_root_uri};

    #[test]
    fn source_filter_builds_canonical_metadata_filter() {
        let payload = source_filter(Some("Documents")).expect("expected source filter");
        assert_eq!(payload["op"], "must");
        assert_eq!(payload["field"], "source");
        assert_eq!(payload["conds"][0], "documents");
    }

    #[test]
    fn sessions_source_does_not_force_wrong_resource_root() {
        assert_eq!(normalize_source_filter("session"), "sessions");
        assert!(source_root_uri("sessions").is_none());
    }
}

pub async fn grep(
    client: &HttpClient,
    uri: &str,
    exclude_uri: Option<String>,
    pattern: &str,
    ignore_case: bool,
    node_limit: i32,
    level_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .grep(
            uri,
            exclude_uri,
            pattern,
            ignore_case,
            node_limit,
            level_limit,
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn glob(
    client: &HttpClient,
    pattern: &str,
    uri: &str,
    node_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.glob(pattern, uri, node_limit).await?;
    output_success(&result, output_format, compact);
    Ok(())
}
