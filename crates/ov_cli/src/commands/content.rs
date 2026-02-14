use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;

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
