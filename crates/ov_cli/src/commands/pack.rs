use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};

pub async fn export(
    client: &HttpClient,
    uri: &str,
    to: &str,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let file_path = client.export_ovpack(uri, to).await?;

    // Output success message with the file path
    let result = serde_json::json!({
        "file": file_path,
        "message": format!("Successfully exported to {}", file_path)
    });

    output_success(&result, format, compact);
    Ok(())
}

pub async fn import(
    client: &HttpClient,
    file_path: &str,
    target: &str,
    force: bool,
    no_vectorize: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let vectorize = !no_vectorize;
    let result = client
        .import_ovpack(file_path, target, force, vectorize)
        .await?;
    output_success(&result, format, compact);
    Ok(())
}
