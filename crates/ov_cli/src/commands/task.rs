use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};

pub async fn status(
    client: &HttpClient,
    task_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.get_task(task_id).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn list(
    client: &HttpClient,
    task_type: Option<&str>,
    status: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.list_tasks(task_type, status).await?;
    output_success(&result, output_format, compact);
    Ok(())
}
