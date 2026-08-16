use crate::client::HttpClient;
use crate::config::Config;
use crate::error::{Error, Result};
use crate::health_ui;
use crate::output::{OutputFormat, output_success};
use crate::status_ui;
use serde_json::json;
use std::io::Write;

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

pub async fn diagnostic_status(
    client: &HttpClient,
    config: &Config,
    output_format: OutputFormat,
    compact: bool,
    verbose: bool,
) -> Result<()> {
    let meta = status_ui::current_config_meta();

    if matches!(output_format, OutputFormat::Json) || verbose {
        let response: serde_json::Value = client.get("/api/v1/observer/system", &[]).await?;
        output_success(&response, output_format, compact);
        return Ok(());
    }

    match client
        .get::<serde_json::Value>("/api/v1/observer/system", &[])
        .await
    {
        Ok(response) => {
            print!(
                "{}",
                status_ui::render_status(&response, config, meta.active_name.as_deref(),)?
            );
        }
        Err(error) => {
            print!(
                "{}",
                status_ui::render_unreachable_status(
                    config,
                    meta.active_name.as_deref(),
                    meta.saved_count,
                    Some(&error),
                )
            );
        }
    }

    Ok(())
}

pub async fn consistency(
    client: &HttpClient,
    uri: &str,
    issue_types: Vec<String>,
    limit: Option<usize>,
    cursor: Option<String>,
    max_scan_records: Option<usize>,
    repair_plan: Option<String>,
    force: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client
        .consistency(
            uri,
            &issue_types,
            limit,
            cursor.as_deref(),
            max_scan_records,
            repair_plan.is_some(),
        )
        .await?;
    if let Some(path) = repair_plan {
        write_repair_plan(&path, response.get("repair_plan"), force)?;
    }
    if matches!(output_format, OutputFormat::Table) {
        output_consistency_table(&response, compact);
    } else {
        output_success(&response, output_format, compact);
    }
    Ok(())
}

fn write_repair_plan(path: &str, plan: Option<&serde_json::Value>, force: bool) -> Result<()> {
    let plan = plan.ok_or_else(|| {
        Error::Client("Server did not return an executable repair plan".to_string())
    })?;
    let mut options = std::fs::OpenOptions::new();
    options.write(true);
    if force {
        options.create(true).truncate(true);
    } else {
        options.create_new(true);
    }
    let mut file = options
        .open(path)
        .map_err(|error| Error::Client(format!("Failed to create repair plan {path}: {error}")))?;
    let bytes = serde_json::to_vec_pretty(plan)
        .map_err(|error| Error::Client(format!("Failed to serialize repair plan: {error}")))?;
    file.write_all(&bytes)?;
    file.write_all(b"\n")?;
    Ok(())
}

pub async fn backend_sync_status(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.backend_sync_status(uri).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn backend_sync_retry(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.backend_sync_retry(uri).await?;
    output_success(&response, output_format, compact);
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
        "complete": response.get("complete").cloned().unwrap_or(serde_json::Value::Null),
        "scanned_count": response.get("scanned_count").cloned().unwrap_or(serde_json::Value::Null),
        "truncated": response.get("truncated").cloned().unwrap_or(serde_json::Value::Null),
    });
    let mut sections = vec![
        crate::output::render_table_with_optional_profile(&summary, compact)
            .unwrap_or_default()
            .trim_end()
            .to_string(),
    ];

    if let Some(missing_records) = response.get("missing_records").and_then(|v| v.as_array())
        && !missing_records.is_empty()
    {
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
    }
    if let Some(findings) = response.get("findings").and_then(|v| v.as_array())
        && !findings.is_empty()
    {
        sections.push("findings".to_string());
        sections.push(
            crate::output::render_table_with_optional_profile(
                &serde_json::Value::Array(findings.clone()),
                compact,
            )
            .unwrap_or_default()
            .trim_end()
            .to_string(),
        );
    }
    println!(
        "{}",
        crate::output::append_profile_to_rendered(sections.join("\n\n"), response)
    );
}

pub async fn health(
    client: &HttpClient,
    config: Option<&Config>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<bool> {
    let response: serde_json::Value = client.get("/health", &[]).await?;

    // Extract the key fields
    let healthy = response
        .get("healthy")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if matches!(output_format, OutputFormat::Json) {
        output_success(&response, output_format, compact);
    } else {
        print!("{}", health_ui::render_health(&response, config));
    }

    Ok(healthy)
}

#[cfg(test)]
mod tests {
    use super::write_repair_plan;
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

    #[test]
    fn repair_plan_file_requires_force_to_overwrite() {
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("repair-plan.json");
        let path = path.to_string_lossy();
        let first = json!({"plan_version": "index-repair/v1", "plan_digest": "one"});
        let second = json!({"plan_version": "index-repair/v1", "plan_digest": "two"});

        write_repair_plan(&path, Some(&first), false).expect("initial write");
        assert!(write_repair_plan(&path, Some(&second), false).is_err());
        write_repair_plan(&path, Some(&second), true).expect("forced overwrite");

        let written: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path.as_ref()).expect("read plan"))
                .expect("valid json");
        assert_eq!(written, second);
    }
}
