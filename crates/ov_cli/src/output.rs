use serde::Serialize;
use serde_json::{json, Value};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::theme;
use colored::Colorize;

const MAX_COL_WIDTH: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum OutputFormat {
    Table,
    Json,
}

impl From<&str> for OutputFormat {
    fn from(s: &str) -> Self {
        match s {
            "json" => OutputFormat::Json,
            _ => OutputFormat::Table,
        }
    }
}

pub fn output_success<T: Serialize>(result: T, format: OutputFormat, compact: bool) {
    if matches!(format, OutputFormat::Json) {
        if compact {
            println!("{}", compact_success_value(result));
        } else {
            println!(
                "{}",
                serde_json::to_string_pretty(&result).unwrap_or_default()
            );
        }
    } else {
        print_table(result, compact);
    }
}

fn compact_success_value<T: Serialize>(result: T) -> Value {
    let mut obj = match serde_json::to_value(result).unwrap_or(Value::Null) {
        Value::Object(obj) => obj,
        value => return json!({ "ok": true, "result": value }),
    };

    let Some(profile) = obj.remove("profile") else {
        return json!({ "ok": true, "result": Value::Object(obj) });
    };

    let result = if obj.len() == 1 && obj.contains_key("result") {
        obj.remove("result").unwrap_or(Value::Null)
    } else {
        Value::Object(obj)
    };

    if profile.is_null() {
        return json!({ "ok": true, "result": result });
    }

    json!({ "status": "ok", "result": result, "profile": profile })
}

#[allow(dead_code)]
pub fn output_error(code: &str, message: &str, format: OutputFormat, compact: bool) {
    if matches!(format, OutputFormat::Json) && compact {
        eprintln!(
            "{}",
            json!({
                "ok": false,
                "error": {
                    "code": code,
                    "message": message
                }
            })
        );
    } else {
        eprintln!(
            "{}[{}]: {}",
            theme::error("ERROR").bold(),
            theme::error(code),
            theme::body(message)
        );
    }
}

fn print_table<T: Serialize>(result: T, compact: bool) {
    // Convert to json Value for processing
    let value = match serde_json::to_value(&result) {
        Ok(v) => v,
        Err(_) => {
            if compact {
                println!("{}", serde_json::to_string(&result).unwrap_or_default());
            } else {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&result).unwrap_or_default()
                );
            }
            return;
        }
    };

    // Handle string result
    if let Some(s) = value.as_str() {
        println!("{}", theme::body(s));
        return;
    }

    // Handle array of objects
    if let Some(items) = value.as_array() {
        if !items.is_empty() {
            if let Some(table) = format_array_to_table(items, compact) {
                println!("{}", table);
                return;
            }
        } else {
            println!("{}", theme::muted("(empty)"));
            return;
        }
    }

    // Handle object
    if let Some(obj) = value.as_object() {
        if !obj.is_empty() {
            if let Some(rendered) = render_session_context(obj, compact) {
                println!("{}", append_profile_section(rendered, obj));
                return;
            }

            if let Some(rendered) = render_session_archive(obj, compact) {
                println!("{}", append_profile_section(rendered, obj));
                return;
            }

            // Rule 5: ComponentStatus (name + is_healthy + status)
            if obj.contains_key("name")
                && obj.contains_key("is_healthy")
                && obj.contains_key("status")
            {
                let health = if obj["is_healthy"].as_bool().unwrap_or(false) {
                    "healthy"
                } else {
                    "unhealthy"
                };
                let name = obj["name"].as_str().unwrap_or("");
                let status = obj["status"].as_str().unwrap_or("");
                println!(
                    "{}",
                    append_profile_section(render_component_status(name, health, status), obj)
                );
                return;
            }

            // Rule 6: SystemStatus (is_healthy + components)
            if obj.contains_key("components") && obj.contains_key("is_healthy") {
                let mut lines: Vec<String> = Vec::new();
                if let Some(components) = obj["components"].as_object() {
                    for (_key, comp) in components {
                        // Try to render each component as table
                        let comp_table = value_to_table(comp, compact);
                        if let Some(table) = comp_table {
                            lines.push(table);
                            lines.push("".to_string());
                        }
                    }
                }
                let health = if obj["is_healthy"].as_bool().unwrap_or(false) {
                    "healthy"
                } else {
                    "unhealthy"
                };
                lines.push(format!(
                    "{} {}",
                    theme::heading("[system]").bold(),
                    style_health_label(health)
                ));
                if let Some(errors) = obj.get("errors") {
                    if let Some(err_list) = errors.as_array() {
                        let error_strs: Vec<&str> =
                            err_list.iter().filter_map(|e| e.as_str()).collect();
                        if !error_strs.is_empty() {
                            lines.push(format!(
                                "{} {}",
                                theme::error("Errors:").bold(),
                                theme::body(error_strs.join(", "))
                            ));
                        }
                    }
                }
                println!("{}", append_profile_section(lines.join("\n"), obj));
                return;
            }

            if let Some(rendered) = value_to_table_with_profile(&value, compact) {
                println!("{}", rendered);
                return;
            }

            // Extract list fields
            let mut dict_lists: Vec<(String, &Vec<serde_json::Value>)> = Vec::new();
            let mut prim_lists: Vec<(String, &Vec<serde_json::Value>)> = Vec::new();

            for (key, val) in obj {
                if key == "profile" {
                    continue;
                }
                if let Some(arr) = val.as_array() {
                    if !arr.is_empty() {
                        if arr.iter().all(|item| item.is_object()) {
                            dict_lists.push((key.clone(), arr));
                        } else if arr
                            .iter()
                            .all(|item| item.is_string() || item.is_number() || item.is_boolean())
                        {
                            prim_lists.push((key.clone(), arr));
                        }
                    }
                }
            }

            // Rule 3a: single list[primitive] -> one item per line
            if dict_lists.is_empty() && prim_lists.len() == 1 {
                let (key, items) = &prim_lists[0];
                let col = if key.ends_with("es") {
                    key.strip_suffix("es").unwrap_or(key)
                } else if key.ends_with('s') {
                    key.strip_suffix('s').unwrap_or(key)
                } else {
                    key
                };
                let mut rows: Vec<serde_json::Value> = Vec::new();
                for item in *items {
                    let mut row = serde_json::Map::new();
                    row.insert(col.to_string(), item.clone());
                    rows.push(serde_json::Value::Object(row));
                }
                if let Some(table) = format_array_to_table(&rows, compact) {
                    println!("{}", append_profile_section(table, obj));
                    return;
                }
            }

            // Rule 3b: single list[dict] -> render directly
            if dict_lists.len() == 1 && prim_lists.is_empty() {
                let (_key, items) = &dict_lists[0];
                if let Some(table) = format_array_to_table(items, compact) {
                    println!("{}", append_profile_section(table, obj));
                    return;
                }
            }

            // Rule 2: multiple list[dict] -> flatten with type column
            if !dict_lists.is_empty() {
                let mut merged: Vec<serde_json::Value> = Vec::new();
                for (key, items) in &dict_lists {
                    let type_name = if key.ends_with("es") {
                        key.strip_suffix("es").unwrap_or(key)
                    } else if key.ends_with('s') {
                        key.strip_suffix('s').unwrap_or(key)
                    } else {
                        key
                    };
                    for item in *items {
                        if let Some(mut obj) = item.as_object().cloned() {
                            obj.insert(
                                "type".to_string(),
                                serde_json::Value::String(type_name.to_string()),
                            );
                            merged.push(serde_json::Value::Object(obj));
                        }
                    }
                }
                if !merged.is_empty() {
                    if let Some(table) = format_array_to_table(&merged, compact) {
                        println!("{}", append_profile_section(table, obj));
                        return;
                    }
                }
            }

            // Rule 4: plain dict (no expandable lists) -> single-row horizontal table
            if dict_lists.is_empty() && prim_lists.is_empty() {
                // Calculate max key width
                let max_key_width = obj
                    .keys()
                    .map(|k| k.width())
                    .max()
                    .unwrap_or(0)
                    .min(MAX_COL_WIDTH);

                let mut output = String::new();
                for (k, v) in obj {
                    if k == "profile" {
                        continue;
                    }
                    let is_uri = k == "uri";
                    let formatted_value = format_value(v);
                    let (content, _) = truncate_string(&formatted_value, is_uri, MAX_COL_WIDTH);
                    let padded_key = pad_cell(k, max_key_width, false);
                    output.push_str(&format!(
                        "{}  {}\n",
                        theme::muted(padded_key),
                        style_table_value(&content, is_uri)
                    ));
                }
                println!("{}", append_profile_section(output, obj));
                return;
            }
        }
    }

    // Default: JSON output
    if compact {
        println!("{}", serde_json::to_string(&result).unwrap_or_default());
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(&result).unwrap_or_default()
        );
    }
}

fn value_to_table_with_profile(value: &serde_json::Value, compact: bool) -> Option<String> {
    let obj = value.as_object()?;
    let rendered = if let Some(rendered) = value_to_table(value, compact) {
        rendered
    } else {
        let max_key_width = obj
            .keys()
            .filter(|k| k.as_str() != "profile")
            .map(|k| k.width())
            .max()
            .unwrap_or(0)
            .min(MAX_COL_WIDTH);

        let mut output = String::new();
        for (k, v) in obj {
            if k == "profile" {
                continue;
            }
            let is_uri = k == "uri";
            let formatted_value = format_value(v);
            let (content, _) = truncate_string(&formatted_value, is_uri, MAX_COL_WIDTH);
            let padded_key = pad_cell(k, max_key_width, false);
            output.push_str(&format!("{}  {}\n", padded_key, content));
        }
        output
    };
    Some(append_profile_section(rendered, obj))
}

fn append_profile_section(
    rendered: String,
    obj: &serde_json::Map<String, serde_json::Value>,
) -> String {
    let Some(profile) = obj.get("profile").and_then(|v| v.as_array()) else {
        return rendered;
    };
    if profile.is_empty() {
        return rendered;
    }

    let lines: Vec<String> = profile
        .iter()
        .map(|line| match line {
            serde_json::Value::String(s) => s.clone(),
            other => format_value(other),
        })
        .collect();
    if lines.is_empty() {
        return rendered;
    }

    let mut out = rendered.trim_end_matches('\n').to_string();
    out.push_str("\n\nprofile\n");
    out.push_str(&lines.join("\n"));
    out.push('\n');
    out
}

pub fn render_profiled_scalar_result(value: &serde_json::Value) -> Option<String> {
    let obj = value.as_object()?;
    let result = obj.get("result")?.as_str()?;
    Some(append_profile_section(result.to_string(), obj))
}

pub fn append_profile_to_rendered(
    rendered: String,
    value: &serde_json::Value,
) -> String {
    let Some(obj) = value.as_object() else {
        return rendered;
    };
    append_profile_section(rendered, obj)
}

pub fn render_table_with_optional_profile(
    value: &serde_json::Value,
    compact: bool,
) -> Option<String> {
    value_to_table_with_profile(value, compact)
}

fn value_to_table(value: &serde_json::Value, compact: bool) -> Option<String> {
    // Rule 1: list[dict] -> multi-row table
    if let Some(items) = value.as_array() {
        if !items.is_empty() && items.iter().all(|i| i.is_object()) {
            return format_array_to_table(items, compact);
        }
    }

    if let Some(obj) = value.as_object() {
        // ComponentStatus (name + is_healthy + status)
        if obj.contains_key("name") && obj.contains_key("is_healthy") && obj.contains_key("status")
        {
            let health = if obj["is_healthy"].as_bool().unwrap_or(false) {
                "healthy"
            } else {
                "unhealthy"
            };
            let name = obj["name"].as_str().unwrap_or("");
            let status = obj["status"].as_str().unwrap_or("");
            return Some(render_component_status(name, health, status));
        }

        // Extract list fields
        let mut dict_lists: Vec<(String, &Vec<serde_json::Value>)> = Vec::new();
        let mut prim_lists: Vec<(String, &Vec<serde_json::Value>)> = Vec::new();

        for (key, val) in obj {
            if key == "profile" {
                continue;
            }
            if let Some(arr) = val.as_array() {
                if !arr.is_empty() {
                    if arr.iter().all(|item| item.is_object()) {
                        dict_lists.push((key.clone(), arr));
                    } else if arr
                        .iter()
                        .all(|item| item.is_string() || item.is_number() || item.is_boolean())
                    {
                        prim_lists.push((key.clone(), arr));
                    }
                }
            }
        }

        // Rule 3a: single list[primitive] -> one item per line
        if dict_lists.is_empty() && prim_lists.len() == 1 {
            let (key, items) = &prim_lists[0];
            let col = if key.ends_with("es") {
                key.strip_suffix("es").unwrap_or(key)
            } else if key.ends_with('s') {
                key.strip_suffix('s').unwrap_or(key)
            } else {
                key
            };
            let mut rows: Vec<serde_json::Value> = Vec::new();
            for item in *items {
                let mut row = serde_json::Map::new();
                row.insert(col.to_string(), item.clone());
                rows.push(serde_json::Value::Object(row));
            }
            return format_array_to_table(&rows, compact);
        }

        // Rule 3b: single list[dict] -> render directly
        if dict_lists.len() == 1 && prim_lists.is_empty() {
            let (_key, items) = &dict_lists[0];
            return format_array_to_table(items, compact);
        }

        // Rule 2: multiple list[dict] -> flatten with type column
        if !dict_lists.is_empty() {
            let mut merged: Vec<serde_json::Value> = Vec::new();
            for (key, items) in &dict_lists {
                let type_name = if key.ends_with("es") {
                    key.strip_suffix("es").unwrap_or(key)
                } else if key.ends_with('s') {
                    key.strip_suffix('s').unwrap_or(key)
                } else {
                    key
                };
                for item in *items {
                    if let Some(mut obj) = item.as_object().cloned() {
                        obj.insert(
                            "type".to_string(),
                            serde_json::Value::String(type_name.to_string()),
                        );
                        merged.push(serde_json::Value::Object(obj));
                    }
                }
            }
            if !merged.is_empty() {
                return format_array_to_table(&merged, compact);
            }
        }
    }

    None
}

fn render_session_context(
    obj: &serde_json::Map<String, serde_json::Value>,
    compact: bool,
) -> Option<String> {
    if !(obj.contains_key("latest_archive_overview")
        && obj.contains_key("pre_archive_abstracts")
        && obj.contains_key("messages"))
    {
        return None;
    }

    let latest_archive_overview = obj
        .get("latest_archive_overview")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let estimated_tokens = obj
        .get("estimatedTokens")
        .map(format_value)
        .unwrap_or_else(|| "0".to_string());

    let mut lines: Vec<String> = Vec::new();
    lines.push(format!("estimated_tokens       {}", estimated_tokens));

    if let Some(stats) = obj.get("stats").and_then(|v| v.as_object()) {
        lines.push(format!(
            "active_messages        {}",
            obj.get("messages")
                .and_then(|v| v.as_array())
                .map(|items| items.len())
                .unwrap_or(0)
        ));
        lines.push(format!(
            "total_archives         {}",
            stats
                .get("totalArchives")
                .map(format_value)
                .unwrap_or_else(|| "0".to_string())
        ));
        lines.push(format!(
            "included_archives      {}",
            stats
                .get("includedArchives")
                .map(format_value)
                .unwrap_or_else(|| "0".to_string())
        ));
        lines.push(format!(
            "dropped_archives       {}",
            stats
                .get("droppedArchives")
                .map(format_value)
                .unwrap_or_else(|| "0".to_string())
        ));
    }

    lines.push(String::new());
    lines.push("latest_archive_overview".to_string());
    if latest_archive_overview.is_empty() {
        let has_abstracts = obj
            .get("pre_archive_abstracts")
            .and_then(|v| v.as_array())
            .map(|items| !items.is_empty())
            .unwrap_or(false);
        if !has_abstracts {
            lines.push("(none)".to_string());
        } else {
            lines.push("(trimmed by token budget or unavailable)".to_string());
        }
    } else {
        lines.push(latest_archive_overview.to_string());
    }

    if let Some(items) = obj.get("pre_archive_abstracts").and_then(|v| v.as_array()) {
        lines.push(String::new());
        lines.push(format!("pre_archive_abstracts ({})", items.len()));
        if items.is_empty() {
            lines.push("(empty)".to_string());
        } else if let Some(table) = format_array_to_table(items, compact) {
            lines.push(table.trim_end().to_string());
        }
    }

    if let Some(messages) = obj.get("messages").and_then(|v| v.as_array()) {
        lines.push(String::new());
        lines.push(format!("messages ({})", messages.len()));
        if messages.is_empty() {
            lines.push("(empty)".to_string());
        } else {
            let rows = build_message_rows(messages);
            if let Some(table) = format_array_to_table(&rows, compact) {
                lines.push(table.trim_end().to_string());
            }
        }
    }

    Some(lines.join("\n"))
}

fn render_session_archive(
    obj: &serde_json::Map<String, serde_json::Value>,
    compact: bool,
) -> Option<String> {
    if !(obj.contains_key("archive_id")
        && obj.contains_key("overview")
        && obj.contains_key("messages"))
    {
        return None;
    }

    let archive_id = obj.get("archive_id").and_then(|v| v.as_str()).unwrap_or("");
    let abstract_text = obj.get("abstract").and_then(|v| v.as_str()).unwrap_or("");
    let overview = obj.get("overview").and_then(|v| v.as_str()).unwrap_or("");

    let mut lines: Vec<String> = Vec::new();
    lines.push(format!(
        "archive_id             {}",
        if archive_id.is_empty() {
            "(none)"
        } else {
            archive_id
        }
    ));
    lines.push(format!(
        "abstract               {}",
        if abstract_text.is_empty() {
            "(empty)"
        } else {
            abstract_text
        }
    ));

    lines.push(String::new());
    lines.push("overview".to_string());
    lines.push(if overview.is_empty() {
        "(empty)".to_string()
    } else {
        overview.to_string()
    });

    if let Some(messages) = obj.get("messages").and_then(|v| v.as_array()) {
        lines.push(String::new());
        lines.push(format!("messages ({})", messages.len()));
        if messages.is_empty() {
            lines.push("(empty)".to_string());
        } else {
            let rows = build_message_rows(messages);
            if let Some(table) = format_array_to_table(&rows, compact) {
                lines.push(table.trim_end().to_string());
            }
        }
    }

    Some(lines.join("\n"))
}

fn build_message_rows(messages: &[serde_json::Value]) -> Vec<serde_json::Value> {
    let mut rows: Vec<serde_json::Value> = Vec::new();

    for message in messages {
        let Some(obj) = message.as_object() else {
            continue;
        };

        let mut row = serde_json::Map::new();
        row.insert(
            "id".to_string(),
            obj.get("id").cloned().unwrap_or(serde_json::Value::Null),
        );
        row.insert(
            "role".to_string(),
            obj.get("role").cloned().unwrap_or(serde_json::Value::Null),
        );
        row.insert(
            "created_at".to_string(),
            obj.get("created_at")
                .cloned()
                .unwrap_or(serde_json::Value::Null),
        );
        row.insert(
            "content".to_string(),
            serde_json::Value::String(summarize_message_content(
                obj.get("parts").and_then(|v| v.as_array()),
            )),
        );
        rows.push(serde_json::Value::Object(row));
    }

    rows
}

fn summarize_message_content(parts: Option<&Vec<serde_json::Value>>) -> String {
    let Some(parts) = parts else {
        return String::new();
    };

    let mut chunks: Vec<String> = Vec::new();
    for part in parts {
        let Some(obj) = part.as_object() else {
            chunks.push(format_value(part));
            continue;
        };

        let part_type = obj.get("type").and_then(|v| v.as_str()).unwrap_or("");
        match part_type {
            "text" => {
                if let Some(text) = obj.get("text").and_then(|v| v.as_str()) {
                    chunks.push(text.to_string());
                }
            }
            "context" => {
                let abstract_text = obj.get("abstract").and_then(|v| v.as_str()).unwrap_or("");
                chunks.push(if abstract_text.is_empty() {
                    "[context]".to_string()
                } else {
                    format!("[context] {}", abstract_text)
                });
            }
            "tool" => {
                let name = obj
                    .get("tool_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("tool");
                let status = obj
                    .get("tool_status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                chunks.push(if status.is_empty() {
                    format!("[tool:{}]", name)
                } else {
                    format!("[tool:{}:{}]", name, status)
                });
            }
            _ => chunks.push(format_value(part)),
        }
    }

    chunks.join(" | ")
}

struct ColumnInfo {
    max_width: usize,          // Max width for alignment (capped at 120)
    is_numeric: bool,          // True if all values in column are numeric
    is_unbounded_column: bool, // True if column should respect server-side length
}

fn format_array_to_table(items: &Vec<serde_json::Value>, compact: bool) -> Option<String> {
    if items.is_empty() {
        return None;
    }

    // Check if all items are objects
    if !items.iter().all(|i| i.is_object()) {
        // Handle list of primitives
        let mut output = String::new();
        for item in items {
            let (content, _) = truncate_string(&format_value(item), false, MAX_COL_WIDTH);
            output.push_str(&format!("{}\n", theme::body(content)));
        }
        return Some(output);
    }

    // Collect all unique keys
    let mut keys: Vec<String> = Vec::new();
    let mut key_set = std::collections::HashSet::new();

    for item in items {
        if let Some(obj) = item.as_object() {
            for k in obj.keys() {
                if key_set.insert(k.clone()) {
                    keys.push(k.clone());
                }
            }
        }
    }

    if keys.is_empty() {
        return None;
    }

    // Filter out empty columns when compact is true
    let filtered_keys: Vec<String> = if compact {
        keys.iter()
            .filter(|key| {
                items.iter().any(|item| {
                    if let Some(obj) = item.as_object() {
                        if let Some(value) = obj.get(*key) {
                            return !value.is_null()
                                && value != ""
                                && !(value.is_array() && value.as_array().unwrap().is_empty());
                        }
                    }
                    false
                })
            })
            .cloned()
            .collect()
    } else {
        keys.clone()
    };

    if filtered_keys.is_empty() {
        return None;
    }

    let keys = filtered_keys;

    // First pass: analyze columns
    let mut column_info: Vec<ColumnInfo> = Vec::new();

    for key in &keys {
        let is_unbounded_column = key == "uri" || key == "abstract";
        let mut is_numeric = true;
        let mut max_width = key.width(); // Start with header width

        for item in items {
            if let Some(obj) = item.as_object() {
                if let Some(value) = obj.get(key) {
                    let formatted = format_value(value);
                    let display_width = formatted.width();

                    max_width = max_width.max(display_width.min(MAX_COL_WIDTH));

                    // Check if numeric
                    if is_numeric && !is_numeric_value(value) {
                        is_numeric = false;
                    }
                }
            }
        }

        column_info.push(ColumnInfo {
            max_width,
            is_numeric,
            is_unbounded_column,
        });
    }

    // Second pass: format rows
    let mut output = String::new();

    // Header row
    let header_cells: Vec<String> = keys
        .iter()
        .enumerate()
        .map(|(i, k)| {
            theme::heading(pad_cell(k, column_info[i].max_width, false))
                .bold()
                .to_string()
        })
        .collect();
    output.push_str(&header_cells.join("  "));
    output.push('\n');

    // Data rows
    for item in items {
        if let Some(obj) = item.as_object() {
            let row_cells: Vec<String> = keys
                .iter()
                .enumerate()
                .map(|(i, k)| {
                    let info = &column_info[i];
                    let value = obj.get(k).map(|v| format_value(v)).unwrap_or_default();

                    let (content, skip_padding) =
                        truncate_string(&value, info.is_unbounded_column, info.max_width);

                    let padded = if skip_padding {
                        // Long URI, output as-is without padding
                        content
                    } else {
                        // Normal cell, apply padding and alignment
                        pad_cell(&content, info.max_width, info.is_numeric)
                    };

                    style_table_value(&padded, info.is_unbounded_column).to_string()
                })
                .collect();

            output.push_str(&row_cells.join("  "));
            output.push('\n');
        }
    }

    Some(output)
}

fn render_component_status(name: &str, health: &str, status: &str) -> String {
    format!(
        "{} {}\n{}",
        theme::heading(format!("[{name}]")).bold(),
        style_health_label(health),
        theme::body(status)
    )
}

fn style_health_label(value: &str) -> String {
    let styled = match value.to_ascii_lowercase().as_str() {
        "healthy" | "ok" | "true" => theme::success(value).bold(),
        "unhealthy" | "error" | "false" => theme::error(value).bold(),
        _ => theme::warning(value).bold(),
    };
    format!("({styled})")
}

fn style_table_value(value: &str, is_uri: bool) -> String {
    let trimmed = value.trim();
    if is_uri
        || trimmed.starts_with("http://")
        || trimmed.starts_with("https://")
        || trimmed.starts_with("~/")
    {
        return theme::sky_value(value).bold().to_string();
    }

    match trimmed.to_ascii_lowercase().as_str() {
        "healthy" | "ok" | "true" | "success" => theme::success(value).bold().to_string(),
        "unhealthy" | "failed" | "error" | "false" => theme::error(value).bold().to_string(),
        "unknown" | "null" | "(empty)" => theme::muted(value).to_string(),
        _ => theme::body(value).to_string(),
    }
}

fn format_value(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::Bool(b) => b.to_string(),
        serde_json::Value::Null => "null".to_string(),
        _ => v.to_string(),
    }
}

fn pad_cell(content: &str, width: usize, align_right: bool) -> String {
    let display_width = content.width();

    if display_width >= width {
        return content.to_string();
    }

    let padding_needed = width - display_width;
    if align_right {
        format!("{}{}", " ".repeat(padding_needed), content)
    } else {
        format!("{}{}", content, " ".repeat(padding_needed))
    }
}

fn is_numeric_value(v: &serde_json::Value) -> bool {
    match v {
        serde_json::Value::Number(_) => true,
        serde_json::Value::String(s) => s.parse::<f64>().is_ok(),
        _ => false,
    }
}

fn truncate_string(s: &str, is_unbounded: bool, max_width: usize) -> (String, bool) {
    let display_width = s.width();

    // URI/abstract columns: never truncate. For long values, skip padding so
    // the server-side limit (such as --abs-limit) remains authoritative.
    if is_unbounded {
        if display_width > max_width {
            return (s.to_string(), true); // true = skip padding
        } else {
            return (s.to_string(), false);
        }
    }

    // Normal truncation - truncate by display width
    if display_width > MAX_COL_WIDTH {
        let mut current_width = 0;
        let mut truncated = String::new();
        for ch in s.chars() {
            let ch_width = ch.width().unwrap_or(0);
            if current_width + ch_width > MAX_COL_WIDTH - 3 {
                break;
            }
            current_width += ch_width;
            truncated.push(ch);
        }
        (format!("{}...", truncated), false)
    } else {
        (s.to_string(), false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_object_formatting_with_alignment() {
        // Test object with keys of different lengths
        let obj = json!({
            "id": "123",
            "name": "Test Resource",
            "uri": "viking://resources/test",
            "type": "document"
        });

        // This should not panic and should produce aligned output
        // We can't easily capture stdout, but at least verify it doesn't crash
        print_table(obj, true);
    }

    #[test]
    fn test_object_with_long_uri() {
        // Test that long URIs are handled correctly
        let obj = json!({
            "id": "456",
            "uri": "viking://resources/very/long/path/that/exceeds/normal/width/limits/and/should/not/be/truncated/because/it/is/a/uri"
        });

        print_table(obj, true);
    }

    #[test]
    fn test_empty_object() {
        let obj = json!({});
        print_table(obj, true);
    }

    #[test]
    fn test_abstract_column_is_not_truncated_by_cli_renderer() {
        let long_abstract = "a".repeat(MAX_COL_WIDTH + 50);
        let (rendered, skip_padding) = truncate_string(&long_abstract, true, 10);
        assert_eq!(rendered, long_abstract);
        assert!(skip_padding);
    }

    #[test]
    fn test_profile_section_is_preserved_for_table_objects_with_list_payloads() {
        let value = json!({
            "items": [
                {"id": "1", "name": "alpha"},
                {"id": "2", "name": "beta"}
            ],
            "profile": [
                "line one",
                "line two"
            ]
        });

        let rendered = value_to_table_with_profile(&value, true).map(|value| strip_ansi(&value));

        assert_eq!(
            rendered,
            Some(
                [
                    "id  name ",
                    " 1  alpha",
                    " 2  beta ",
                    "",
                    "profile",
                    "line one",
                    "line two",
                    "",
                ]
                .join("\n")
            )
        );
    }

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
    fn test_compact_json_lifts_profile_next_to_result_for_list_payloads() {
        let value = json!({
            "result": [
                {"id": "1", "name": "alpha"}
            ],
            "profile": [
                "line one"
            ]
        });

        let rendered = compact_success_value(value);

        assert_eq!(
            rendered,
            json!({
                "status": "ok",
                "result": [
                    {"id": "1", "name": "alpha"}
                ],
                "profile": [
                    "line one"
                ]
            })
        );
    }

    #[test]
    fn test_compact_json_lifts_profile_next_to_result_for_object_payloads() {
        let value = json!({
            "healthy": true,
            "version": "0.1.x",
            "profile": [
                "line one"
            ]
        });

        let rendered = compact_success_value(value);

        assert_eq!(
            rendered,
            json!({
                "status": "ok",
                "result": {
                    "healthy": true,
                    "version": "0.1.x"
                },
                "profile": [
                    "line one"
                ]
            })
        );
    }

    #[test]
    fn test_compact_json_treats_null_profile_as_absent() {
        let value = json!({
            "result": [
                {"id": "1", "name": "alpha"}
            ],
            "profile": null
        });

        let rendered = compact_success_value(value.clone());

        assert_eq!(
            rendered,
            json!({
                "ok": true,
                "result": [
                    {"id": "1", "name": "alpha"}
                ]
            })
        );
    }

    #[test]
    fn test_profile_section_is_not_duplicated_for_plain_object_output() {
        let value = json!({
            "healthy": true,
            "version": "0.1.x",
            "profile": [
                "line one",
                "line two"
            ]
        });

        let rendered = value_to_table_with_profile(&value, true);

        assert_eq!(
            rendered,
            Some(
                [
                    "healthy  true",
                    "version  0.1.x",
                    "",
                    "profile",
                    "line one",
                    "line two",
                    "",
                ]
                .join("\n")
            )
        );
    }

    #[test]
    fn test_render_profiled_scalar_result_appends_profile_section() {
        let value = json!({
            "result": "content",
            "profile": [
                "line one",
                "line two"
            ]
        });

        let rendered = render_profiled_scalar_result(&value);

        assert_eq!(
            rendered,
            Some(
                [
                    "content",
                    "",
                    "profile",
                    "line one",
                    "line two",
                    "",
                ]
                .join("\n")
            )
        );
    }
}
