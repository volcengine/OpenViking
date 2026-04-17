use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};
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

pub async fn status(client: &HttpClient, output_format: OutputFormat, compact: bool) -> Result<()> {
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
    let healthy = response
        .get("healthy")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // For table output, print in a readable format
    if matches!(output_format, OutputFormat::Table) || matches!(output_format, OutputFormat::Json) {
        output_success(&response, output_format, compact);
    } else {
        // Simple text output - print healthy first, then other fields line by line
        println!("healthy  {}", if healthy { "true" } else { "false" });
        if let Some(obj) = response.as_object() {
            for (key, value) in obj {
                if key != "healthy" {
                    println!("{}  {}", key, value);
                }
            }
        }
    }

    Ok(healthy)
}
