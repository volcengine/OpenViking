use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};
use serde_json::json;

pub async fn wait(
    client: &HttpClient,
    timeout: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let path = if let Some(t) = timeout {
        format!("/api/v1/system/wait?timeout={}", t)
    } else {
        "/api/v1/system/wait".to_string()
    };

    let response: serde_json::Value = client.post(&path, &json!({})).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn status(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.get("/api/v1/system/status", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn health(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<bool> {
    let response: serde_json::Value = client.get("/health", &[]).await?;
    
    // Extract the key fields
    let healthy = response.get("healthy").and_then(|v| v.as_bool()).unwrap_or(false);
    let _status = response.get("status").and_then(|v| v.as_str());
    let version = response.get("version").and_then(|v| v.as_str());
    let user_id = response.get("user_id").and_then(|v| v.as_str());
    
    // For table output, print in a readable format
    if matches!(output_format, OutputFormat::Table) || matches!(output_format, OutputFormat::Json) {
        output_success(&response, output_format, compact);
    } else {
        // Simple text output
        print!("healthy  {}", if healthy { "true" } else { "false" });
        if let Some(v) = version {
            print!("  version  {}", v);
        }
        if let Some(u) = user_id {
            print!("  user_id  {}", u);
        }
        println!();
    }
    
    Ok(healthy)
}
