use super::render_utils::append_profile_lines;
use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use colored::Colorize;
use serde_json::Value;
use serde_json::json;

pub async fn new_session(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.post("/api/v1/sessions", &json!({})).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn list_sessions(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.get("/api/v1/sessions", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn get_session(
    client: &HttpClient,
    session_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}", url_encode(session_id));
    let response: serde_json::Value = client.get(&path, &[]).await?;
    output_session_get(&response, output_format, compact);
    Ok(())
}

fn output_session_get(response: &Value, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Table)
        && let Some(rendered) = render_session_get_for_table(response)
    {
        println!("{rendered}");
    } else {
        output_success(response, output_format, compact);
    }
}

fn render_session_get_for_table(value: &Value) -> Option<String> {
    let object = value.as_object()?;
    let session_id = object.get("session_id").and_then(Value::as_str)?;
    let mut lines = Vec::new();

    lines.push(theme::heading("Session").bold().to_string());
    push_row(&mut lines, "id", session_id);
    push_optional_row(&mut lines, "created", object.get("created_at"));
    push_optional_row(&mut lines, "updated", object.get("updated_at"));
    push_optional_row(&mut lines, "last commit", object.get("last_commit_at"));

    lines.push(String::new());
    lines.push(theme::heading("Identity").bold().to_string());
    push_optional_row(&mut lines, "created by", object.get("created_by_user_id"));
    if let Some(users) = object.get("participant_user_ids") {
        push_row(&mut lines, "participants", &format_list(users));
    }
    if let Some(user) = object.get("user").and_then(Value::as_object) {
        push_row(&mut lines, "active user", &format_identity(user));
    }

    lines.push(String::new());
    lines.push(theme::heading("Activity").bold().to_string());
    push_optional_row(&mut lines, "messages", object.get("message_count"));
    push_optional_row(
        &mut lines,
        "total messages",
        object.get("total_message_count"),
    );
    push_optional_row(&mut lines, "commits", object.get("commit_count"));
    push_optional_row(&mut lines, "pending tokens", object.get("pending_tokens"));
    push_optional_row(&mut lines, "keep recent", object.get("keep_recent_count"));

    if let Some(memories) = object.get("memories_extracted").and_then(Value::as_object) {
        lines.push(String::new());
        lines.push(theme::heading("Memory").bold().to_string());
        push_row(
            &mut lines,
            "memories extracted",
            &format_ordered_counts(
                memories,
                &[
                    "profile",
                    "preferences",
                    "entities",
                    "events",
                    "cases",
                    "patterns",
                    "tools",
                    "skills",
                    "total",
                ],
            ),
        );
    }

    lines.push(String::new());
    lines.push(theme::heading("Tokens").bold().to_string());
    if let Some(llm) = object.get("llm_token_usage").and_then(Value::as_object) {
        push_row(
            &mut lines,
            "llm",
            &format_ordered_counts(llm, &["prompt_tokens", "completion_tokens", "total_tokens"]),
        );
    }
    if let Some(embedding) = object
        .get("embedding_token_usage")
        .and_then(Value::as_object)
    {
        push_row(
            &mut lines,
            "embedding",
            &format_ordered_counts(embedding, &["total_tokens"]),
        );
    }

    append_profile_lines(
        object.get("profile").filter(|profile| !profile.is_null()),
        &mut lines,
    );
    Some(lines.join("\n"))
}

fn push_optional_row(lines: &mut Vec<String>, label: &str, value: Option<&Value>) {
    let Some(value) = value else {
        return;
    };
    let formatted = format_json_value(value);
    if formatted.is_empty() || formatted == "null" {
        return;
    }
    push_row(lines, label, &formatted);
}

fn push_row(lines: &mut Vec<String>, label: &str, value: &str) {
    lines.push(format!(
        "  {}  {}",
        theme::muted(pad_label(label, 18)),
        theme::body(value)
    ));
}

fn pad_label(label: &str, width: usize) -> String {
    if label.len() >= width {
        label.to_string()
    } else {
        format!("{}{}", label, " ".repeat(width - label.len()))
    }
}

fn format_list(value: &Value) -> String {
    if let Some(items) = value.as_array() {
        return items
            .iter()
            .map(format_json_value)
            .filter(|value| !value.is_empty() && value != "null")
            .collect::<Vec<_>>()
            .join(", ");
    }
    format_json_value(value)
}

fn format_identity(object: &serde_json::Map<String, Value>) -> String {
    ["account_id", "user_id"]
        .into_iter()
        .filter_map(|key| object.get(key).map(|value| (key, format_json_value(value))))
        .filter(|(_, value)| !value.is_empty() && value != "null")
        .map(|(key, value)| format!("{key} {value}"))
        .collect::<Vec<_>>()
        .join(", ")
}

fn format_ordered_counts(object: &serde_json::Map<String, Value>, order: &[&str]) -> String {
    let mut parts = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for key in order {
        if let Some(value) = object.get(*key) {
            seen.insert(*key);
            parts.push(format!("{key} {}", format_json_value(value)));
        }
    }
    for (key, value) in object {
        if !seen.contains(key.as_str()) {
            parts.push(format!("{key} {}", format_json_value(value)));
        }
    }
    parts.join(", ")
}

fn format_json_value(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Bool(value) => value.to_string(),
        Value::Null => "null".to_string(),
        other => other.to_string(),
    }
}

pub async fn get_session_context(
    client: &HttpClient,
    session_id: &str,
    token_budget: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}/context", url_encode(session_id));
    let response: serde_json::Value = client
        .get(
            &path,
            &[("token_budget".to_string(), token_budget.to_string())],
        )
        .await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn get_session_archive(
    client: &HttpClient,
    session_id: &str,
    archive_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!(
        "/api/v1/sessions/{}/archives/{}",
        url_encode(session_id),
        url_encode(archive_id)
    );
    let response: serde_json::Value = client.get(&path, &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn delete_session(
    client: &HttpClient,
    session_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}", url_encode(session_id));
    let response: serde_json::Value = client.delete(&path, &[]).await?;

    // Return session_id in result if empty (similar to Python implementation)
    let result =
        if response.is_null() || response.as_object().map(|o| o.is_empty()).unwrap_or(false) {
            json!({"session_id": session_id})
        } else {
            response
        };

    output_success(&result, output_format, compact);
    Ok(())
}

fn parse_messages(input: &str) -> Result<Vec<(String, String)>> {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(arr) = value.as_array() {
            let messages: std::result::Result<Vec<(String, String)>, _> = arr
                .iter()
                .enumerate()
                .map(|(i, item)| {
                    let role = item["role"].as_str().ok_or_else(|| {
                        Error::Client(format!(
                            "messages[{}]: 'role' must be a string, got {:?}",
                            i, item["role"]
                        ))
                    })?;
                    let content = item["content"].as_str().ok_or_else(|| {
                        Error::Client(format!(
                            "messages[{}]: 'content' must be a string, got {:?}",
                            i, item["content"]
                        ))
                    })?;
                    Ok((role.to_string(), content.to_string()))
                })
                .collect();
            return messages;
        } else if value.get("role").is_some() || value.get("content").is_some() {
            let role = value["role"].as_str().ok_or_else(|| {
                Error::Client(format!("'role' must be a string, got {:?}", value["role"]))
            })?;
            let content = value["content"].as_str().ok_or_else(|| {
                Error::Client(format!(
                    "'content' must be a string, got {:?}",
                    value["content"]
                ))
            })?;
            return Ok(vec![(role.to_string(), content.to_string())]);
        }
    }
    Ok(vec![("user".to_string(), input.to_string())])
}

pub async fn add_message(
    client: &HttpClient,
    session_id: &str,
    role: &str,
    content: &str,
    peer_id: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}/messages", url_encode(session_id));
    let mut body = json!({
        "role": role,
        "content": content
    });
    if let Some(peer_id) = peer_id {
        body["peer_id"] = json!(peer_id);
    }

    let response: serde_json::Value = client.post(&path, &body).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn add_messages(
    client: &HttpClient,
    session_id: &str,
    input: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let messages = parse_messages(input)?;
    let path = format!("/api/v1/sessions/{}/messages/batch", url_encode(session_id));
    let messages_json: Vec<serde_json::Value> = messages
        .iter()
        .map(|(role, content)| json!({"role": role, "content": content}))
        .collect();
    let body = json!({"messages": messages_json});
    let response: serde_json::Value = client.post(&path, &body).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn commit_session(
    client: &HttpClient,
    session_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}/commit", url_encode(session_id));
    let response: serde_json::Value = client.post(&path, &json!({})).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

/// Add memory in one shot: creates a session, adds messages, and commits.
///
/// Input can be:
/// - A plain string → treated as a single "user" message
/// - A JSON object with "role" and "content" → single message with specified role
/// - A JSON array of {role, content} objects → multiple messages
pub async fn add_memory(
    client: &HttpClient,
    input: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let messages = parse_messages(input)?;

    // 1. Create a new session
    let session_response: serde_json::Value = client.post("/api/v1/sessions", &json!({})).await?;
    let mut profile_lines: Vec<serde_json::Value> = extract_profile_lines(&session_response);
    let session_id = session_response["session_id"].as_str().ok_or_else(|| {
        crate::error::Error::api("Failed to get session_id from new session response".to_string())
    })?;

    // 2. Add messages (batch)
    let path = format!("/api/v1/sessions/{}/messages/batch", url_encode(session_id));
    let messages_json: Vec<serde_json::Value> = messages
        .iter()
        .map(|(role, content)| json!({"role": role, "content": content}))
        .collect();
    let body = json!({"messages": messages_json});
    let response: serde_json::Value = client.post(&path, &body).await?;
    profile_lines.extend(extract_profile_lines(&response));

    // 3. Commit (async — don't read response)
    let commit_path = format!("/api/v1/sessions/{}/commit", url_encode(session_id));
    let commit_response: serde_json::Value = client.post(&commit_path, &json!({})).await?;
    profile_lines.extend(extract_profile_lines(&commit_response));

    let result = if profile_lines.is_empty() {
        json!("OK")
    } else {
        json!({
            "result": "OK",
            "profile": profile_lines,
        })
    };
    output_success(&result, output_format, compact);
    Ok(())
}

fn extract_profile_lines(value: &serde_json::Value) -> Vec<serde_json::Value> {
    value
        .get("profile")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default()
}

fn url_encode(s: &str) -> String {
    // Simple URL encoding for session IDs
    s.replace('/', "%2F")
        .replace(':', "%3A")
        .replace(' ', "%20")
}

#[cfg(test)]
mod tests {
    use super::{parse_messages, render_session_get_for_table};
    use crate::error::Error;
    use serde_json::json;

    #[test]
    fn parse_messages_reports_invalid_array_item_as_command_error() {
        let error = parse_messages(r#"[{"role":123,"content":"hello"}]"#)
            .expect_err("invalid role type should fail");

        match error {
            Error::Client(message) => {
                assert!(message.contains("messages[0]: 'role' must be a string"));
            }
            other => panic!("expected client error, got {other:?}"),
        }
    }

    #[test]
    fn parse_messages_reports_invalid_object_item_as_command_error() {
        let error = parse_messages(r#"{"role":"user","content":123}"#)
            .expect_err("invalid content type should fail");

        match error {
            Error::Client(message) => {
                assert!(message.contains("'content' must be a string"));
            }
            other => panic!("expected client error, got {other:?}"),
        }
    }

    #[test]
    fn add_memory_ok_output_can_include_profile_section() {
        let result = json!({
            "result": "OK",
            "profile": [
                "create session 1ms",
                "commit 2ms"
            ]
        });

        let rendered = crate::output::render_profiled_scalar_result(&result);

        assert_eq!(
            rendered,
            Some(["OK", "", "profile", "create session 1ms", "commit 2ms", "",].join("\n"))
        );
    }

    #[test]
    fn session_get_table_output_groups_metadata_sections() {
        let result = json!({
            "session_id": "d34f8a7c-eb14-49c4-b689-2743ddb9b75e",
            "created_at": "2026-05-26T09:53:03.661Z",
            "updated_at": "2026-05-26T10:00:07.603Z",
            "created_by_user_id": "haozhe",
            "participant_user_ids": ["haozhe"],
            "message_count": 0,
            "commit_count": 1,
            "memories_extracted": {
                "profile": 0,
                "preferences": 0,
                "entities": 0,
                "events": 0,
                "total": 3
            },
            "llm_token_usage": {
                "prompt_tokens": 14807,
                "completion_tokens": 1087,
                "total_tokens": 15894
            },
            "embedding_token_usage": {
                "total_tokens": 831
            },
            "pending_tokens": 0,
            "total_message_count": 2,
            "user": {
                "account_id": "default",
                "user_id": "haozhe"
            }
        });

        let rendered = strip_ansi(&render_session_get_for_table(&result).expect("session"));

        assert!(rendered.contains("Session"));
        assert!(rendered.contains("Identity"));
        assert!(rendered.contains("Activity"));
        assert!(rendered.contains("Memory"));
        assert!(rendered.contains("Tokens"));
        assert!(rendered.contains("d34f8a7c-eb14-49c4-b689-2743ddb9b75e"));
        assert!(rendered.contains("memories extracted"));
        assert!(rendered.contains("profile 0, preferences 0, entities 0, events 0, total 3"));
        assert!(!rendered.contains("{\"profile\":0"));
    }

    #[test]
    fn session_get_renderer_skips_non_session_objects() {
        let result = json!({"status": "ok"});

        assert!(render_session_get_for_table(&result).is_none());
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next == 'm' {
                        break;
                    }
                }
            } else {
                output.push(ch);
            }
        }
        output
    }
}
