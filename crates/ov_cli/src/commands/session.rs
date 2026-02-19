use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};
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

pub async fn delete_session(
    client: &HttpClient,
    session_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = format!("/api/v1/sessions/{}", url_encode(session_id));
    let response: serde_json::Value = client.delete(&path, &[]).await?;
    
    // Return session_id in result if empty (similar to Python implementation)
    let result = if response.is_null() || response.as_object().map(|o| o.is_empty()).unwrap_or(false) {
        json!({"session_id": session_id})
    } else {
        response
    };
    
    output_success(&result, output_format, compact);
    Ok(())
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
    // Parse input to determine messages
    let messages: Vec<(String, String)> = if let Ok(value) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(arr) = value.as_array() {
            // JSON array of {role, content}
            arr.iter()
                .map(|item| {
                    let role = item["role"].as_str().unwrap_or("user").to_string();
                    let content = item["content"].as_str().unwrap_or("").to_string();
                    (role, content)
                })
                .collect()
        } else if value.get("role").is_some() || value.get("content").is_some() {
            // Single JSON object with role/content
            let role = value["role"].as_str().unwrap_or("user").to_string();
            let content = value["content"].as_str().unwrap_or("").to_string();
            vec![(role, content)]
        } else {
            // JSON but not a message object, treat as plain string
            vec![("user".to_string(), input.to_string())]
        }
    } else {
        // Plain string
        vec![("user".to_string(), input.to_string())]
    };

    // 1. Create a new session
    let session_response: serde_json::Value = client.post("/api/v1/sessions", &json!({})).await?;
    let session_id = session_response["session_id"]
        .as_str()
        .ok_or_else(|| crate::error::Error::Api("Failed to get session_id from new session response".to_string()))?;

    // 2. Add messages
    for (role, content) in &messages {
        let path = format!("/api/v1/sessions/{}/messages", url_encode(session_id));
        let body = json!({
            "role": role,
            "content": content
        });
        let _: serde_json::Value = client.post(&path, &body).await?;
    }

    // 3. Commit
    let commit_path = format!("/api/v1/sessions/{}/commit", url_encode(session_id));
    let commit_response: serde_json::Value = client.post(&commit_path, &json!({})).await?;

    // Extract memories count from commit response
    let memories_extracted = commit_response["memories_extracted"].as_i64().unwrap_or(0);

    let result = json!({
        "memories_extracted": memories_extracted
    });
    output_success(&result, output_format, compact);
    Ok(())
}

fn url_encode(s: &str) -> String {
    // Simple URL encoding for session IDs
    s.replace('/', "%2F")
        .replace(':', "%3A")
        .replace(' ', "%20")
}
