use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn ls(
    client: &HttpClient,
    uri: &str,
    simple: bool,
    recursive: bool,
    output: &str,
    abs_limit: i32,
    show_all_hidden: bool,
    node_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.ls(uri, simple, recursive, output, abs_limit, show_all_hidden, node_limit).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn tree(
    client: &HttpClient,
    uri: &str,
    output: &str,
    abs_limit: i32,
    show_all_hidden: bool,
    node_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.tree(uri, output, abs_limit, show_all_hidden, node_limit).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn mkdir(
    client: &HttpClient,
    uri: &str,
    _output_format: OutputFormat,
    _compact: bool,
) -> Result<()> {
    client.mkdir(uri).await?;
    println!("Directory created: {}", uri);
    Ok(())
}

pub async fn rm(
    client: &HttpClient,
    uri: &str,
    recursive: bool,
    _output_format: OutputFormat,
    _compact: bool,
) -> Result<()> {
    client.rm(uri, recursive).await?;
    println!("Removed: {}", uri);
    Ok(())
}

pub async fn mv(
    client: &HttpClient,
    from_uri: &str,
    to_uri: &str,
    _output_format: OutputFormat,
    _compact: bool,
) -> Result<()> {
    client.mv(from_uri, to_uri).await?;
    println!("Moved: {} -> {}", from_uri, to_uri);
    Ok(())
}

pub async fn stat(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.stat(uri).await?;
    output_success(&result, output_format, compact);
    Ok(())
}
