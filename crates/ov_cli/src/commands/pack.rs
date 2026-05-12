use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};

pub async fn export(
    client: &HttpClient,
    uri: &str,
    to: &str,
    include_vectors: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let file_path = client.export_ovpack(uri, to, include_vectors).await?;

    // Output success message with the file path
    let result = serde_json::json!({
        "file": file_path,
        "message": format!("Successfully exported to {}", file_path)
    });

    output_success(&result, format, compact);
    Ok(())
}

pub async fn backup(
    client: &HttpClient,
    to: &str,
    include_vectors: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let file_path = client.backup_ovpack(to, include_vectors).await?;

    let result = serde_json::json!({
        "file": file_path,
        "message": format!("Successfully backed up to {}", file_path)
    });

    output_success(&result, format, compact);
    Ok(())
}

pub async fn import(
    client: &HttpClient,
    file_path: &str,
    target: &str,
    on_conflict: Option<&str>,
    vector_mode: Option<&str>,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .import_ovpack(file_path, target, on_conflict, vector_mode)
        .await?;
    output_success(&result, format, compact);
    Ok(())
}

pub async fn restore(
    client: &HttpClient,
    file_path: &str,
    on_conflict: Option<&str>,
    vector_mode: Option<&str>,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .restore_ovpack(file_path, on_conflict, vector_mode)
        .await?;
    output_success(&result, format, compact);
    Ok(())
}
