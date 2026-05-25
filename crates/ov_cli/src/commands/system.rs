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
    let response: serde_json::Value = client
        .post("/api/v1/system/wait", &json!({ "timeout": timeout }))
        .await?;
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
    let mut sections = vec![
        crate::output::render_table_with_optional_profile(&summary, compact)
            .unwrap_or_default()
            .trim_end()
            .to_string(),
    ];

    let Some(missing_records) = response.get("missing_records").and_then(|v| v.as_array()) else {
        println!(
            "{}",
            crate::output::append_profile_to_rendered(sections.join("\n"), response)
        );
        return;
    };
    if missing_records.is_empty() {
        println!(
            "{}",
            crate::output::append_profile_to_rendered(sections.join("\n"), response)
        );
        return;
    }

    sections.push("missing_records".to_string());
    sections.push(
        crate::output::render_table_with_optional_profile(
            &serde_json::Value::Array(missing_records.clone()),
            compact,
        )
        .unwrap_or_default()
        .trim_end()
        .to_string(),
    );
    println!(
        "{}",
        crate::output::append_profile_to_rendered(sections.join("\n\n"), response)
    );
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    #[test]
    fn consistency_table_output_keeps_profile_section() {
        let response = json!({
            "ok": true,
            "expected_count": 3,
            "missing_record_count": 1,
            "missing_records_truncated": false,
            "missing_records": [
                {"key": "viking://a", "value": "missing"}
            ],
            "profile": [
                "consistency took 2ms"
            ]
        });

        let full = crate::output::append_profile_to_rendered(
            "ok  true\n\nmissing_records\nkey         value\nviking://a  missing".to_string(),
            &response,
        );

        assert!(full.contains("profile\nconsistency took 2ms\n"));
    }
}
