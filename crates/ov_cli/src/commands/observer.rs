use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn queue(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.get("/api/v1/observer/queue", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn vikingdb(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.get("/api/v1/observer/vikingdb", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn vlm(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.get("/api/v1/observer/vlm", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn system(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: serde_json::Value = client.get("/api/v1/observer/system", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}
