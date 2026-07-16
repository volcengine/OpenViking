use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;
use serde_json::{Value, json};
use std::collections::BTreeSet;
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
    let result = client.write(uri, content, mode, wait, timeout).await?;
    crate::output::output_success(result, output_format, compact);
    Ok(())
}

pub async fn set_tags(
    client: &HttpClient,
    uri: &str,
    tags: Vec<String>,
    mode: &str,
    recursive: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    // Tags are explicit k=v metadata labels; append mode replaces any existing value for the same key.
    let result = client.set_tags(uri, tags, mode, recursive).await?;
    output_set_tags_result(result, output_format, compact);
    Ok(())
}

pub async fn reindex(
    client: &HttpClient,
    uri: &str,
    mode: &str,
    wait: bool,
    dry_run: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.reindex(uri, mode, wait, dry_run).await?;
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

fn output_set_tags_result(result: Value, output_format: OutputFormat, compact: bool) {
    match output_format {
        OutputFormat::Json => crate::output::output_success(result, output_format, compact),
        OutputFormat::Table => {
            if let Some(rendered) = render_set_tags_result_for_table(&result) {
                println!("{rendered}");
            } else {
                crate::output::output_success(result, output_format, compact);
            }
        }
    }
}

fn render_set_tags_result_for_table(result: &Value) -> Option<String> {
    let obj = result.as_object()?;
    let uri = obj.get("uri")?.as_str()?;

    let tags = obj
        .get("tags")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();

    let updated_uris = obj
        .get("updated_uris")
        .and_then(Value::as_array)
        .map(|items| {
            let unique = items
                .iter()
                .filter_map(Value::as_str)
                .collect::<BTreeSet<_>>();
            let count = unique.len();
            if count == 0 {
                String::new()
            } else if count == 1 {
                unique.into_iter().next().unwrap_or_default().to_string()
            } else {
                let lines = unique
                    .into_iter()
                    .map(|item| format!("- {item}"))
                    .collect::<Vec<_>>()
                    .join("\n");
                format!("{count} updates\n{lines}")
            }
        })
        .unwrap_or_default();

    let display = json!({
        "uri": uri,
        "tags": tags,
        "updated_uris": updated_uris,
        "mode": obj.get("mode").cloned().unwrap_or(Value::Null),
        "success_count": obj.get("success_count").cloned().unwrap_or(Value::Null),
        "skipped_count": obj.get("skipped_count").cloned().unwrap_or(Value::Null),
        "failed_count": obj.get("failed_count").cloned().unwrap_or(Value::Null),
        "tags_updated": obj.get("tags_updated").cloned().unwrap_or(Value::Null),
    });

    crate::output::render_table_with_optional_profile(&display, true)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    fn strip_ansi(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();

        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
                continue;
            }
            output.push(ch);
        }
        output
    }

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
            Some(["content", "", "profile", "line one", "line two", "",].join("\n"))
        );
    }

    #[test]
    fn table_output_renders_set_tags_uri_and_updated_uris() {
        let result = json!({
            "uri": "viking://resources/demo/doc.md",
            "updated_uris": [
                "viking://resources/demo/doc.md",
                "viking://resources/demo/doc.md",
                "viking://resources/demo/doc.md/doc_v2.md"
            ],
            "tags": ["team=test"],
            "mode": "replace",
            "success_count": 3,
            "skipped_count": 0,
            "failed_count": 0,
            "tags_updated": true
        });

        let rendered =
            super::render_set_tags_result_for_table(&result).map(|value| strip_ansi(&value));

        assert_eq!(
            rendered,
            Some(
                [
                    "uri            viking://resources/demo/doc.md",
                    "tags           team=test",
                    "updated_uris   2 updates",
                    "- viking://resources/demo/doc.md",
                    "- viking://resources/demo/doc.md/doc_v2.md",
                    "mode           replace",
                    "success_count  3",
                    "skipped_count  0",
                    "failed_count   0",
                    "tags_updated   true",
                ]
                .join("\n")
                    + "\n"
            )
        );
    }
}
