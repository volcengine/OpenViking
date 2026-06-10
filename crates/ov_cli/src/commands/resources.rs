use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};

pub async fn add_resource(
    client: &HttpClient,
    path: &str,
    to: Option<String>,
    parent: Option<String>,
    parent_auto_create: Option<String>,
    reason: String,
    instruction: String,
    wait: bool,
    timeout: Option<f64>,
    strict: bool,
    ignore_dirs: Option<String>,
    include: Option<String>,
    exclude: Option<String>,
    directly_upload_media: bool,
    watch_interval: f64,
    format: OutputFormat,
    compact: bool,
    show_progress: bool,
    verbose: bool,
) -> Result<()> {
    let recovery_target = to.clone();
    let result = match client
        .add_resource(
            path,
            to,
            parent,
            parent_auto_create,
            &reason,
            &instruction,
            wait,
            timeout,
            strict,
            ignore_dirs,
            include,
            exclude,
            directly_upload_media,
            watch_interval,
            show_progress,
            verbose,
        )
        .await
    {
        Ok(result) => result,
        Err(error) => {
            if let (Some(target), Error::Network(_)) = (recovery_target.as_deref(), &error) {
                if let Ok(stat) = client.stat(target).await {
                    let is_locked = stat
                        .get("isLocked")
                        .and_then(|value| value.as_bool())
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "unknown".to_string());
                    let count = stat
                        .get("count")
                        .and_then(|value| value.as_u64())
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "unknown".to_string());
                    eprintln!(
                        "Recovery: target URI exists after the network error: {target} (isLocked={is_locked}, count={count})."
                    );
                    eprintln!(
                        "Run 'ov wait' and 'ov stat {target}' to confirm whether processing completed."
                    );
                }
            }
            return Err(error);
        }
    };

    if !wait && matches!(format, OutputFormat::Table) {
        eprintln!("Note: Resource is being processed in the background.");
        eprintln!(
            "Use 'ov task status <task_id>' to check progress, or 'ov task list' to see all tasks."
        );
    }

    output_success(&result, format, compact);
    Ok(())
}

pub async fn add_skill(
    client: &HttpClient,
    data: &str,
    wait: bool,
    timeout: Option<f64>,
    show_progress: bool,
    verbose: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .add_skill(data, wait, timeout, show_progress, verbose, None)
        .await?;

    if !wait && matches!(format, OutputFormat::Table) {
        eprintln!("Note: Skill is being processed in the background.");
        eprintln!(
            "Use 'ov task status <task_id>' to check progress, or 'ov task list' to see all tasks."
        );
    }

    output_success(&result, format, compact);
    Ok(())
}
