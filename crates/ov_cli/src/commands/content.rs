use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::fs::File;
use std::io::Write;
use std::path::Path;

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

pub async fn write(
    client: &HttpClient,
    uri: &str,
    content: &str,
    append: bool,
    wait: bool,
    timeout: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .write(
            uri,
            content,
            if append { "append" } else { "replace" },
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
    regenerate: bool,
    wait: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.reindex(uri, regenerate, wait).await?;
    crate::output::output_success(result, output_format, compact);
    Ok(())
}

pub async fn reindex_all_dry_run(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let listing = client
        .ls("viking://resources", true, true, "original", 0, false, 100_000)
        .await?;
    let resource_uris = collect_resource_uris(&listing);
    let embedding_summary = load_embedding_config_summary();

    let result = json!({
        "mode": "dry-run",
        "scope": "all",
        "root_uri": "viking://resources",
        "resource_count": resource_uris.len(),
        "resource_uris": resource_uris,
        "embedding": embedding_summary.to_value(),
    });

    crate::output::output_success(&result, output_format, compact);
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct EmbeddingConfigSummary {
    provider: Option<String>,
    model: Option<String>,
    dimension: Option<i64>,
    config_path: Option<String>,
    note: Option<String>,
}

impl EmbeddingConfigSummary {
    fn to_value(&self) -> Value {
        json!({
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "config_path": self.config_path,
            "note": self.note,
        })
    }
}

fn load_embedding_config_summary() -> EmbeddingConfigSummary {
    match resolve_server_config_path() {
        Some(path) => load_embedding_config_summary_from_path(&path),
        None => EmbeddingConfigSummary {
            provider: None,
            model: None,
            dimension: None,
            config_path: None,
            note: Some("ov.conf not found".to_string()),
        },
    }
}

fn load_embedding_config_summary_from_path(path: &Path) -> EmbeddingConfigSummary {
    let config_path = path.display().to_string();
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(err) => {
            return EmbeddingConfigSummary {
                provider: None,
                model: None,
                dimension: None,
                config_path: Some(config_path),
                note: Some(format!("Failed to read ov.conf: {}", err)),
            };
        }
    };

    let parsed: Value = match serde_json::from_str(&content) {
        Ok(value) => value,
        Err(err) => {
            return EmbeddingConfigSummary {
                provider: None,
                model: None,
                dimension: None,
                config_path: Some(config_path),
                note: Some(format!("Failed to parse ov.conf: {}", err)),
            };
        }
    };

    let mut summary = parse_embedding_config_summary(&parsed);
    summary.config_path = Some(config_path);
    if summary.note.is_none() && (summary.provider.is_none() || summary.model.is_none()) {
        summary.note = Some("embedding.dense is not configured".to_string());
    }
    summary
}

fn parse_embedding_config_summary(value: &Value) -> EmbeddingConfigSummary {
    let dense = value
        .get("embedding")
        .and_then(|embedding| embedding.get("dense"))
        .and_then(Value::as_object);

    let provider = dense
        .and_then(|dense| dense.get("provider"))
        .and_then(Value::as_str)
        .map(str::to_string);
    let model = dense
        .and_then(|dense| dense.get("model"))
        .and_then(Value::as_str)
        .map(str::to_string);
    let dimension = dense
        .and_then(|dense| dense.get("dimension"))
        .and_then(value_as_i64);

    EmbeddingConfigSummary {
        provider,
        model,
        dimension,
        config_path: None,
        note: None,
    }
}

fn resolve_server_config_path() -> Option<std::path::PathBuf> {
    if let Ok(path) = env::var("OPENVIKING_CONFIG_FILE") {
        let path = std::path::PathBuf::from(path);
        if path.exists() {
            return Some(path);
        }
    }

    if let Some(home) = dirs::home_dir() {
        let path = home.join(".openviking").join("ov.conf");
        if path.exists() {
            return Some(path);
        }
    }

    let system_path = std::path::PathBuf::from("/etc/openviking/ov.conf");
    if system_path.exists() {
        return Some(system_path);
    }

    None
}

fn value_as_i64(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number.as_i64(),
        Value::String(text) => text.parse::<i64>().ok(),
        _ => None,
    }
}

fn is_reindexable_resource_uri(uri: &str) -> bool {
    uri.starts_with("viking://resources") && uri != "viking://resources" && uri != "viking://resources/"
}

fn collect_resource_uris(value: &Value) -> Vec<String> {
    let mut uris = BTreeSet::new();
    collect_resource_uris_into(value, &mut uris);
    uris.into_iter().collect()
}

fn collect_resource_uris_into(value: &Value, uris: &mut BTreeSet<String>) {
    match value {
        Value::String(uri) => {
            if is_reindexable_resource_uri(uri) {
                uris.insert(uri.to_string());
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_resource_uris_into(item, uris);
            }
        }
        Value::Object(map) => {
            if let Some(uri) = map.get("uri").and_then(Value::as_str) {
                if is_reindexable_resource_uri(uri) {
                    uris.insert(uri.to_string());
                }
            }
            for key in ["children", "tree", "result"] {
                if let Some(nested) = map.get(key) {
                    collect_resource_uris_into(nested, uris);
                }
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::{collect_resource_uris, load_embedding_config_summary_from_path, parse_embedding_config_summary};
    use serde_json::json;
    use std::fs;

    #[test]
    fn collect_resource_uris_deduplicates_nested_entries() {
        let listing = json!([
            "viking://resources",
            "viking://resources/doc-a.md",
            {"uri": "viking://resources/doc-b.md"},
            {
                "uri": "viking://resources/folder",
                "children": [
                    {"uri": "viking://resources/doc-a.md"},
                    {"uri": "viking://resources/folder/doc-c.md"}
                ]
            }
        ]);

        let uris = collect_resource_uris(&listing);

        assert_eq!(
            uris,
            vec![
                "viking://resources/doc-a.md".to_string(),
                "viking://resources/doc-b.md".to_string(),
                "viking://resources/folder".to_string(),
                "viking://resources/folder/doc-c.md".to_string(),
            ]
        );
    }

    #[test]
    fn parse_embedding_config_summary_reads_provider_model_and_dimension() {
        let config = json!({
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "dimension": "1536"
                }
            }
        });

        let summary = parse_embedding_config_summary(&config);

        assert_eq!(summary.provider.as_deref(), Some("openai"));
        assert_eq!(summary.model.as_deref(), Some("text-embedding-3-small"));
        assert_eq!(summary.dimension, Some(1536));
    }

    #[test]
    fn load_embedding_config_summary_from_path_reports_parse_errors() {
        let temp_dir = tempfile::tempdir().expect("tempdir should be created");
        let config_path = temp_dir.path().join("ov.conf");
        fs::write(&config_path, "{not-json").expect("config should be written");

        let summary = load_embedding_config_summary_from_path(&config_path);

        assert_eq!(summary.config_path.as_deref(), Some(config_path.to_string_lossy().as_ref()));
        assert!(summary.note.as_deref().unwrap_or_default().contains("Failed to parse ov.conf"));
    }
}
