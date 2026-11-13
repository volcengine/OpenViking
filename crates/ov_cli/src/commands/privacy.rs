use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::io::{self, IsTerminal, Write};

const TREE_INDENT: &str = "  ";

fn render_privacy_tree(result: &Value, references: bool) -> String {
    let Some(categories) = result
        .get("categories")
        .and_then(Value::as_array)
        .filter(|categories| !categories.is_empty())
    else {
        return theme::muted("(empty)").to_string();
    };
    let mut lines = vec![theme::heading("viking://~/privacy").to_string()];
    for category in categories {
        lines.push(format!(
            "{TREE_INDENT}{}",
            theme::heading(category["category"].as_str().unwrap_or_default())
        ));
        for target in category
            .get("targets")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            lines.push(format!(
                "{}{}  {}",
                TREE_INDENT.repeat(2),
                theme::sky_value(target["target_key"].as_str().unwrap_or_default()),
                theme::muted(format!("(v{})", target["version"]))
            ));
            for field in target
                .get("fields")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let status = if field["configured"].as_bool().unwrap_or(false) {
                    theme::success("configured")
                } else {
                    theme::warning("unconfigured")
                };
                lines.push(format!(
                    "{}{}  {}",
                    TREE_INDENT.repeat(3),
                    theme::sky_value(field["field"].as_str().unwrap_or_default()),
                    status
                ));
                if references && let Some(reference) = field["reference"].as_str() {
                    lines.push(format!(
                        "{}{}",
                        TREE_INDENT.repeat(4),
                        theme::muted(reference)
                    ));
                }
            }
        }
    }
    lines.join("\n")
}

pub async fn tree(
    client: &HttpClient,
    category: Option<&str>,
    references: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.privacy_tree(category).await?;
    if output_format == OutputFormat::Json {
        output_success(&result, OutputFormat::Json, compact);
    } else {
        println!(
            "{}",
            crate::output::append_profile_to_rendered(
                render_privacy_tree(&result, references),
                &result
            )
        );
    }
    Ok(())
}

fn parse_field_pairs(pairs: &[String]) -> Result<HashMap<String, String>> {
    let mut parsed: HashMap<String, String> = HashMap::new();
    for pair in pairs {
        let (field, value) = pair.split_once('=').ok_or_else(|| {
            Error::Client(format!(
                "Invalid --field format: {} (expected field=value)",
                pair
            ))
        })?;
        let field = field.trim();
        if field.is_empty() {
            return Err(Error::Client("--field name cannot be empty".to_string()));
        }
        parsed.insert(field.to_string(), value.to_string());
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
        "field": key,
        "value": match value {
            Value::String(s) => Value::String(s.clone()),
            _ => Value::String(value.to_string()),
        }
    })
}

fn to_kv_rows(map: &Map<String, Value>) -> Vec<Value> {
    let mut items: Vec<(&String, &Value)> = map.iter().collect();
    items.sort_by(|(a, _), (b, _)| a.cmp(b));
    items.into_iter().map(|(k, v)| to_kv_row(k, v)).collect()
}

fn split_kv_rows_by_keys(
    map: &Map<String, Value>,
    selected_keys: &HashSet<String>,
) -> (Vec<Value>, Vec<Value>) {
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
        let field = obj
            .get("field")
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
        cells.push((field, value));
    }
    cells
}

fn render_kv_ascii_table(rows: &[Value]) -> String {
    let cells = extract_kv_cells(rows);
    let mut field_width = string_width("field");
    let mut value_width = string_width("value");
    for (k, v) in &cells {
        field_width = field_width.max(string_width(k));
        value_width = value_width.max(string_width(v));
    }

    let border = format!(
        "+{}+{}+",
        "-".repeat(field_width + 2),
        "-".repeat(value_width + 2)
    );

    let mut out = String::new();
    out.push_str(&border);
    out.push('\n');
    out.push_str(&format!(
        "| {} | {} |\n",
        pad_right("field", field_width),
        pad_right("value", value_width)
    ));
    out.push_str(&border);
    out.push('\n');

    for (k, v) in cells {
        out.push_str(&format!(
            "| {} | {} |\n",
            pad_right(&k, field_width),
            pad_right(&v, value_width)
        ));
    }

    out.push_str(&border);
    out
}

fn render_section_table(title: &str, rows: Vec<Value>) -> String {
    format!("[{}]\n{}", title, render_kv_ascii_table(&rows))
}

fn render_meta_current_tables(meta: &Map<String, Value>, values: &Map<String, Value>) -> String {
    format!(
        "{}\n\n{}",
        render_section_table("meta", to_kv_rows(meta)),
        render_section_table("current", to_kv_rows(values))
    )
}

fn output_version_like_tables(result: &Value, _compact: bool) -> Result<()> {
    let mut meta = result
        .as_object()
        .ok_or_else(|| Error::Parse("Expected object response".to_string()))?
        .clone();
    let values = meta
        .remove("values")
        .and_then(|v| v.as_object().cloned())
        .ok_or_else(|| Error::Parse("Expected values object in response".to_string()))?;

    let rendered = render_meta_current_tables(&meta, &values);
    println!(
        "{}",
        crate::output::append_profile_to_rendered(rendered, result)
    );
    Ok(())
}

pub async fn categories(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
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
        let rendered = render_meta_current_tables(&meta, &values);
        println!(
            "{}",
            crate::output::append_profile_to_rendered(rendered, &result)
        );
        return Ok(());
    }

    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn reference(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    field: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let field = infer_field(client, "reference", category, target_key, field).await?;
    let result = client
        .privacy_get_field(category, target_key, &field, false)
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn resolve(
    client: &HttpClient,
    selector_or_category: &str,
    target_key: Option<&str>,
    field: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = match (target_key, field) {
        (None, None) => client.privacy_resolve_selector(selector_or_category).await?,
        (Some(target_key), field) => {
            let field =
                infer_field(client, "resolve", selector_or_category, target_key, field).await?;
            client
                .privacy_get_field(selector_or_category, target_key, &field, true)
                .await?
        }
        (None, Some(_)) => {
            return Err(Error::Client(
                "Resolve accepts either one reference/field URI or category target [field]."
                    .to_string(),
            ));
        }
    };
    output_success(&result, output_format, compact);
    Ok(())
}

async fn infer_field(
    client: &HttpClient,
    command: &str,
    category: &str,
    target_key: &str,
    field: Option<&str>,
) -> Result<String> {
    if let Some(field) = field {
        return Ok(field.to_string());
    }

    let result = client.privacy_list_fields(category, target_key).await?;
    let entries = result
        .get("fields")
        .and_then(Value::as_array)
        .ok_or_else(|| Error::Parse("Expected fields array in Privacy response".to_string()))?;
    let fields = entries
        .iter()
        .filter_map(|entry| entry.get("field").and_then(Value::as_str))
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    select_inferred_field(command, category, target_key, fields)
}

fn select_inferred_field(
    command: &str,
    category: &str,
    target_key: &str,
    mut fields: Vec<String>,
) -> Result<String> {
    fields.sort();

    match fields.as_slice() {
        [field] => Ok(field.clone()),
        [] => Err(Error::Client(format!(
            "Privacy target {category}/{target_key} has no active fields."
        ))),
        _ => Err(Error::Client(format!(
            "Privacy target {category}/{target_key} has multiple active fields: {}. Specify one, for example: ov privacy {command} {category} {target_key} <field>",
            fields.join(", ")
        ))),
    }
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
    let result = client
        .privacy_get_version(category, target_key, version)
        .await?;
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
    let result = client
        .privacy_activate(category, target_key, version)
        .await?;
    if matches!(output_format, OutputFormat::Table) {
        return output_version_like_tables(&result, compact);
    }

    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn delete(
    client: &HttpClient,
    selector_or_category: &str,
    target_key: Option<&str>,
    field: Option<&str>,
    yes: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let display = match (target_key, field) {
        (None, None) => selector_or_category.to_string(),
        (Some(target), None) => format!("{selector_or_category}/{target}"),
        (Some(target), Some(field)) => format!("{selector_or_category}/{target}#{field}"),
        (None, Some(_)) => unreachable!("Clap cannot supply the third positional alone"),
    };
    if !yes && !confirm_delete(&display)? {
        output_success(
            &serde_json::json!({
                "selector": display,
                "deleted": false,
                "cancelled": true,
            }),
            output_format,
            compact,
        );
        return Ok(());
    }

    let result = match (target_key, field) {
        (None, None) => client.privacy_delete_selector(selector_or_category).await?,
        (Some(target), None) => client.privacy_delete(selector_or_category, target).await?,
        (Some(target), Some(field)) => {
            client
                .privacy_delete_field(selector_or_category, target, field)
                .await?
        }
        (None, Some(_)) => unreachable!("Clap cannot supply the third positional alone"),
    };
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn scan(
    client: &HttpClient,
    uri: &str,
    fix: bool,
    node_limit: u16,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let body = serde_json::json!({
        "uri": uri,
        "fix": fix,
        "node_limit": node_limit,
    });
    let result = client.privacy_scan(&body).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

fn confirm_delete(display: &str) -> Result<bool> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return Err(Error::Client(
            "Deleting a privacy target requires confirmation. Pass --yes to skip the prompt."
                .to_string(),
        ));
    }

    print!(
        "Delete Privacy selection {}? [y/N]: ",
        display
    );
    io::stdout().flush()?;
    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    let answer = answer.trim().to_ascii_lowercase();
    Ok(answer == "y" || answer == "yes")
}

pub async fn upsert(
    client: &HttpClient,
    category: &str,
    target_key: &str,
    values_json: Option<&str>,
    values_file: Option<&str>,
    field_pairs: &[String],
    change_reason: &str,
    labels_json: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let parsed_values = parse_values_json(values_json, values_file)?;
    let mut final_values: Map<String, Value> = parsed_values.clone().unwrap_or_default();

    let field_overrides = parse_field_pairs(field_pairs)?;
    let mut requested_keys: HashSet<String> = HashSet::new();
    if let Some(values_from_json) = parsed_values.as_ref() {
        requested_keys.extend(values_from_json.keys().cloned());
    }
    requested_keys.extend(field_overrides.keys().cloned());

    let partial_update = !field_overrides.is_empty();
    for (field, value) in field_overrides {
        final_values.insert(field, Value::String(value));
    }

    if final_values.is_empty() {
        return Err(Error::Client(
            "No values provided; use --values-json/--values-file, --field <field=value>, or --field-<name> <value>"
                .to_string(),
        ));
    }

    let labels_value = if let Some(raw) = labels_json {
        let parsed: Value = serde_json::from_str(raw)
            .map_err(|e| Error::Client(format!("Invalid JSON for --labels-json: {}", e)))?;
        if !parsed.is_object() {
            return Err(Error::Client(
                "--labels-json must be a JSON object".to_string(),
            ));
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

    let result = if partial_update {
        client.privacy_patch(category, target_key, &payload).await?
    } else {
        client.privacy_upsert(category, target_key, &payload).await?
    };
    if matches!(output_format, OutputFormat::Table) {
        let values = result
            .get("values")
            .and_then(Value::as_object)
            .ok_or_else(|| Error::Parse("Expected values object in upsert response".to_string()))?
            .clone();
        let (updated_rows, unchanged_rows) = split_kv_rows_by_keys(&values, &requested_keys);
        let rendered = format!(
            "{}\n\n{}",
            render_section_table("updated", updated_rows),
            render_section_table("unchanged", unchanged_rows)
        );
        println!(
            "{}",
            crate::output::append_profile_to_rendered(rendered, &result)
        );
        return Ok(());
    }

    output_success(&result, output_format, compact);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn privacy_tree_result() -> Value {
        json!({"owner_user_id": "alice", "categories": [{
            "category": "credential", "targets": [{
                "target_key": "github", "version": 3, "fields": [
                    {"field": "pat", "configured": true,
                     "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}"},
                    {"field": "region", "configured": false,
                     "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/region}}"}
                ]
            }]
        }]})
    }

    #[test]
    fn privacy_tree_renders_empty() {
        for references in [false, true] {
            let rendered = render_privacy_tree(&json!({"categories": []}), references);
            assert!(rendered.contains("(empty)"));
            assert!(!rendered.contains("ov_privacy"));
        }
    }

    #[test]
    fn privacy_tree_without_references_shows_hierarchy_and_status() {
        let rendered = render_privacy_tree(&privacy_tree_result(), false);
        let lines: Vec<_> = rendered.lines().collect();
        assert_eq!(lines.len(), 5);
        assert!(lines[0].contains("viking://~/privacy"));
        assert!(lines[1].starts_with("  ") && lines[1].contains("credential"));
        assert!(
            lines[2].starts_with("    ")
                && lines[2].contains("github")
                && lines[2].contains("(v3)")
        );
        assert!(
            lines[3].starts_with("      ")
                && lines[3].contains("pat")
                && lines[3].contains("configured")
        );
        assert!(!lines[3].contains("unconfigured"));
        assert!(
            lines[4].starts_with("      ")
                && lines[4].contains("region")
                && lines[4].contains("unconfigured")
        );
        assert!(!rendered.contains("ov_privacy:"));
    }

    #[test]
    fn privacy_tree_references_are_indented_under_each_field() {
        let result = privacy_tree_result();
        let rendered = render_privacy_tree(&result, true);
        let lines: Vec<_> = rendered.lines().collect();
        assert_eq!(lines.len(), 7);
        for (index, field) in [(4, 0), (6, 1)] {
            let reference = result["categories"][0]["targets"][0]["fields"][field]["reference"]
                .as_str()
                .unwrap();
            assert!(lines[index].starts_with("        ") && lines[index].contains(reference));
        }
    }

    #[test]
    fn inferred_field_requires_an_unambiguous_target() {
        let one = vec!["pat".to_string()];
        assert_eq!(
            select_inferred_field("reference", "credential", "github", one)
                .expect("one field should be inferred"),
            "pat"
        );

        let multiple = vec!["region".to_string(), "pat".to_string()];
        let error = select_inferred_field("resolve", "credential", "github", multiple)
            .expect_err("multiple fields must not be guessed")
            .to_string();
        assert!(error.contains("multiple active fields: pat, region"));
        assert!(error.contains("ov privacy resolve credential github <field>"));
    }

    #[test]
    fn privacy_table_uses_field_header() {
        let rendered = render_kv_ascii_table(&[json!({"field": "pat", "value": "secret"})]);
        assert!(rendered.contains("| field | value  |"));
        assert!(!rendered.contains("| key "));
    }

    #[test]
    fn version_like_table_output_keeps_profile_section() {
        let result = json!({
            "version": 3,
            "change_reason": "rotate",
            "values": {
                "k": "v"
            },
            "profile": [
                "privacy took 3ms"
            ]
        });

        let mut meta = result.as_object().expect("object").clone();
        let values = meta
            .remove("values")
            .and_then(|v| v.as_object().cloned())
            .expect("values");
        let rendered = format!(
            "[meta]\n{}\n\n[current]\n{}",
            render_kv_ascii_table(&to_kv_rows(&meta)),
            render_kv_ascii_table(&to_kv_rows(&values))
        );
        let final_rendered = crate::output::append_profile_to_rendered(rendered, &result);

        assert!(final_rendered.contains("profile\nprivacy took 3ms\n"));
    }
}
