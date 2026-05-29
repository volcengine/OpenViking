use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};
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
    output_success(&response, output_format, compact);
    Ok(())
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
                        crate::error::Error::Api(format!(
                            "messages[{}]: 'role' must be a string, got {:?}",
                            i, item["role"]
                        ))
                    })?;
                    let content = item["content"].as_str().ok_or_else(|| {
                        crate::error::Error::Api(format!(
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
                crate::error::Error::Api(format!(
                    "'role' must be a string, got {:?}",
                    value["role"]
                ))
            })?;
            let content = value["content"].as_str().ok_or_else(|| {
                crate::error::Error::Api(format!(
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
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}/messages", url_encode(session_id));
    let body = json!({
        "role": role,
        "content": content
    });

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
    let path = format!(
        "/api/v1/sessions/{}/messages/batch",
        url_encode(session_id)
    );
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
        crate::error::Error::Api("Failed to get session_id from new session response".to_string())
    })?;

    // 2. Add messages (batch)
    let path = format!(
        "/api/v1/sessions/{}/messages/batch",
        url_encode(session_id)
    );
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
    use serde_json::json;

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
            Some(
                [
                    "OK",
                    "",
                    "profile",
                    "create session 1ms",
                    "commit 2ms",
                    "",
                ]
                .join("\n")
            )
        );
    }
}
