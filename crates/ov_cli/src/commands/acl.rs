use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde_json::{Value, json};

fn show(value: Value, output_format: OutputFormat, compact: bool) -> Result<()> {
    output_success(value, output_format, compact);
    Ok(())
}

pub async fn get(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show(client.acl_get(uri).await?, output_format, compact)
}

pub async fn set(
    client: &HttpClient,
    uri: &str,
    raw_entries: Vec<String>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let mut entries = Vec::new();
    for raw in raw_entries {
        let Some((user_id, level)) = raw.split_once('=') else {
            return Err(Error::Client(format!(
                "Invalid ACL entry '{raw}'. Expected user=viewer|editor|manager."
            )));
        };
        entries.push(json!({"user_id": user_id, "level": level}));
    }
    show(client.acl_set(uri, entries).await?, output_format, compact)
}

pub async fn grant(
    client: &HttpClient,
    uri: &str,
    user_id: &str,
    level: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show(
        client.acl_grant(uri, user_id, level).await?,
        output_format,
        compact,
    )
}

pub async fn revoke(
    client: &HttpClient,
    uri: &str,
    user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show(
        client.acl_revoke(uri, user_id).await?,
        output_format,
        compact,
    )
}

pub async fn remove(
    client: &HttpClient,
    uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    show(client.acl_delete(uri).await?, output_format, compact)
}
