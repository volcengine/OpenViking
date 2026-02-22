use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn find(
    client: &HttpClient,
    query: &str,
    uri: &str,
    limit: i32,
    threshold: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.find(query.to_string(), uri.to_string(), limit, threshold).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn search(
    client: &HttpClient,
    query: &str,
    uri: &str,
    session_id: Option<String>,
    limit: i32,
    threshold: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.search(query.to_string(), uri.to_string(), session_id, limit, threshold).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn grep(
    client: &HttpClient,
    uri: &str,
    pattern: &str,
    ignore_case: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.grep(uri, pattern, ignore_case).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn glob(
    client: &HttpClient,
    pattern: &str,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.glob(pattern, uri).await?;
    output_success(&result, output_format, compact);
    Ok(())
}
