use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};

fn output_path(to: Option<&str>, wait: bool) -> Result<Option<&str>> {
    match (wait, to) {
        (true, None) => Err(Error::Client(
            "An output path is required when waiting for OVPack".into(),
        )),
        (false, Some(_)) => Err(Error::Client(
            "Omit the output path with --wait false".into(),
        )),
        _ => Ok(to),
    }
}

pub async fn export(
    client: &HttpClient,
    uri: &str,
    to: Option<&str>,
    include_vectors: bool,
    wait: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let mut result = client
        .export_ovpack(
            uri,
            output_path(to, wait)?,
            include_vectors,
            matches!(format, OutputFormat::Table),
        )
        .await?;
    if let Some(file) = result["file"].as_str() {
        result["message"] = serde_json::json!(format!("Successfully exported to {file}"));
    }

    output_success(&result, format, compact);
    Ok(())
}

pub async fn backup(
    client: &HttpClient,
    to: Option<&str>,
    include_vectors: bool,
    wait: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let mut result = client
        .backup_ovpack(
            output_path(to, wait)?,
            include_vectors,
            matches!(format, OutputFormat::Table),
        )
        .await?;
    if let Some(file) = result["file"].as_str() {
        result["message"] = serde_json::json!(format!("Successfully backed up to {file}"));
    }

    output_success(&result, format, compact);
    Ok(())
}

pub async fn import(
    client: &HttpClient,
    file_path: &str,
    target: &str,
    on_conflict: Option<&str>,
    vector_mode: Option<&str>,
    wait: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .import_ovpack(
            file_path,
            target,
            on_conflict,
            vector_mode,
            wait,
            matches!(format, OutputFormat::Table),
        )
        .await?;
    output_success(&result, format, compact);
    Ok(())
}

pub async fn restore(
    client: &HttpClient,
    file_path: &str,
    on_conflict: Option<&str>,
    vector_mode: Option<&str>,
    wait: bool,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .restore_ovpack(
            file_path,
            on_conflict,
            vector_mode,
            wait,
            matches!(format, OutputFormat::Table),
        )
        .await?;
    output_success(&result, format, compact);
    Ok(())
}
