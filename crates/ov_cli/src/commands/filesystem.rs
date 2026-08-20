use super::render_utils::{append_profile_lines, with_ascii_ellipsis, wrap_display_text};
use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use chrono::{DateTime, Local};
use colored::Colorize;
use serde_json::Value;
use std::collections::HashSet;
use std::io::IsTerminal;
use unicode_width::UnicodeWidthStr;

const ENTRY_TEXT_WIDTH: usize = 96;
const ENTRY_MIN_TEXT_WIDTH: usize = 32;
const ENTRY_MAX_ABSTRACT_LINES: usize = 2;
const ENTRY_INDENT: &str = "   ";
const TREE_INDENT: &str = "  ";
const TREE_NAME_COLUMN_WIDTH: usize = 38;
const TREE_MIN_NAME_COLUMN_WIDTH: usize = 18;
const FIELD_SEP: &str = ",";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FieldAlignment {
    Left,
    Right,
}

#[derive(Debug, Clone)]
struct FieldDef {
    name: &'static str,
    header: &'static str,
    alignment: FieldAlignment,
}

static ALL_FIELDS: &[FieldDef] = &[
    FieldDef { name: "name", header: "NAME", alignment: FieldAlignment::Left },
    FieldDef { name: "uri", header: "URI", alignment: FieldAlignment::Left },
    FieldDef { name: "path", header: "PATH", alignment: FieldAlignment::Left },
    FieldDef { name: "type", header: "TYPE", alignment: FieldAlignment::Left },
    FieldDef { name: "size", header: "SIZE", alignment: FieldAlignment::Right },
    FieldDef { name: "mode", header: "MODE", alignment: FieldAlignment::Left },
    FieldDef { name: "mtime", header: "MTIME", alignment: FieldAlignment::Left },
    FieldDef { name: "locked", header: "LOCKED", alignment: FieldAlignment::Left },
    FieldDef { name: "id", header: "ID", alignment: FieldAlignment::Left },
    FieldDef { name: "count", header: "COUNT", alignment: FieldAlignment::Right },
    FieldDef { name: "tags", header: "TAGS", alignment: FieldAlignment::Left },
    FieldDef { name: "abstract", header: "ABSTRACT", alignment: FieldAlignment::Left },
];

fn resolve_fields(fields: &[String], is_tree: bool) -> Vec<&'static FieldDef> {
    let mut resolved = Vec::new();
    let mut seen = HashSet::new();
    let id_field = if is_tree { "path" } else { "name" };
    let has_identifier = fields.iter().any(|n| {
        let t = n.trim();
        t == "name" || t == "uri" || t == "path"
    });
    if !has_identifier {
        if let Some(def) = ALL_FIELDS.iter().find(|f| f.name == id_field) {
            seen.insert(def.name);
            resolved.push(def);
        }
    }
    for name in fields {
        let trimmed = name.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(def) = ALL_FIELDS.iter().find(|f| f.name == trimmed) {
            if seen.insert(def.name) {
                resolved.push(def);
            }
        }
    }
    resolved
}

fn field_value(entry: &Value, field: &FieldDef) -> String {
    let obj = entry.as_object();
    match field.name {
        "name" => entry_string(obj, "name")
            .or_else(|| {
                let p = entry_string(obj, "rel_path").or_else(|| entry_string(obj, "uri"))?;
                Some(p.trim_end_matches('/').rsplit('/').next().unwrap_or(p))
            })
            .map(|s| {
                if entry_is_dir(obj) {
                    format!("{s}/")
                } else {
                    s.to_string()
                }
            })
            .unwrap_or_else(|| "-".to_string()),
        "uri" => entry_string(obj, "uri").unwrap_or("-").to_string(),
        "path" => entry_string(obj, "rel_path")
            .map(|p| {
                if entry_is_dir(obj) && !p.ends_with('/') {
                    format!("{p}/")
                } else {
                    p.to_string()
                }
            })
            .or_else(|| entry_string(obj, "name").map(|s| s.to_string()))
            .or_else(|| entry_string(obj, "uri").map(|s| s.to_string()))
            .unwrap_or_else(|| "-".to_string()),
        "type" => {
            if entry_is_dir(obj) {
                "dir".to_string()
            } else {
                "file".to_string()
            }
        }
        "size" => obj
            .and_then(|o| o.get("size"))
            .and_then(Value::as_u64)
            .map(format_size)
            .unwrap_or_else(|| "-".to_string()),
        "mode" => obj
            .and_then(|o| o.get("mode"))
            .and_then(Value::as_u64)
            .map(|m| format_mode(m, entry_is_dir(obj)))
            .unwrap_or_else(|| "-".to_string()),
        "mtime" => entry_mod_time(obj).unwrap_or_else(|| "-".to_string()),
        "locked" => obj
            .and_then(|o| o.get("isLocked"))
            .and_then(Value::as_bool)
            .map(|v| if v { "yes".to_string() } else { "no".to_string() })
            .unwrap_or_else(|| "-".to_string()),
        "id" => entry_string(obj, "id")
            .map(str::to_string)
            .unwrap_or_else(|| "-".to_string()),
        "count" => obj
            .and_then(|o| o.get("count"))
            .and_then(Value::as_u64)
            .map(|c| c.to_string())
            .unwrap_or_else(|| "-".to_string()),
        "tags" => obj
            .and_then(|o| o.get("tags"))
            .and_then(Value::as_array)
            .map(|tags| tags.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(","))
            .filter(|tags| !tags.is_empty())
            .unwrap_or_else(|| "-".to_string()),
        "abstract" => entry_string(obj, "abstract")
            .map(|s| {
                if is_directory_abstract_placeholder(s) {
                    "-".to_string()
                } else {
                    s.chars().take(80).collect::<String>()
                }
            })
            .unwrap_or_else(|| "-".to_string()),
        _ => "-".to_string(),
    }
}

fn tree_depth(entry: &Value) -> usize {
    entry
        .as_object()
        .and_then(|o| o.get("rel_path"))
        .and_then(Value::as_str)
        .map(|p| p.matches('/').count())
        .unwrap_or(0)
}

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
    fields: Option<Vec<String>>,
    tags: &[String],
) -> Result<()> {
    let extra = extra_fields_from(&fields);
    // When fields are requested we need entry objects (not URI strings) regardless of --simple.
    let api_simple = simple && fields.is_none();
    let result = client
        .ls(
            uri,
            api_simple,
            recursive,
            output,
            abs_limit,
            show_all_hidden,
            node_limit,
            &extra,
            tags,
            fields.as_ref().is_some_and(|items| items.iter().any(|item| item == "tags")) || !tags.is_empty(),
        )
        .await?;
    output_filesystem_entries(&result, output_format, compact, false, simple, fields.as_deref());
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
    simple: bool,
    fields: Option<Vec<String>>,
    tags: &[String],
) -> Result<()> {
    let extra = extra_fields_from(&fields);
    let result = client
        .tree(
            uri,
            output,
            abs_limit,
            show_all_hidden,
            node_limit,
            level_limit,
            &extra,
            tags,
            fields.as_ref().is_some_and(|items| items.iter().any(|item| item == "tags")) || !tags.is_empty(),
        )
        .await?;
    output_filesystem_entries(&result, output_format, compact, true, simple, fields.as_deref());
    Ok(())
}

fn extra_fields_from(fields: &Option<Vec<String>>) -> Vec<String> {
    let Some(fields) = fields else {
        return Vec::new();
    };
    let mut extra = Vec::new();
    for f in fields {
        let key = match f.as_str() {
            "locked" => Some("locked"),
            "id" => Some("id"),
            "count" => Some("count"),
            _ => None,
        };
        if let Some(k) = key {
            if !extra.iter().any(|e: &String| e == k) {
                extra.push(k.to_string());
            }
        }
    }
    extra
}

fn output_filesystem_entries(
    result: &Value,
    output_format: OutputFormat,
    compact: bool,
    is_tree: bool,
    simple: bool,
    fields: Option<&[String]>,
) {
    match output_format {
        OutputFormat::Json => {
            output_success(result, output_format, compact);
        }
        OutputFormat::Table => {
            if let Some(fields) = fields {
                let defs = resolve_fields(fields, is_tree);
                if defs.is_empty() {
                    output_success(result, output_format, compact);
                    return;
                }
                if simple {
                    if let Some(rendered) = render_simple_fields(result, &defs, is_tree) {
                        println!("{rendered}");
                    } else {
                        output_success(result, output_format, compact);
                    }
                } else {
                    render_fields_table(result, &defs, is_tree);
                }
            } else if let Some(rendered) =
                render_filesystem_entries_for_table(result, output_format, is_tree, simple)
            {
                println!("{rendered}");
            } else {
                output_success(result, output_format, compact);
            }
        }
    }
}

/// Print one URI per line from a glob-style `{matches:[...],count:N}` result (URI strings).
pub fn print_uri_blob_per_line(result: &Value) {
    let Some(matches) = result.get("result").and_then(|v| v.get("matches")).or_else(|| result.get("matches")) else {
        return;
    };
    let Some(arr) = matches.as_array() else { return };
    for item in arr {
        if let Some(s) = item.as_str() {
            println!("{s}");
        }
    }
}

pub fn output_entry_list(
    result: &Value,
    output_format: OutputFormat,
    compact: bool,
    simple: bool,
    fields: Option<&[String]>,
) {
    output_filesystem_entries(result, output_format, compact, false, simple, fields);
}

fn render_simple_fields(
    result: &Value,
    fields: &[&FieldDef],
    is_tree: bool,
) -> Option<String> {
    let entries = extract_entries(result)?;
    let mut lines = Vec::with_capacity(entries.len());
    for entry in entries {
        let vals: Vec<String> = fields
            .iter()
            .map(|f| {
                let mut v = field_value(entry, f);
                if f.name == "name" && is_tree {
                    let depth = tree_depth(entry);
                    let indent = TREE_INDENT.repeat(depth);
                    v = format!("{indent}{v}");
                }
                v
            })
            .collect();
        lines.push(vals.join(FIELD_SEP));
    }
    Some(lines.join("\n"))
}

fn render_simple_tree_paths(result: &Value) -> Option<String> {
    let path = ALL_FIELDS.iter().find(|field| field.name == "path")?;
    render_simple_fields(result, &[path], false)
}

fn render_fields_table(result: &Value, fields: &[&FieldDef], is_tree: bool) {
    let (entries, profile) = match filesystem_entries(result) {
        Some(v) => v,
        None => {
            output_success(result, OutputFormat::Json, false);
            return;
        }
    };
    let mut lines = Vec::new();
    if entries.is_empty() {
        lines.push(theme::muted("(empty)").to_string());
        append_profile_lines(profile, &mut lines);
        println!("{}", lines.join("\n"));
        return;
    }

    let mut rows: Vec<Vec<String>> = Vec::with_capacity(entries.len());
    for entry in entries {
        let mut row: Vec<String> = fields
            .iter()
            .map(|f| {
                let mut v = field_value(entry, f);
                if f.name == "name" && is_tree {
                    let depth = tree_depth(entry);
                    let indent = TREE_INDENT.repeat(depth);
                    v = format!("{indent}{v}");
                }
                v
            })
            .collect();
        if is_tree {
            if let Some(path_field_idx) = fields.iter().position(|f| f.name == "path") {
                let depth = tree_depth(entry);
                let indent = TREE_INDENT.repeat(depth);
                let val = &row[path_field_idx];
                row[path_field_idx] = format!("{indent}{val}");
            }
        }
        rows.push(row);
    }

    let headers: Vec<String> = fields.iter().map(|f| f.header.to_string()).collect();
    let mut col_widths: Vec<usize> = headers.iter().map(|h| h.width()).collect();
    for row in &rows {
        for (i, cell) in row.iter().enumerate() {
            let w = display_width(cell);
            if i < col_widths.len() && w > col_widths[i] {
                col_widths[i] = w;
            }
        }
    }

    let header_line = headers
        .iter()
        .enumerate()
        .map(|(i, h)| pad_cell(h, col_widths[i], fields[i].alignment == FieldAlignment::Right))
        .collect::<Vec<_>>()
        .join("  ");
    lines.push(theme::heading(header_line).bold().to_string());

    for row in &rows {
        let line = row
            .iter()
            .enumerate()
            .map(|(i, cell)| {
                let content = fit_display_text(cell, col_widths[i]);
                pad_cell(
                    &content,
                    col_widths[i],
                    fields[i].alignment == FieldAlignment::Right,
                )
            })
            .collect::<Vec<_>>()
            .join("  ");
        lines.push(line);
    }

    append_profile_lines(profile, &mut lines);
    println!("{}", lines.join("\n"));
}

fn display_width(s: &str) -> usize {
    s.width()
}

fn extract_entries(value: &Value) -> Option<Vec<&Value>> {
    if let Some(arr) = value.as_array() {
        if arr.iter().all(Value::is_object) {
            return Some(arr.iter().collect());
        }
        return None;
    }
    let obj = value.as_object()?;
    // glob shape: top-level {matches:[...], count:N} (unwrapped from the success envelope
    // as a result object, with no nested "result" key).
    if let Some(matches) = obj.get("matches") {
        if let Some(arr) = matches.as_array() {
            if arr.iter().all(Value::is_object) {
                return Some(arr.iter().collect());
            }
        }
    }
    let result = obj.get("result")?;
    if let Some(arr) = result.as_array() {
        if arr.iter().all(Value::is_object) {
            return Some(arr.iter().collect());
        }
    }
    if let Some(obj_res) = result.as_object() {
        if let Some(matches) = obj_res.get("matches") {
            if let Some(arr) = matches.as_array() {
                if arr.iter().all(Value::is_object) {
                    return Some(arr.iter().collect());
                }
            }
        }
    }
    None
}

fn render_filesystem_entries_for_table(
    value: &Value,
    output_format: OutputFormat,
    is_tree: bool,
    simple: bool,
) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    if is_tree {
        if simple {
            render_simple_tree_paths(value)
        } else {
            render_tree_entries_for_table(value)
        }
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
    // glob shape: top-level {matches:[...], count:N}
    if let Some(matches) = object.get("matches") {
        if let Some(arr) = matches.as_array() {
            if arr.iter().all(Value::is_object) {
                return Some((arr.iter().collect(), profile));
            }
        }
    }
    let result = object.get("result")?;
    if let Some(arr) = result.as_array() {
        if arr.iter().all(Value::is_object) {
            return Some((arr.iter().collect(), profile));
        }
    }
    if let Some(obj_res) = result.as_object() {
        if let Some(matches) = obj_res.get("matches") {
            if let Some(arr) = matches.as_array() {
                if arr.iter().all(Value::is_object) {
                    return Some((arr.iter().collect(), profile));
                }
            }
        }
    }
    None
}

fn render_ls_entry(rank: usize, entry: &Value, text_width: usize, lines: &mut Vec<String>) {
    let object = entry.as_object();
    let metadata = entry_metadata(object);
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

fn entry_metadata(object: Option<&serde_json::Map<String, Value>>) -> Vec<String> {
    let mut metadata = vec![
        theme::heading(if entry_is_dir(object) { "dir" } else { "file" })
            .bold()
            .to_string(),
    ];

    if entry_access_denied(object) {
        metadata.push(theme::warning("permission denied").bold().to_string());
        return metadata;
    }

    if !entry_is_dir(object)
        && let Some(size) = object
            .and_then(|object| object.get("size"))
            .and_then(Value::as_u64)
    {
        metadata.push(theme::value(format_size(size)).bold().to_string());
    }

    if let Some(mod_time) = entry_mod_time(object) {
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

    if entry_access_denied(object) {
        metadata.push("permission denied".to_string());
        return metadata;
    }

    if !entry_is_dir(object)
        && let Some(size) = object
            .and_then(|object| object.get("size"))
            .and_then(Value::as_u64)
    {
        metadata.push(format_size(size));
    }

    if let Some(mod_time) = entry_mod_time(object) {
        metadata.push(mod_time);
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

fn entry_access_denied(object: Option<&serde_json::Map<String, Value>>) -> bool {
    object
        .and_then(|object| object.get("access"))
        .and_then(Value::as_str)
        == Some("denied")
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

fn entry_mod_time(object: Option<&serde_json::Map<String, Value>>) -> Option<String> {
    entry_string(object, "modTime").map(format_mod_time_for_display)
}

fn format_mod_time_for_display(value: &str) -> String {
    // Agent output already contains compact dates/times; leave those unchanged.
    DateTime::parse_from_rfc3339(value)
        .map(|dt| {
            dt.with_timezone(&Local)
                .format("%Y-%m-%d %H:%M")
                .to_string()
        })
        .unwrap_or_else(|_| value.to_string())
}

fn is_directory_abstract_placeholder(value: &str) -> bool {
    value.contains("[Directory abstract is not ready]")
        || value.contains("[.abstract.md is not ready]")
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

fn format_mode(mode: u64, is_dir: bool) -> String {
    let file_type = if is_dir { 'd' } else { '-' };
    let perm = |read: bool, write: bool, execute: bool| -> String {
        format!(
            "{}{}{}",
            if read { 'r' } else { '-' },
            if write { 'w' } else { '-' },
            if execute { 'x' } else { '-' }
        )
    };
    let owner = perm(mode & 0o400 != 0, mode & 0o200 != 0, mode & 0o100 != 0);
    let group = perm(mode & 0o040 != 0, mode & 0o020 != 0, mode & 0o010 != 0);
    let other = perm(mode & 0o004 != 0, mode & 0o002 != 0, mode & 0o001 != 0);
    format!("{file_type}{owner}{group}{other}")
}

fn pad_cell(content: &str, width: usize, align_right: bool) -> String {
    let visible = display_width(content);
    let pad = width.saturating_sub(visible);
    if align_right {
        format!("{}{content}", " ".repeat(pad))
    } else {
        format!("{content}{}", " ".repeat(pad))
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
    wait: bool,
    timeout: Option<f64>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.rm(uri, recursive, wait, timeout).await?;

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

pub async fn cp(
    client: &HttpClient,
    from_uri: &str,
    to_uri: &str,
    recursive: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.cp(from_uri, to_uri, recursive).await?;
    output_message_result(
        result,
        format!("Copied: {} -> {}", from_uri, to_uri),
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

pub async fn attrs(
    client: &HttpClient,
    uri: &str,
    key: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let mut result = client.attrs(uri).await?;
    if let Some(key) = key {
        result = select_attr_key(&result, key)
            .cloned()
            .ok_or_else(|| Error::Client(format!("Attribute not found: {key}")))?;
    }
    output_success(&result, output_format, compact);
    Ok(())
}

fn select_attr_key<'a>(result: &'a Value, key: &str) -> Option<&'a Value> {
    let mut current = result.get("attrs")?;
    for part in key.strip_prefix("attrs.").unwrap_or(key).split('.') {
        current = current.get(part)?;
    }
    Some(current)
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
        render_filesystem_entries_for_table, render_ls_entries_for_table, render_simple_fields,
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
            },
            {
                "uri": "viking://resources/restricted",
                "isDir": true,
                "access": "denied"
            }
        ]);

        let rendered = strip_ansi(&render_ls_entries_for_table(&result).expect("ls"));

        assert!(rendered.contains("1. dir · 2026-05-26"));
        assert!(rendered.contains("viking://resources"));
        assert!(rendered.contains("The resources directory is a centralized collection"));
        assert!(rendered.contains("2. dir · 2026-05-25"));
        assert!(rendered.contains("3. dir · permission denied"));
        assert!(rendered.contains("viking://resources/restricted"));
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
    fn ls_table_output_renders_original_iso_modtime_in_local_timezone() {
        let _tz = ScopedEnvVar::set("TZ", "Asia/Singapore");
        let result = json!([
            {
                "uri": "viking://resources/hermes-agent/CONTRIBUTING.md",
                "size": 44394,
                "isDir": false,
                "modTime": "2026-06-09T07:47:22Z",
                "abstract": ""
            }
        ]);

        let rendered = strip_ansi(&render_ls_entries_for_table(&result).expect("ls"));

        assert!(rendered.contains("1. file · 43.4 KB · 2026-06-09 15:47"));
        assert!(!rendered.contains("2026-06-09T07:47:22Z"));
    }

    #[test]
    fn ls_table_output_renders_compact_raw_modtime_in_local_timezone() {
        let _tz = ScopedEnvVar::set("TZ", "Asia/Singapore");
        let result = json!([
            {
                "uri": "viking://resources/hermes-agent/CONTRIBUTING.md",
                "size": 44394,
                "isDir": false,
                "modTime": "2026-06-10T16:30:17Z",
                "abstract": ""
            }
        ]);

        let rendered = strip_ansi(&render_ls_entries_for_table(&result).expect("ls"));

        assert!(rendered.contains("1. file · 43.4 KB · 2026-06-11 00:30"));
        assert!(!rendered.contains("2026-06-10T16:30:17Z"));
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
            },
            {
                "uri": "viking://user/haozhe/memories/entities/restricted",
                "isDir": true,
                "rel_path": "restricted",
                "access": "denied"
            }
        ]);

        let rendered = strip_ansi(&render_tree_entries_for_table(&result).expect("tree"));

        assert!(rendered.contains("sports_event/"));
        assert!(rendered.contains("  2026_fifa_world_cup.md"));
        assert!(rendered.contains("1.3 KB  2026-05-25"));
        assert!(rendered.contains("program/"));
        assert!(rendered.contains("restricted/"));
        assert!(rendered.contains("permission denied"));
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
            render_filesystem_entries_for_table(&result, crate::output::OutputFormat::Json, false, false)
                .is_none()
        );
    }

    #[test]
    fn tree_simple_without_fields_renders_one_path_per_line() {
        let result = json!([
            {"rel_path": "docs", "isDir": true},
            {"rel_path": "docs/readme.md", "isDir": false}
        ]);

        let rendered = render_filesystem_entries_for_table(
            &result,
            crate::output::OutputFormat::Table,
            true,
            true,
        );

        assert_eq!(rendered.as_deref(), Some("docs/\ndocs/readme.md"));
    }

    #[test]
    fn simple_id_field_keeps_complete_record_id() {
        let record_id = "0123456789abcdef0123456789abcdef";
        let result = json!([{"name": "readme.md", "id": record_id, "isDir": false}]);
        let fields = vec!["id".to_string()];
        let defs = super::resolve_fields(&fields, false);

        let rendered = render_simple_fields(&result, &defs, false);
        let expected = format!("readme.md,{record_id}");

        assert_eq!(rendered.as_deref(), Some(expected.as_str()));
        assert!(!rendered.expect("simple fields").contains(".."));
    }

    #[test]
    fn table_id_field_keeps_complete_record_id() {
        let record_id = "0123456789abcdef0123456789abcdef";
        let entry = json!({"name": "readme.md", "id": record_id, "isDir": false});
        let id_field = super::ALL_FIELDS
            .iter()
            .find(|field| field.name == "id")
            .expect("id field");

        assert_eq!(super::field_value(&entry, id_field), record_id);
    }

    #[test]
    fn format_mode_formats_permissions() {
        assert_eq!(super::format_mode(0o40755, true), "drwxr-xr-x");
        assert_eq!(super::format_mode(0o100644, false), "-rw-r--r--");
        assert_eq!(super::format_mode(0o100755, false), "-rwxr-xr-x");
        // AGFS omits S_IFMT bits; isDir flag drives the file-type char
        assert_eq!(super::format_mode(0o755, true), "drwxr-xr-x");
        assert_eq!(super::format_mode(0o644, false), "-rw-r--r--");
    }

    #[test]
    fn resolve_fields_prepends_name_when_no_identifier_given_ls() {
        let fields = vec!["size".to_string(), "mtime".to_string()];
        let defs = super::resolve_fields(&fields, false);
        let names: Vec<&str> = defs.iter().map(|d| d.name).collect();
        assert_eq!(names, vec!["name", "size", "mtime"]);
    }

    #[test]
    fn resolve_fields_prepends_path_when_no_identifier_given_tree() {
        let fields = vec!["size".to_string()];
        let defs = super::resolve_fields(&fields, true);
        let names: Vec<&str> = defs.iter().map(|d| d.name).collect();
        assert_eq!(names, vec!["path", "size"]);
    }

    #[test]
    fn resolve_fields_keeps_user_provided_identifier() {
        let fields = vec!["uri".to_string(), "size".to_string()];
        let defs = super::resolve_fields(&fields, false);
        let names: Vec<&str> = defs.iter().map(|d| d.name).collect();
        assert_eq!(names, vec!["uri", "size"]);
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

    struct ScopedEnvVar {
        key: &'static str,
        previous: Option<String>,
    }

    impl ScopedEnvVar {
        fn set(key: &'static str, value: &str) -> Self {
            let previous = std::env::var(key).ok();
            unsafe {
                std::env::set_var(key, value);
            }
            Self { key, previous }
        }
    }

    impl Drop for ScopedEnvVar {
        fn drop(&mut self) {
            unsafe {
                if let Some(previous) = &self.previous {
                    std::env::set_var(self.key, previous);
                } else {
                    std::env::remove_var(self.key);
                }
            }
        }
    }
}
