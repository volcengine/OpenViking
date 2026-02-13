use serde::Serialize;
use serde_json::json;
use tabled::{Table, Tabled};

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
            let mut rows: Vec<Vec<String>> = Vec::new();
            for (k, v) in obj {
                rows.push(vec![k.clone(), truncate_string(&format_value(v))]);
            }
            
            let mut output = String::new();
            for row in rows {
                output.push_str(&format!("{}\t{}\n", row[0], row[1]));
            }
            println!("{}", output);
            return;
        }
    }
    
    // Default: JSON output
    println!("{}", serde_json::to_string_pretty(&result).unwrap_or_default());
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
            output.push_str(&format!("{}\n", truncate_string(&format_value(item))));
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
    
    // Create table rows
    let mut rows: Vec<Vec<String>> = Vec::new();
    for item in items {
        if let Some(obj) = item.as_object() {
            let row: Vec<String> = keys
                .iter()
                .map(|k| {
                    obj.get(k)
                        .map(|v| truncate_string(&format_value(v)))
                        .unwrap_or_default()
                })
                .collect();
            rows.push(row);
        }
    }
    
    // Build simple table
    let mut output = String::new();
    
    // Header
    output.push_str(&keys.join("\t"));
    output.push('\n');
    
    // Rows
    for row in rows {
        output.push_str(&row.join("\t"));
        output.push('\n');
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

fn truncate_string(s: &str) -> String {
    const MAX_LEN: usize = 80;
    if s.len() > MAX_LEN {
        format!("{}...", &s[..MAX_LEN - 3])
    } else {
        s.to_string()
    }
}

#[derive(Tabled)]
struct TableRow {
    #[tabled(rename = "Key")]
    key: String,
    #[tabled(rename = "Value")]
    value: String,
}

pub fn print_key_value_table(data: std::collections::HashMap<String, String>) {
    let rows: Vec<TableRow> = data
        .into_iter()
        .map(|(k, v)| TableRow { key: k, value: v })
        .collect();
    
    let table = Table::new(rows);
    println!("{}", table);
}
