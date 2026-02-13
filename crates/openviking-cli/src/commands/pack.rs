use crate::client::HttpClient;
use crate::output::{output_error, output_success, OutputFormat};
use crate::error::Result;

pub async fn export(
    _client: &HttpClient,
    _uri: &str,
    _to: &str,
    _format: OutputFormat,
) -> Result<()> {
    println!("Pack export - not implemented");
    Ok(())
}

pub async fn import(
    _client: &HttpClient,
    _file_path: &str,
    _target: &str,
    _force: bool,
    _no_vectorize: bool,
    _format: OutputFormat,
) -> Result<()> {
    println!("Pack import - not implemented");
    Ok(())
}
