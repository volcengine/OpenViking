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

pub async fn consistency(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.consistency(uri).await?;
    if matches!(output_format, OutputFormat::Table) {
        output_consistency_table(&response, compact);
    } else {
        output_success(&response, output_format, compact);
    }
    Ok(())
}

fn output_consistency_table(response: &serde_json::Value, compact: bool) {
    let summary = json!({
        "ok": response.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
        "expected_count": response.get("expected_count").and_then(|v| v.as_u64()).unwrap_or(0),
        "missing_record_count": response
            .get("missing_record_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        "missing_records_truncated": response
            .get("missing_records_truncated")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
    });
    output_success(&summary, OutputFormat::Table, compact);

    let Some(missing_records) = response.get("missing_records").and_then(|v| v.as_array()) else {
        return;
    };
    if missing_records.is_empty() {
        return;
    }

    println!();
    println!("missing_records");
    output_success(missing_records, OutputFormat::Table, compact);
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
