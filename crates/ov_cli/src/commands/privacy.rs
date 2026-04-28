use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};

fn parse_kv_pairs(pairs: &[String]) -> Result<HashMap<String, String>> {
    let mut parsed: HashMap<String, String> = HashMap::new();
    for pair in pairs {
        let (key, value) = pair.split_once('=').ok_or_else(|| {
            Error::Client(format!("Invalid --key format: {} (expected key=value)", pair))
        })?;
        let key = key.trim();
        if key.is_empty() {
            return Err(Error::Client("--key key cannot be empty".to_string()));
        }
        parsed.insert(key.to_string(), value.to_string());
    }
    Ok(parsed)
}

fn parse_values_json(
    values_json: Option<&str>,
    values_file: Option<&str>,
) -> Result<Option<Map<String, Value>>> {
    let parsed = match (values_json, values_file) {
        (Some(_), Some(_)) => {
            return Err(Error::Client(
                "Specify exactly one of --values-json or --values-file".to_string(),
            ));
        }
        (Some(raw), None) => Some(raw.to_string()),
        (None, Some(path)) => Some(
            std::fs::read_to_string(path)
                .map_err(|e| Error::Client(format!("Failed to read --values-file: {}", e)))?,
        ),
        (None, None) => None,
    };

    let Some(raw) = parsed else {
        return Ok(None);
    };

    let value: Value = serde_json::from_str(&raw)
        .map_err(|e| Error::Client(format!("Invalid JSON for values: {}", e)))?;
    let obj = value
        .as_object()
        .ok_or_else(|| Error::Client("values JSON must be an object".to_string()))?
        .clone();
    Ok(Some(obj))
}

fn to_kv_row(key: &str, value: &Value) -> Value {
    serde_json::json!({
        "key": key,
        "value": match value {
            Value::String(s) => Value::String(s.clone()),
            _ => Value::String(value.to_string()),
        }
    })
}

fn to_kv_rows(map: &Map<String, Value>) -> Vec<Value> {
    let mut items: Vec<(&String, &Value)> = map.iter().collect();
    items.sort_by(|(a, _), (b, _)| a.cmp(b));
    items
        .into_iter()
        .map(|(k, v)| to_kv_row(k, v))
        .collect()
}

fn split_kv_rows_by_keys(map: &Map<String, Value>, selected_keys: &HashSet<String>) -> (Vec<Value>, Vec<Value>) {
    let mut items: Vec<(&String, &Value)> = map.iter().collect();
    items.sort_by(|(a, _), (b, _)| a.cmp(b));

    let mut selected_rows: Vec<Value> = Vec::new();
    let mut other_rows: Vec<Value> = Vec::new();
    for (key, value) in items {
        if selected_keys.contains(key.as_str()) {
            selected_rows.push(to_kv_row(key, value));
        } else {
            other_rows.push(to_kv_row(key, value));
        }
    }
    (selected_rows, other_rows)
}

fn string_width(s: &str) -> usize {
    s.chars().count()
}

fn pad_right(s: &str, width: usize) -> String {
    let w = string_width(s);
    if w >= width {
        return s.to_string();
    }
    format!("{}{}", s, " ".repeat(width - w))
}

fn extract_kv_cells(rows: &[Value]) -> Vec<(String, String)> {
    let mut cells: Vec<(String, String)> = Vec::new();
    for row in rows {
        let Some(obj) = row.as_object() else {
            continue;
        };
        let key = obj
            .get("key")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                _ => v.to_string(),
            })
            .unwrap_or_default();
        let value = obj
            .get("value")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                _ => v.to_string(),
            })
            .unwrap_or_default();
        cells.push((key, value));
    }
    cells
}

fn render_kv_ascii_table(rows: &[Value]) -> String {
    let cells = extract_kv_cells(rows);
    let mut key_width = string_width("key");
    let mut value_width = string_width("value");
    for (k, v) in &cells {
        key_width = key_width.max(string_width(k));
        value_width = value_width.max(string_width(v));
    }

    let border = format!(
        "+{}+{}+",
        "-".repeat(key_width + 2),
        "-".repeat(value_width + 2)
    );

    let mut out = String::new();
    out.push_str(&border);
    out.push('\n');
    out.push_str(&format!(
        "| {} | {} |\n",
        pad_right("key", key_width),
        pad_right("value", value_width)
    ));
    out.push_str(&border);
    out.push('\n');

    for (k, v) in cells {
        out.push_str(&format!(
            "| {} | {} |\n",
            pad_right(&k, key_width),
            pad_right(&v, value_width)
        ));
    }

    out.push_str(&border);
    out
}

fn output_section_table(title: &str, rows: Vec<Value>, _compact: bool) {
    println!("[{}]", title);
    println!("{}", render_kv_ascii_table(&rows));
}

fn output_meta_current_tables(meta: &Map<String, Value>, values: &Map<String, Value>, compact: bool) {
    output_section_table("meta", to_kv_rows(meta), compact);
    println!();
    output_section_table("current", to_kv_rows(values), compact);
}

fn output_version_like_tables(result: &Value, compact: bool) -> Result<()> {
    let mut meta = result
        .as_object()
        .ok_or_else(|| Error::Parse("Expected object response".to_string()))?
        .clone();
    let values = meta
        .remove("values")
        .and_then(|v| v.as_object().cloned())
        .ok_or_else(|| Error::Parse("Expected values object in response".to_string()))?;

    output_meta_current_tables(&meta, &values, compact);
    Ok(())
}

pub async fn categories(client: &HttpClient, output_format: OutputFormat, compact: bool) -> Result<()> {
    let result = client.privacy_list_categories().await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn list_targets(
    client: &HttpClient,
    category: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.privacy_list_targets(category).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn get_current(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.privacy_get_current(category, target_key).await?;
    if matches!(output_format, OutputFormat::Table) {
        let meta = result
            .get("meta")
            .and_then(Value::as_object)
            .ok_or_else(|| Error::Parse("Expected meta object in response".to_string()))?
            .clone();
        let values = result
            .get("current")
            .and_then(|v| v.get("values"))
            .and_then(Value::as_object)
            .ok_or_else(|| Error::Parse("Expected current.values object in response".to_string()))?
            .clone();
        output_meta_current_tables(&meta, &values, compact);
        return Ok(());
    }

    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn list_versions(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.privacy_list_versions(category, target_key).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn get_version(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    version: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.privacy_get_version(category, target_key, version).await?;
    if matches!(output_format, OutputFormat::Table) {
        return output_version_like_tables(&result, compact);
    }

    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn activate(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    version: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.privacy_activate(category, target_key, version).await?;
    if matches!(output_format, OutputFormat::Table) {
        return output_version_like_tables(&result, compact);
    }

    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn upsert(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    values_json: Option<&str>,
    values_file: Option<&str>,
    key_pairs: &[String],
    change_reason: &str,
    labels_json: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let parsed_values = parse_values_json(values_json, values_file)?;
    let mut final_values: Map<String, Value> = parsed_values.clone().unwrap_or_default();

    let key_overrides = parse_kv_pairs(key_pairs)?;
    let mut requested_keys: HashSet<String> = HashSet::new();
    if let Some(values_from_json) = parsed_values.as_ref() {
        requested_keys.extend(values_from_json.keys().cloned());
    }
    requested_keys.extend(key_overrides.keys().cloned());

    if !key_overrides.is_empty() {
        let current = client.privacy_get_current(category, target_key).await?;
        let mut base_values = current
            .get("current")
            .and_then(|v| v.get("values"))
            .and_then(Value::as_object)
            .ok_or_else(|| {
                Error::Parse("privacy get current response missing current.values object".to_string())
            })?
            .clone();

        if let Some(values_from_json) = parsed_values {
            for (key, value) in values_from_json {
                base_values.insert(key, value);
            }
        }
        for (key, value) in key_overrides {
            base_values.insert(key, Value::String(value));
        }
        final_values = base_values;
    }

    if final_values.is_empty() {
        return Err(Error::Client(
            "No values provided; use --values-json/--values-file or --key-{key} <value>"
                .to_string(),
        ));
    }

    let labels_value = if let Some(raw) = labels_json {
        let parsed: Value = serde_json::from_str(raw)
            .map_err(|e| Error::Client(format!("Invalid JSON for --labels-json: {}", e)))?;
        if !parsed.is_object() {
            return Err(Error::Client("--labels-json must be a JSON object".to_string()));
        }
        Some(parsed)
    } else {
        None
    };

    let mut payload = serde_json::json!({
        "values": Value::Object(final_values),
        "change_reason": change_reason,
    });
    if let Some(labels) = labels_value {
        payload["labels"] = labels;
    }

    let result = client.privacy_upsert(category, target_key, &payload).await?;
    if matches!(output_format, OutputFormat::Table) {
        let values = result
            .get("values")
            .and_then(Value::as_object)
            .ok_or_else(|| Error::Parse("Expected values object in upsert response".to_string()))?
            .clone();
        let (updated_rows, unchanged_rows) = split_kv_rows_by_keys(&values, &requested_keys);
        output_section_table("updated", updated_rows, compact);
        println!();
        output_section_table("unchanged", unchanged_rows, compact);
        return Ok(());
    }

    output_success(&result, output_format, compact);
    Ok(())
}
