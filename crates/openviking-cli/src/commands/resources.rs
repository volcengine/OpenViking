use crate::client::HttpClient;
use crate::output::{output_error, output_success, OutputFormat};
use crate::error::Result;

pub async fn add_resource(
    _client: &HttpClient,
    _path: &str,
    _to: Option<String>,
    _reason: String,
    _instruction: String,
    _wait: bool,
    _timeout: Option<f64>,
    _format: OutputFormat,
) -> Result<()> {
    println!("Add resource - not implemented");
    Ok(())
}

pub async fn add_skill(
    _client: &HttpClient,
    _data: &str,
    _wait: bool,
    _timeout: Option<f64>,
    _format: OutputFormat,
) -> Result<()> {
    println!("Add skill - not implemented");
    Ok(())
}
