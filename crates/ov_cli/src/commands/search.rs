use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};

pub async fn find(
    client: &HttpClient,
    query: &str,
    uri: &str,
    node_limit: i32,
    threshold: Option<f64>,
    since: Option<&str>,
    until: Option<&str>,
    time_field: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .find(
            query.to_string(),
            uri.to_string(),
            node_limit,
            threshold,
            since.map(|s| s.to_string()),
            until.map(|s| s.to_string()),
            time_field.map(|s| s.to_string()),
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn search(
    client: &HttpClient,
    query: &str,
    uri: &str,
    session_id: Option<String>,
    node_limit: i32,
    threshold: Option<f64>,
    since: Option<&str>,
    until: Option<&str>,
    time_field: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .search(
            query.to_string(),
            uri.to_string(),
            session_id,
            node_limit,
            threshold,
            since.map(|s| s.to_string()),
            until.map(|s| s.to_string()),
            time_field.map(|s| s.to_string()),
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}
pub async fn grep(
    client: &HttpClient,
    uri: &str,
    exclude_uri: Option<String>,
    pattern: &str,
    ignore_case: bool,
    node_limit: i32,
    level_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .grep(
            uri,
            exclude_uri,
            pattern,
            ignore_case,
            node_limit,
            level_limit,
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn glob(
    client: &HttpClient,
    pattern: &str,
    uri: &str,
    node_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.glob(pattern, uri, node_limit).await?;
    output_success(&result, output_format, compact);
    Ok(())
}
