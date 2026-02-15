use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};

pub async fn list_relations(
    client: &HttpClient,
    uri: &str,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.relations(uri).await?;
    output_success(&result, format, compact);
    Ok(())
}

pub async fn link(
    client: &HttpClient,
    from_uri: &str,
    to_uris: &Vec<String>,
    reason: &str,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.link(from_uri, to_uris, reason).await?;
    // If the server returns null/empty, show a confirmation summary
    if result.is_null() {
        let summary = serde_json::json!({
            "from": from_uri,
            "to": to_uris,
            "reason": reason,
        });
        output_success(&summary, format, compact);
    } else {
        output_success(&result, format, compact);
    }
    Ok(())
}

pub async fn unlink(
    client: &HttpClient,
    from_uri: &str,
    to_uri: &str,
    format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.unlink(from_uri, to_uri).await?;
    if result.is_null() {
        let summary = serde_json::json!({
            "from": from_uri,
            "to": to_uri,
        });
        output_success(&summary, format, compact);
    } else {
        output_success(&result, format, compact);
    }
    Ok(())
}
