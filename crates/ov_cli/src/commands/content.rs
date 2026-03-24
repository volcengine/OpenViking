use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;
use std::fs::File;
use std::io::Write;
use std::path::Path;

pub async fn read(
    client: &HttpClient,
    uri: &str,
    _output_format: OutputFormat,
    _compact: bool,
) -> Result<()> {
    let content = client.read(uri).await?;
    println!("{}", content);
    Ok(())
}

pub async fn abstract_content(
    client: &HttpClient,
    uri: &str,
    _output_format: OutputFormat,
    _compact: bool,
) -> Result<()> {
    let content = client.abstract_content(uri).await?;
    println!("{}", content);
    Ok(())
}

pub async fn overview(
    client: &HttpClient,
    uri: &str,
    _output_format: OutputFormat,
    _compact: bool,
) -> Result<()> {
    let content = client.overview(uri).await?;
    println!("{}", content);
    Ok(())
}

pub async fn reindex(
    client: &HttpClient,
    uri: &str,
    regenerate: bool,
    wait: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.reindex(uri, regenerate, wait).await?;
    crate::output::output_success(result, output_format, compact);
    Ok(())
}

pub async fn get(client: &HttpClient, uri: &str, local_path: &str) -> Result<()> {
    // Check if target path already exists
    let path = Path::new(local_path);
    if path.exists() {
        return Err(crate::error::Error::Client(format!(
            "File already exists: {}",
            local_path
        )));
    }

    // Ensure parent directory exists
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // Download file
    let bytes = client.get_bytes(uri).await?;

    // Write to local file
    let mut file = File::create(path)?;
    file.write_all(&bytes)?;
    file.flush()?;

    println!("Downloaded {} bytes to {}", bytes.len(), local_path);
    Ok(())
}
