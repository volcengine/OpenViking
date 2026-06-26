use std::io::Write;
use std::path::PathBuf;

use serde_json::{json, Value};

use crate::client::{HttpClient, SnapshotCommitReq, SnapshotRestoreReq, SnapshotShowResult};
use crate::error::Result;
use crate::output::{OutputFormat, output_success};
use crate::SnapshotCmd;

pub async fn dispatch(
    client: &HttpClient,
    cmd: SnapshotCmd,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    match cmd {
        SnapshotCmd::Commit {
            message,
            paths,
            branch,
            author_name,
            author_email,
        } => {
            let req = SnapshotCommitReq {
                message,
                paths,
                branch,
                author_name,
                author_email,
            };
            let value = client.snapshot_commit(&req).await?;
            print_commit(&value, output_format, compact);
            Ok(())
        }
        SnapshotCmd::Restore {
            project_dir,
            source_commit,
            branch,
            dry_run,
            message,
            author_name,
            author_email,
        } => {
            let req = SnapshotRestoreReq {
                project_dir,
                source_commit,
                branch,
                dry_run,
                message,
                author_name,
                author_email,
            };
            let value = client.snapshot_restore(&req).await?;
            print_restore(&value, output_format, compact);
            Ok(())
        }
        SnapshotCmd::Show {
            target_ref,
            path,
            out_path,
        } => {
            let result = client.snapshot_show(&target_ref, path.as_deref()).await?;
            handle_show(result, out_path, output_format, compact)
        }
        SnapshotCmd::Log { branch, limit } => {
            let value = client.snapshot_log(&branch, limit).await?;
            print_log(&value, output_format, compact);
            Ok(())
        }
    }
}

fn print_commit(value: &Value, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Json) {
        output_success(value, output_format, compact);
        return;
    }
    // The server returns the inner result dict (BaseClient unwraps the envelope).
    // It is already a flat object, so hand it to the shared table renderer.
    output_success(value, OutputFormat::Table, compact);
}

fn print_restore(value: &Value, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Json) {
        output_success(value, output_format, compact);
        return;
    }
    // Dry-run shape nests counts under {diff: {to_write, to_delete, unchanged}},
    // which the generic renderer cannot flatten. Reshape it into a flat dict;
    // the applied/noop shapes are already flat and render directly.
    let display = if let Some(diff) = value.get("diff") {
        let count = |key: &str| {
            diff.get(key)
                .and_then(|v| v.as_array())
                .map(|a| a.len())
                .unwrap_or(0)
        };
        json!({
            "result": "dry-run",
            "to_write": count("to_write"),
            "to_delete": count("to_delete"),
            "unchanged": count("unchanged"),
        })
    } else {
        value.clone()
    };
    output_success(&display, OutputFormat::Table, compact);
}

fn handle_show(
    result: SnapshotShowResult,
    out_path: Option<PathBuf>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    match result {
        SnapshotShowResult::Metadata(meta) => {
            if matches!(output_format, OutputFormat::Json) {
                output_success(&meta, output_format, compact);
                return Ok(());
            }
            // Flatten the `parents` array into a scalar so the whole object
            // renders as a single key/value table instead of the renderer
            // collapsing onto just the list field.
            let mut display = serde_json::Map::new();
            for key in ["oid", "tree", "author", "committer"] {
                if let Some(v) = meta.get(key) {
                    display.insert(key.to_string(), v.clone());
                }
            }
            if let Some(parents) = meta.get("parents").and_then(|v| v.as_array()) {
                let names: Vec<String> = parents
                    .iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect();
                display.insert("parents".to_string(), Value::String(names.join(", ")));
            }
            if let Some(msg) = meta.get("message").and_then(|v| v.as_str()) {
                display.insert("message".to_string(), Value::String(msg.to_string()));
            }
            output_success(&Value::Object(display), OutputFormat::Table, compact);
            Ok(())
        }
        SnapshotShowResult::Blob { oid, bytes, size } => {
            if matches!(output_format, OutputFormat::Json) {
                let envelope = serde_json::json!({"oid": oid, "size": size});
                output_success(&envelope, output_format, compact);
                if let Some(path) = out_path {
                    let mut f = std::fs::File::create(&path)?;
                    f.write_all(&bytes)?;
                }
                return Ok(());
            }
            match out_path {
                Some(path) => {
                    let mut f = std::fs::File::create(&path)?;
                    f.write_all(&bytes)?;
                    eprintln!("Wrote {} bytes from {} to {}", size, &oid[..12.min(oid.len())], path.display());
                }
                None => {
                    let mut out = std::io::stdout().lock();
                    out.write_all(&bytes)?;
                    eprintln!("Read {} bytes from {}", size, &oid[..12.min(oid.len())]);
                }
            }
            Ok(())
        }
    }
}

fn print_log(value: &Value, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Json) {
        output_success(value, output_format, compact);
        return;
    }
    // value is the unwrapped "result" — a JSON array of commit entries with
    // nested authors and multi-line messages. Flatten each entry into a row so
    // the shared renderer produces an aligned table like the other commands.
    let entries = value.as_array().cloned().unwrap_or_default();
    let rows: Vec<Value> = entries
        .iter()
        .map(|entry| {
            let oid = entry.get("oid").and_then(|v| v.as_str()).unwrap_or("");
            let short = oid.get(..12).unwrap_or(oid);
            let msg_full = entry.get("message").and_then(|v| v.as_str()).unwrap_or("");
            let subject = msg_full.lines().next().unwrap_or("");
            let author = entry
                .get("author")
                .and_then(|a| a.get("name").or_else(|| a.as_str().map(|_| a)))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            json!({
                "oid": short,
                "author": author,
                "subject": subject,
            })
        })
        .collect();
    output_success(&rows, OutputFormat::Table, compact);
}
