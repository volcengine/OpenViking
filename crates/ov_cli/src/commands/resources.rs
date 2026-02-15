use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn add_resource(
    client: &HttpClient,
    path: &str,
    to: Option<String>,
    reason: String,
    instruction: String,
    wait: bool,
    timeout: Option<f64>,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .add_resource(path, to, &reason, &instruction, wait, timeout)
        .await?;
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
    output_success(&result, format, compact);
    Ok(())
}
