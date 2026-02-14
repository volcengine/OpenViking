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
) -> Result<()> {
    let response: serde_json::Value = client.get("/health", &[]).await?;
    
    // For health check, if it's a simple status, just print it
    if let Some(status) = response.get("status").and_then(|v| v.as_str()) {
        if matches!(output_format, OutputFormat::Json) {
            output_success(&response, output_format, compact);
        } else {
            println!("{}", status);
        }
    } else {
        output_success(&response, output_format, compact);
    }
    
    Ok(())
}
