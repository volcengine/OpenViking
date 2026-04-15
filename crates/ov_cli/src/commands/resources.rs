use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};

pub async fn add_resource(
    client: &HttpClient,
    path: &str,
    to: Option<String>,
    parent: Option<String>,
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
) -> Result<()> {
    let result = client
        .add_resource(
            path,
            to,
            parent,
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
        )
        .await?;

    // Print helpful message for async processing
    if !wait && matches!(format, OutputFormat::Table) {
        eprintln!("Note: Resource is being processed in the background.");
        eprintln!("Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.");
    }

    output_success(&result, format, compact);
    Ok(())
}

pub async fn add_skill(
    client: &HttpClient,
    data: &str,
    wait: bool,
    timeout: Option<f64>,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.add_skill(data, wait, timeout).await?;

    // Print helpful message for async processing
    if !wait && matches!(format, OutputFormat::Table) {
        eprintln!("Note: Skill is being processed in the background.");
        eprintln!("Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.");
    }

    output_success(&result, format, compact);
    Ok(())
}
