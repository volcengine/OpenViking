use super::render_utils::{append_profile_lines, with_ascii_ellipsis, wrap_display_text};
use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};
use crate::theme;
use colored::Colorize;
use serde_json::Value;
use std::io::IsTerminal;
use unicode_width::UnicodeWidthStr;

const ENTRY_TEXT_WIDTH: usize = 96;
const ENTRY_MIN_TEXT_WIDTH: usize = 32;
const ENTRY_MAX_ABSTRACT_LINES: usize = 2;
const ENTRY_INDENT: &str = "   ";
const TREE_INDENT: &str = "  ";
const TREE_NAME_COLUMN_WIDTH: usize = 38;
const TREE_MIN_NAME_COLUMN_WIDTH: usize = 18;

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
    let result = client
        .ls(
            uri,
            simple,
            recursive,
            output,
            abs_limit,
            show_all_hidden,
            node_limit,
        )
        .await?;
    output_filesystem_entries(&result, output_format, compact, false);
    Ok(())
}

pub async fn tree(
    client: &HttpClient,
    uri: &str,
    output: &str,
    abs_limit: i32,
    show_all_hidden: bool,
    node_limit: i32,
    level_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .tree(
            uri,
            output,
            abs_limit,
            show_all_hidden,
            node_limit,
            level_limit,
        )
        .await?;
    output_filesystem_entries(&result, output_format, compact, true);
    Ok(())
}

fn output_filesystem_entries(
    result: &Value,
    output_format: OutputFormat,
    compact: bool,
    is_tree: bool,
) {
    if let Some(rendered) = render_filesystem_entries_for_table(result, output_format, is_tree) {
        println!("{rendered}");
    } else {
        output_success(result, output_format, compact);
    }
}

fn render_filesystem_entries_for_table(
    value: &Value,
    output_format: OutputFormat,
    is_tree: bool,
) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    if is_tree {
        render_tree_entries_for_table(value)
    } else {
        render_ls_entries_for_table(value)
    }
}

fn render_ls_entries_for_table(value: &Value) -> Option<String> {
    let (entries, profile) = filesystem_entries(value)?;
    let mut lines = Vec::new();
    let text_width = entry_text_width();

    if entries.is_empty() {
        lines.push(theme::muted("(empty)").to_string());
        append_profile_lines(profile, &mut lines);
        return Some(lines.join("\n"));
    }

    for (index, entry) in entries.iter().enumerate() {
        if index > 0 {
            lines.push(String::new());
        }
        render_ls_entry(index + 1, entry, text_width, &mut lines);
    }

    append_profile_lines(profile, &mut lines);
    Some(lines.join("\n"))
}

fn render_tree_entries_for_table(value: &Value) -> Option<String> {
    let (entries, profile) = filesystem_entries(value)?;
    let mut lines = Vec::new();
    let text_width = entry_text_width();

    if entries.is_empty() {
        lines.push(theme::muted("(empty)").to_string());
        append_profile_lines(profile, &mut lines);
        return Some(lines.join("\n"));
    }

    for (index, entry) in entries.iter().enumerate() {
        render_tree_entry(index + 1, entry, text_width, &mut lines);
    }

    append_profile_lines(profile, &mut lines);
    Some(lines.join("\n"))
}

fn filesystem_entries(value: &Value) -> Option<(Vec<&Value>, Option<&Value>)> {
    if let Some(entries) = value.as_array() {
        if entries.iter().all(Value::is_object) {
            return Some((entries.iter().collect(), None));
        }
        return None;
    }

    let object = value.as_object()?;
    let profile = object.get("profile").filter(|profile| !profile.is_null());
    let entries = object.get("result")?.as_array()?;
    if !entries.iter().all(Value::is_object) {
        return None;
    }
    Some((entries.iter().collect(), profile))
}

fn render_ls_entry(rank: usize, entry: &Value, text_width: usize, lines: &mut Vec<String>) {
    let object = entry.as_object();
    let metadata = entry_metadata(object, false);
    lines.push(format!(
        "{}. {}",
        theme::command(rank.to_string()).bold(),
        metadata.join(" · ")
    ));

    if let Some(uri) = entry_string(object, "uri") {
        for line in wrap_display_text(uri, text_width, 2) {
            lines.push(format!("{ENTRY_INDENT}{}", theme::sky_value(line).bold()));
        }
    }

    append_entry_abstract(object, ENTRY_INDENT, text_width, lines);
}

fn render_tree_entry(rank: usize, entry: &Value, text_width: usize, lines: &mut Vec<String>) {
    let object = entry.as_object();
    let rel_path = entry_string(object, "rel_path");
    let path = rel_path
        .or_else(|| entry_string(object, "uri"))
        .unwrap_or("entry");
    let depth = rel_path
        .map(|rel_path| rel_path.matches('/').count())
        .unwrap_or(0);
    let indent = TREE_INDENT.repeat(depth);
    let display_name = path
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or(path);
    let display_name = if entry_is_dir(object) {
        format!("{display_name}/")
    } else {
        display_name.to_string()
    };
    let metadata = tree_metadata(object);

    if rank > 1 && depth == 0 {
        lines.push(String::new());
    }

    lines.push(render_tree_line(
        &indent,
        &display_name,
        &metadata.join("  "),
        text_width,
    ));
}

fn entry_metadata(
    object: Option<&serde_json::Map<String, Value>>,
    compact_directory: bool,
) -> Vec<String> {
    let mut metadata = vec![
        theme::heading(if entry_is_dir(object) { "dir" } else { "file" })
            .bold()
            .to_string(),
    ];

    if !entry_is_dir(object)
        && let Some(size) = object
            .and_then(|object| object.get("size"))
            .and_then(Value::as_u64)
    {
        metadata.push(theme::value(format_size(size)).bold().to_string());
    }

    if !compact_directory || !entry_is_dir(object) {
        if let Some(mod_time) = entry_string(object, "modTime") {
            metadata.push(theme::muted(mod_time).to_string());
        }
    } else if let Some(mod_time) = entry_string(object, "modTime") {
        metadata.push(theme::muted(mod_time).to_string());
    }

    metadata
}

fn append_entry_abstract(
    object: Option<&serde_json::Map<String, Value>>,
    indent: &str,
    text_width: usize,
    lines: &mut Vec<String>,
) {
    let Some(abstract_text) = entry_string(object, "abstract") else {
        return;
    };
    if abstract_text.trim().is_empty() || is_directory_abstract_placeholder(abstract_text) {
        return;
    }

    for line in wrap_display_text(abstract_text, text_width, ENTRY_MAX_ABSTRACT_LINES) {
        lines.push(format!("{indent}{}", theme::body(line)));
    }
}

fn tree_metadata(object: Option<&serde_json::Map<String, Value>>) -> Vec<String> {
    let mut metadata = Vec::new();

    if !entry_is_dir(object)
        && let Some(size) = object
            .and_then(|object| object.get("size"))
            .and_then(Value::as_u64)
    {
        metadata.push(format_size(size));
    }

    if let Some(mod_time) = entry_string(object, "modTime") {
        metadata.push(mod_time.to_string());
    }

    metadata
}

fn render_tree_line(indent: &str, name: &str, metadata: &str, text_width: usize) -> String {
    if metadata.is_empty() {
        return format!("{indent}{}", theme::sky_value(name).bold());
    }

    let available_width = text_width.saturating_sub(indent.width());
    let metadata_width = metadata.width();
    let name_column_width = if available_width > metadata_width + 2 {
        TREE_NAME_COLUMN_WIDTH
            .min(available_width - metadata_width - 2)
            .max(TREE_MIN_NAME_COLUMN_WIDTH.min(available_width))
    } else {
        TREE_MIN_NAME_COLUMN_WIDTH.min(available_width)
    };
    let display_name = fit_display_text(name, name_column_width);
    let padding = name_column_width.saturating_sub(display_name.width()) + 2;

    format!(
        "{indent}{}{}{}",
        theme::sky_value(display_name).bold(),
        " ".repeat(padding),
        theme::muted(metadata)
    )
}

fn fit_display_text(value: &str, width: usize) -> String {
    if value.width() > width {
        with_ascii_ellipsis(value, width)
    } else {
        value.to_string()
    }
}

fn entry_is_dir(object: Option<&serde_json::Map<String, Value>>) -> bool {
    object
        .and_then(|object| object.get("isDir"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn entry_string<'a>(
    object: Option<&'a serde_json::Map<String, Value>>,
    key: &str,
) -> Option<&'a str> {
    object?
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn is_directory_abstract_placeholder(value: &str) -> bool {
    value.contains("[Directory abstract is not ready]")
}

fn format_size(bytes: u64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;

    if bytes < 1024 {
        format!("{bytes} B")
    } else if (bytes as f64) < MB {
        format!("{:.1} KB", bytes as f64 / KB)
    } else if (bytes as f64) < GB {
        format!("{:.1} MB", bytes as f64 / MB)
    } else {
        format!("{:.1} GB", bytes as f64 / GB)
    }
}

fn entry_text_width() -> usize {
    if std::io::stdout().is_terminal()
        && let Ok((columns, _)) = crossterm::terminal::size()
    {
        return usize::from(columns)
            .saturating_sub(ENTRY_INDENT.width())
            .clamp(ENTRY_MIN_TEXT_WIDTH, ENTRY_TEXT_WIDTH);
    }

    ENTRY_TEXT_WIDTH
}

pub async fn mkdir(
    client: &HttpClient,
    uri: &str,
    description: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.mkdir(uri, description).await?;
    output_message_result(
        result,
        format!("Directory created: {}", uri),
        output_format,
        compact,
    );
    Ok(())
}

pub async fn rm(
    client: &HttpClient,
    uri: &str,
    recursive: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.rm(uri, recursive).await?;

    let message = if let Some(count) = result
        .get("estimated_deleted_count")
        .and_then(|v| v.as_u64())
    {
        format!("Removed: {} ({} items)", uri, count)
    } else {
        format!("Removed: {}", uri)
    };

    output_message_result(result, message, output_format, compact);

    Ok(())
}

pub async fn mv(
    client: &HttpClient,
    from_uri: &str,
    to_uri: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.mv(from_uri, to_uri).await?;
    output_message_result(
        result,
        format!("Moved: {} -> {}", from_uri, to_uri),
        output_format,
        compact,
    );
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

fn output_message_result(
    result: serde_json::Value,
    message: String,
    output_format: OutputFormat,
    compact: bool,
) {
    match output_format {
        OutputFormat::Json => output_success(result, output_format, compact),
        OutputFormat::Table => {
            println!(
                "{}",
                crate::output::append_profile_to_rendered(message, &result)
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        render_filesystem_entries_for_table, render_ls_entries_for_table,
        render_tree_entries_for_table,
    };
    use crate::output::render_profiled_scalar_result;
    use serde_json::json;

    #[test]
    fn profiled_filesystem_message_includes_profile_section() {
        let result = json!({
            "result": "Directory created: viking://dir",
            "profile": [
                "mkdir took 1ms"
            ]
        });

        let rendered = render_profiled_scalar_result(&result);

        assert_eq!(
            rendered,
            Some(
                [
                    "Directory created: viking://dir",
                    "",
                    "profile",
                    "mkdir took 1ms",
                    "",
                ]
                .join("\n")
            )
        );
    }

    #[test]
    fn ls_table_output_renders_compact_entry_list() {
        let result = json!([
            {
                "uri": "viking://resources",
                "size": 0,
                "isDir": true,
                "modTime": "2026-05-26",
                "abstract": "The resources directory is a centralized collection of learning and reference materials focused on cloud architecture, AWS best practices, and technical quick-reference content."
            },
            {
                "uri": "viking://user/default/memories",
                "size": 0,
                "isDir": true,
                "modTime": "2026-05-25",
                "abstract": "# viking://user/default/memories [Directory abstract is not ready]"
            }
        ]);

        let rendered = strip_ansi(&render_ls_entries_for_table(&result).expect("ls"));

        assert!(rendered.contains("1. dir · 2026-05-26"));
        assert!(rendered.contains("viking://resources"));
        assert!(rendered.contains("The resources directory is a centralized collection"));
        assert!(rendered.contains("2. dir · 2026-05-25"));
        assert!(!rendered.contains("Directory abstract is not ready"));
        assert!(!rendered.contains("uri  size  isDir"));
        for line in rendered.lines() {
            assert!(
                line.chars().count() < 140,
                "line should not sprawl horizontally: {line}"
            );
        }
    }

    #[test]
    fn tree_table_output_renders_indented_tree() {
        let result = json!([
            {
                "uri": "viking://user/haozhe/memories/entities/sports_event",
                "size": 0,
                "isDir": true,
                "modTime": "2026-05-25",
                "rel_path": "sports_event",
                "abstract": "# viking://user/haozhe/memories/entities/sports_event [Directory abstract is not ready]"
            },
            {
                "uri": "viking://user/haozhe/memories/entities/sports_event/2026_fifa_world_cup.md",
                "size": 1304,
                "isDir": false,
                "modTime": "2026-05-25",
                "rel_path": "sports_event/2026_fifa_world_cup.md",
                "abstract": ""
            },
            {
                "uri": "viking://user/haozhe/memories/entities/program",
                "size": 0,
                "isDir": true,
                "modTime": "2026-05-25",
                "rel_path": "program",
                "abstract": ""
            }
        ]);

        let rendered = strip_ansi(&render_tree_entries_for_table(&result).expect("tree"));

        assert!(rendered.contains("sports_event/"));
        assert!(rendered.contains("  2026_fifa_world_cup.md"));
        assert!(rendered.contains("1.3 KB  2026-05-25"));
        assert!(rendered.contains("program/"));
        assert!(!rendered.contains("1. dir"));
        assert!(!rendered.contains("2. file"));
        assert!(!rendered.contains("Directory abstract is not ready"));
        assert!(!rendered.contains("uri  size  isDir"));
    }

    #[test]
    fn tree_table_output_does_not_indent_uri_fallback_like_a_path() {
        let result = json!([
            {
                "uri": "viking://resources",
                "size": 0,
                "isDir": true,
                "modTime": "2026-05-26",
                "abstract": ""
            }
        ]);

        let rendered = strip_ansi(&render_tree_entries_for_table(&result).expect("tree"));

        assert!(rendered.starts_with("resources/"));
    }

    #[test]
    fn filesystem_renderers_skip_json_output() {
        let result = json!([
            {"uri": "viking://resources", "isDir": true}
        ]);

        assert!(
            render_filesystem_entries_for_table(&result, crate::output::OutputFormat::Json, false)
                .is_none()
        );
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next == 'm' {
                        break;
                    }
                }
            } else {
                output.push(ch);
            }
        }
        output
    }
}
