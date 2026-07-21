use std::time::Duration;

use tokio::time::Instant;

use crate::client::{CompileAccepted, CompileResult, CompileTaskStatus, HttpClient};
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};

pub async fn run(
    client: &HttpClient,
    from_uris: Vec<String>,
    to: String,
    skill: String,
    reason: Option<String>,
    wait: bool,
    timeout: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let sources = normalize_sources(from_uris)?;
    if timeout.is_some_and(|seconds| !seconds.is_finite() || seconds <= 0.0) {
        return Err(Error::Client("--timeout must be a positive number".into()));
    }
    let reason = reason
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let accepted = client
        .create_compile(&sources, to.trim(), skill.trim(), reason)
        .await?;
    if !wait {
        render_accepted(&accepted, output_format, compact);
        return Ok(());
    }

    let deadline = timeout.map(|seconds| Instant::now() + Duration::from_secs_f64(seconds));
    let mut polling = Duration::from_millis(500);
    loop {
        if deadline.is_some_and(|value| Instant::now() >= value) {
            return Err(Error::Client(format!(
                "Timed out waiting for compile task {}; the task is still running",
                accepted.task_id
            )));
        }
        let status = client.get_compile(&accepted.task_id).await?;
        match status.status.as_str() {
            "completed" => {
                render_completed(&status, output_format, compact);
                return Ok(());
            }
            "failed" => {
                let error = status.error.unwrap_or(crate::client::CompileErrorInfo {
                    code: "UNKNOWN".into(),
                    message: "Compile task failed".into(),
                });
                return Err(Error::api(format!("[{}] {}", error.code, error.message)));
            }
            _ => {}
        }

        let sleep_for = deadline
            .map(|value| polling.min(value.saturating_duration_since(Instant::now())))
            .unwrap_or(polling);
        tokio::time::sleep(sleep_for).await;
        polling = (polling * 2).min(Duration::from_secs(2));
    }
}

fn normalize_sources(values: Vec<String>) -> Result<Vec<String>> {
    let mut result = Vec::new();
    for value in values {
        for item in value.split(',') {
            let item = item.trim();
            if item.is_empty() {
                return Err(Error::Client("--from contains an empty directory".into()));
            }
            if !result.iter().any(|existing| existing == item) {
                result.push(item.to_string());
            }
        }
    }
    if result.is_empty() {
        return Err(Error::Client(
            "at least one --from directory is required".into(),
        ));
    }
    Ok(result)
}

fn render_accepted(value: &CompileAccepted, format: OutputFormat, compact: bool) {
    if matches!(format, OutputFormat::Json) {
        output_success(value, format, compact);
    } else {
        println!("task_id: {}", value.task_id);
        println!("status: {}", value.status);
        println!("to: {}", value.to);
    }
}

fn render_completed(value: &CompileTaskStatus, format: OutputFormat, compact: bool) {
    if matches!(format, OutputFormat::Json) {
        output_success(value, format, compact);
        return;
    }
    let result = value
        .result
        .as_ref()
        .cloned()
        .unwrap_or_else(|| CompileResult {
            from_uris: Vec::new(),
            to: String::new(),
            skill: String::new(),
            okf_version: "0.1".into(),
            created: Vec::new(),
            updated: Vec::new(),
            unchanged: Vec::new(),
            page_count: 0,
            link_count: 0,
            warnings: Vec::new(),
        });
    println!("to: {}", result.to);
    println!("created: {}", result.created.len());
    println!("updated: {}", result.updated.len());
    println!("unchanged: {}", result.unchanged.len());
    println!("page_count: {}", result.page_count);
    println!("link_count: {}", result.link_count);
    for warning in result.warnings {
        eprintln!("warning: {warning}");
    }
}

#[cfg(test)]
mod tests {
    use super::normalize_sources;

    #[test]
    fn expands_comma_separated_and_repeated_sources_stably() {
        let result = normalize_sources(vec![
            "viking://resources/a,viking://resources/b".into(),
            "viking://resources/a".into(),
        ])
        .expect("sources should be valid");
        assert_eq!(result, vec!["viking://resources/a", "viking://resources/b"]);
    }

    #[test]
    fn rejects_empty_source_items() {
        assert!(normalize_sources(vec!["viking://resources/a,".into()]).is_err());
    }
}
