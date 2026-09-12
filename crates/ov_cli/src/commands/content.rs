use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;
use serde_json::{Value, json};
use std::collections::BTreeSet;
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
    processing_mode: &str,
    tags: Vec<String>,
    tag_mode: &str,
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
            processing_mode,
            tags,
            tag_mode,
        )
        .await?;
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
    tags: Vec<String>,
    tag_mode: &str,
    recursive: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .reindex(uri, mode, wait, dry_run, tags, tag_mode, recursive)
        .await?;
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
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }

    // Stream into a sibling temp file so memory stays bounded and a partial
    // download never occupies the requested target; publish via hard link so
    // the target appears atomically and an existing target is never replaced.
    // The temp file must live in the target's own directory: link(2) does not
    // cross filesystems.
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent)?;

    let written = client.get_stream(uri, &mut file).await?;
    file.flush()?;

    let display = path.display().to_string();
    file.persist_noclobber(path).map_err(|error| {
        if error.error.kind() == std::io::ErrorKind::AlreadyExists {
            crate::error::Error::Client(format!("File already exists: {display}"))
        } else {
            crate::error::Error::Io(error.error)
        }
    })?;

    println!("Downloaded {written} bytes to {display}");
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
    use crate::error::Error;
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

    enum DownloadBody {
        Complete(Vec<u8>),
        /// Advertise `declared` bytes but send only `sent`, then close: the
        /// client must treat the transfer as failed mid-body.
        Truncated { declared: usize, sent: Vec<u8> },
    }

    async fn spawn_download_server(body: DownloadBody) -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let address = listener
            .local_addr()
            .expect("listener should have an address");
        tokio::spawn(async move {
            let (mut socket, _) = listener
                .accept()
                .await
                .expect("download request should arrive");
            // Drain the request head; a GET with no body arrives in one read.
            let mut buffer = vec![0u8; 8192];
            let _ = socket.read(&mut buffer).await;

            let (declared, sent, truncate) = match body {
                DownloadBody::Complete(bytes) => (bytes.len(), bytes, false),
                DownloadBody::Truncated { declared, sent } => (declared, sent, true),
            };
            let head = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/octet-stream\r\ncontent-length: {declared}\r\nconnection: close\r\n\r\n"
            );
            socket
                .write_all(head.as_bytes())
                .await
                .expect("response head should write");
            socket.write_all(&sent).await.expect("body should write");
            if truncate {
                socket.shutdown().await.expect("socket should shut down");
            }
        });
        format!("http://{address}")
    }

    fn download_client(base_url: String) -> crate::client::HttpClient {
        crate::client::HttpClient::new(base_url, None, None, None, None, 5.0, false, None)
    }

    #[tokio::test]
    async fn get_streams_body_and_publishes_target_atomically() {
        let body: Vec<u8> = (0..300_000u32).map(|i| (i % 251) as u8).collect();
        let base_url = spawn_download_server(DownloadBody::Complete(body.clone())).await;
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let target = dir.path().join("download.bin");

        super::get(
            &download_client(base_url),
            "viking://resources/file.bin",
            &target.to_string_lossy(),
        )
        .await
        .expect("download should succeed");

        assert_eq!(
            std::fs::read(&target).expect("published file should be readable"),
            body
        );
        let entries: Vec<_> = std::fs::read_dir(dir.path())
            .expect("target directory should be listable")
            .map(|entry| entry.expect("entry should be readable"))
            .collect();
        assert_eq!(
            entries.len(),
            1,
            "no temp file should remain after success: {entries:?}"
        );
        assert!(entries[0].path().ends_with("download.bin"));
    }

    #[tokio::test]
    async fn get_interrupted_transfer_leaves_no_target_and_no_partial_files() {
        let base_url = spawn_download_server(DownloadBody::Truncated {
            declared: 4096,
            sent: vec![7u8; 512],
        })
        .await;
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let target = dir.path().join("download.bin");

        let error = super::get(
            &download_client(base_url),
            "viking://resources/file.bin",
            &target.to_string_lossy(),
        )
        .await
        .expect_err("a truncated body must fail the download");

        assert!(
            matches!(
                error,
                Error::Network(_) | Error::Timeout(_) | Error::Parse(_) | Error::Io(_)
            ),
            "unexpected error variant: {error:?}"
        );
        assert!(
            !target.exists(),
            "the requested target must never appear from a failed transfer"
        );
        let residue: Vec<_> = std::fs::read_dir(dir.path())
            .expect("target directory should be listable")
            .collect();
        assert!(
            residue.is_empty(),
            "the partial temp file must be removed on error: {residue:?}"
        );
    }

    #[tokio::test]
    async fn get_refuses_to_overwrite_existing_target() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let target = dir.path().join("download.bin");
        std::fs::write(&target, b"existing").expect("fixture should be written");

        // No server needed: the preflight check rejects before any request.
        let client = download_client("http://127.0.0.1:1".into());
        let error = super::get(
            &client,
            "viking://resources/file.bin",
            &target.to_string_lossy(),
        )
        .await
        .expect_err("existing targets must be rejected");

        assert!(matches!(error, Error::Client(_)));
        assert_eq!(
            std::fs::read(&target).expect("existing file should remain readable"),
            b"existing"
        );
    }
}
