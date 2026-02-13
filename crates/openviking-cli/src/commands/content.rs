use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn read(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
) -> Result<()> {
    let content = client.read(uri).await?;
    println!("{}", content);
    Ok(())
}

pub async fn abstract_content(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
) -> Result<()> {
    let content = client.abstract_content(uri).await?;
    println!("{}", content);
    Ok(())
}

pub async fn overview(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
) -> Result<()> {
    let content = client.overview(uri).await?;
    println!("{}", content);
    Ok(())
}
