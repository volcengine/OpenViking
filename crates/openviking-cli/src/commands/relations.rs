use crate::client::HttpClient;
use crate::output::{output_error, output_success, OutputFormat};
use crate::error::Result;

pub async fn list_relations(
    _client: &HttpClient,
    _uri: &str,
    _format: OutputFormat,
) -> Result<()> {
    println!("Relations list - not implemented");
    Ok(())
}

pub async fn link(
    _client: &HttpClient,
    _from_uri: &str,
    _to_uris: &Vec<String>,
    _reason: &str,
    _format: OutputFormat,
) -> Result<()> {
    println!("Relations link - not implemented");
    Ok(())
}

pub async fn unlink(
    _client: &HttpClient,
    _from_uri: &str,
    _to_uri: &str,
    _format: OutputFormat,
) -> Result<()> {
    println!("Relations unlink - not implemented");
    Ok(())
}
