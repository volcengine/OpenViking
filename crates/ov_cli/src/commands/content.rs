use crate::client::HttpClient;
use crate::error::Result;
use crate::output::OutputFormat;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};

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
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .write(uri, content, mode, wait, timeout, processing_mode)
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

pub async fn get(client: &HttpClient, uri: &str, local_path: Option<&str>) -> Result<()> {
    preflight_explicit_download_target(local_path)?;

    // The body has to arrive before the target can be named: a directory URI
    // comes back as a ZIP, and that decides the `.zip` suffix.
    let (bytes, content_type) = client.get_bytes_with_type(uri).await?;
    let is_zip = content_type
        .as_deref()
        .is_some_and(|value| value.starts_with("application/zip"));

    let path = download_target(uri, local_path, is_zip);
    let display = path.display().to_string();
    if path.exists() {
        return Err(crate::error::Error::Client(format!(
            "File already exists: {display}"
        )));
    }

    // Ensure parent directory exists
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }

    // Write to local file
    let mut file = create_download_target(&path, &display)?;
    file.write_all(&bytes)?;
    file.flush()?;

    println!("Downloaded {} bytes to {}", bytes.len(), display);
    Ok(())
}

/// Resolve where the downloaded bytes go.
///
/// A target that is an existing directory — or omitted, meaning the current
/// directory — receives a file named after the resource, so downloading a
/// folder lands a real `<name>.zip` instead of an extension-less file carrying
/// ZIP bytes. Any other path is used verbatim, which keeps
/// `ov get <uri> ./explicit-name.zip` working as documented.
fn download_target(uri: &str, local_path: Option<&str>, is_zip: bool) -> PathBuf {
    let base = Path::new(local_path.unwrap_or("."));
    if local_path.is_some() && !base.is_dir() {
        return base.to_path_buf();
    }

    let mut name = uri
        .trim_end_matches('/')
        .rsplit('/')
        .find(|segment| !segment.is_empty() && !segment.ends_with(':'))
        .unwrap_or("download")
        .to_string();
    if is_zip && !name.ends_with(".zip") {
        name.push_str(".zip");
    }
    base.join(name)
}

fn preflight_explicit_download_target(local_path: Option<&str>) -> Result<()> {
    let Some(local_path) = local_path else {
        return Ok(());
    };
    let path = Path::new(local_path);
    if path.is_dir() {
        return Ok(());
    }
    match path.symlink_metadata() {
        Ok(_) => Err(crate::error::Error::Client(format!(
            "File already exists: {local_path}"
        ))),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn create_download_target(path: &Path, local_path: &str) -> Result<std::fs::File> {
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                crate::error::Error::Client(format!("File already exists: {local_path}"))
            } else {
                crate::error::Error::Io(error)
            }
        })
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
    fn download_target_names_archives_inside_a_directory() {
        use super::download_target;
        use std::path::PathBuf;

        let dir = tempfile::tempdir().expect("tempdir");
        let dir_path = dir.path().to_str().expect("utf-8 tempdir");

        // Omitted target -> current directory, named after the resource.
        assert_eq!(
            download_target("viking://resources/myfolder", None, true),
            PathBuf::from("./myfolder.zip")
        );
        assert_eq!(
            download_target("viking://resources/logo.png", None, false),
            PathBuf::from("./logo.png")
        );
        // Existing directory -> the archive lands inside it, not over it.
        assert_eq!(
            download_target("viking://resources/myfolder", Some(dir_path), true),
            dir.path().join("myfolder.zip")
        );
        // An explicit non-directory path stays verbatim.
        assert_eq!(
            download_target("viking://resources/myfolder", Some("./explicit.zip"), true),
            PathBuf::from("./explicit.zip")
        );
        // Already-suffixed names are not doubled up.
        assert_eq!(
            download_target("viking://resources/bundle.zip", None, true),
            PathBuf::from("./bundle.zip")
        );
        // Scope-only URIs still produce a usable name.
        assert_eq!(
            download_target("viking://", None, true),
            PathBuf::from("./download.zip")
        );
    }

    #[test]
    fn preflight_rejects_explicit_existing_file_target() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let path = dir.path().join("result.bin");
        std::fs::write(&path, b"existing").expect("fixture should be written");

        let error = super::preflight_explicit_download_target(Some(&path.to_string_lossy()))
            .expect_err("explicit existing file targets must fail before download");

        assert!(matches!(error, Error::Client(_)));
    }

    #[test]
    fn preflight_allows_directory_or_missing_explicit_targets() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let missing_path = dir.path().join("new-result.bin");

        super::preflight_explicit_download_target(None).expect("omitted target is named later");
        super::preflight_explicit_download_target(Some(&missing_path.to_string_lossy()))
            .expect("missing explicit file target is usable");
        super::preflight_explicit_download_target(Some(&dir.path().to_string_lossy()))
            .expect("directory target is named after the response type");
    }

    #[cfg(unix)]
    #[test]
    fn preflight_rejects_broken_symlink_target() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let link_path = dir.path().join("broken-link");
        std::os::unix::fs::symlink(dir.path().join("missing-target"), &link_path)
            .expect("symlink fixture should be created");

        let error = super::preflight_explicit_download_target(Some(&link_path.to_string_lossy()))
            .expect_err("existing symlink targets must fail before download");

        assert!(matches!(error, Error::Client(_)));
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

    #[test]
    fn download_target_creation_does_not_truncate_an_existing_file() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let path = dir.path().join("result.bin");
        std::fs::write(&path, b"existing").expect("fixture should be written");

        let error = super::create_download_target(&path, &path.to_string_lossy())
            .expect_err("existing targets must be rejected atomically");

        assert!(matches!(error, Error::Client(_)));
        assert_eq!(
            std::fs::read(&path).expect("existing file should remain readable"),
            b"existing"
        );
    }
}
