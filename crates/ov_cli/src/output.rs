use serde::Serialize;
use serde_json::json;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

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

pub fn output_success<T: Serialize>(result: T, format: OutputFormat, json_output: bool) {
    if json_output || matches!(format, OutputFormat::Json) {
        println!("{}", json!({ "ok": true, "result": result }));
    } else {
        print_table(result);
    }
}

#[allow(dead_code)]
pub fn output_error(code: &str, message: &str, format: OutputFormat, json_output: bool) {
    if json_output || matches!(format, OutputFormat::Json) {
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
        eprintln!("ERROR[{}]: {}", code, message);
    }
}

fn print_table<T: Serialize>(result: T) {
    // Convert to json Value for processing
    let value = match serde_json::to_value(&result) {
        Ok(v) => v,
        Err(_) => {
            println!("{}", serde_json::to_string_pretty(&result).unwrap_or_default());
            return;
        }
    };
    
    // Handle string result
    if let Some(s) = value.as_str() {
        println!("{}", s);
        return;
    }
    
    // Handle array of objects
    if let Some(items) = value.as_array() {
        if !items.is_empty() {
            if let Some(table) = format_array_to_table(items) {
                println!("{}", table);
                return;
            }
        } else {
            println!("(empty)");
            return;
        }
    }
    
    // Handle object
    if let Some(obj) = value.as_object() {
        if !obj.is_empty() {
            // Calculate max key width
            let max_key_width = obj.keys()
                .map(|k| k.width())
                .max()
                .unwrap_or(0)
                .min(120);

            let mut output = String::new();
            for (k, v) in obj {
                let is_uri = k == "uri";
                let formatted_value = format_value(v);
                let (content, _) = truncate_string(&formatted_value, is_uri, 120);
                let padded_key = pad_cell(k, max_key_width, false);
                output.push_str(&format!("{}  {}\n", padded_key, content));
            }
            println!("{}", output);
            return;
        }
    }
    
    // Default: JSON output
    println!("{}", serde_json::to_string_pretty(&result).unwrap_or_default());
}

struct ColumnInfo {
    max_width: usize,      // Max width for alignment (capped at 120)
    is_numeric: bool,      // True if all values in column are numeric
    is_uri_column: bool,   // True if column name is "uri"
}

fn format_array_to_table(items: &Vec<serde_json::Value>) -> Option<String> {
    if items.is_empty() {
        return None;
    }

    // Check if all items are objects
    if !items.iter().all(|i| i.is_object()) {
        // Handle list of primitives
        let mut output = String::new();
        for item in items {
            let (content, _) = truncate_string(&format_value(item), false, 120);
            output.push_str(&format!("{}\n", content));
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

    // First pass: analyze columns
    let mut column_info: Vec<ColumnInfo> = Vec::new();

    for key in &keys {
        let is_uri_column = key == "uri";
        let mut is_numeric = true;
        let mut max_width = key.width(); // Start with header width

        for item in items {
            if let Some(obj) = item.as_object() {
                if let Some(value) = obj.get(key) {
                    let formatted = format_value(value);
                    let display_width = formatted.width();

                    // Update max_width (capped at 120 for alignment calculation)
                    max_width = max_width.max(display_width.min(120));

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
            is_uri_column,
        });
    }

    // Second pass: format rows
    let mut output = String::new();

    // Header row
    let header_cells: Vec<String> = keys.iter()
        .enumerate()
        .map(|(i, k)| pad_cell(k, column_info[i].max_width, false))
        .collect();
    output.push_str(&header_cells.join("  "));
    output.push('\n');

    // Data rows
    for item in items {
        if let Some(obj) = item.as_object() {
            let row_cells: Vec<String> = keys.iter()
                .enumerate()
                .map(|(i, k)| {
                    let info = &column_info[i];
                    let value = obj.get(k)
                        .map(|v| format_value(v))
                        .unwrap_or_default();

                    let (content, skip_padding) = truncate_string(
                        &value,
                        info.is_uri_column,
                        info.max_width
                    );

                    if skip_padding {
                        // Long URI, output as-is without padding
                        content
                    } else {
                        // Normal cell, apply padding and alignment
                        pad_cell(&content, info.max_width, info.is_numeric)
                    }
                })
                .collect();

            output.push_str(&row_cells.join("  "));
            output.push('\n');
        }
    }

    Some(output)
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

fn truncate_string(s: &str, is_uri: bool, max_width: usize) -> (String, bool) {
    const MAX_LEN: usize = 120;
    let display_width = s.width();

    // URI columns: don't truncate if exceeds threshold
    if is_uri && display_width > max_width {
        return (s.to_string(), true); // true = skip padding
    }

    // Normal truncation - truncate by display width
    if display_width > MAX_LEN {
        let mut current_width = 0;
        let mut truncated = String::new();
        for ch in s.chars() {
            let ch_width = ch.width().unwrap_or(0);
            if current_width + ch_width > MAX_LEN - 3 {
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
        print_table(obj);
    }

    #[test]
    fn test_object_with_long_uri() {
        // Test that long URIs are handled correctly
        let obj = json!({
            "id": "456",
            "uri": "viking://resources/very/long/path/that/exceeds/normal/width/limits/and/should/not/be/truncated/because/it/is/a/uri"
        });

        print_table(obj);
    }

    #[test]
    fn test_empty_object() {
        let obj = json!({});
        print_table(obj);
    }
}
