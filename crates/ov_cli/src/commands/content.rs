use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;
use serde_json::Value;
use std::fs::File;
use std::io::Write;
use std::path::Path;

pub async fn read(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let content = client.read_profiled(uri).await?;
    output_content_result(content, output_format, compact)
}

pub async fn abstract_content(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let content = client.abstract_content_profiled(uri).await?;
    output_content_result(content, output_format, compact)
}

pub async fn overview(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let content = client.overview_profiled(uri).await?;
    output_content_result(content, output_format, compact)
}

pub async fn write(
    client: &HttpClient,
    uri: &str,
    content: &str,
    mode: &str,
    wait: bool,
    timeout: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .write(
            uri,
            content,
            mode,
            wait,
            timeout,
        )
        .await?;
    crate::output::output_success(result, output_format, compact);
    Ok(())
}

pub async fn reindex(
    client: &HttpClient,
    uri: &str,
    mode: &str,
    wait: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.reindex(uri, mode, wait).await?;
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

fn output_content_result(result: Value, output_format: OutputFormat, compact: bool) -> Result<()> {
    match output_format {
        OutputFormat::Json => crate::output::output_success(result, output_format, compact),
        OutputFormat::Table => {
            if let Some(rendered) = crate::output::render_profiled_scalar_result(&result) {
                println!("{}", rendered);
            } else if let Some(content) = result.as_str() {
                println!("{}", content);
            } else {
                crate::output::output_success(result, output_format, compact);
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    #[test]
    fn table_output_renders_profiled_scalar_content() {
        let result = json!({
            "result": "content",
            "profile": [
                "line one",
                "line two"
            ]
        });

        let rendered = crate::output::render_profiled_scalar_result(&result);

        assert_eq!(
            rendered,
            Some(
                [
                    "content",
                    "",
                    "profile",
                    "line one",
                    "line two",
                    "",
                ]
                .join("\n")
            )
        );
    }
}
