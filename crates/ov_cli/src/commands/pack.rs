use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn export(
    client: &HttpClient,
    uri: &str,
    to: &str,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.export_ovpack(uri, to).await?;
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
