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

fn url_encode(s: &str) -> String {
    // Simple URL encoding for session IDs
    s.replace('/', "%2F")
        .replace(':', "%3A")
        .replace(' ', "%20")
}
