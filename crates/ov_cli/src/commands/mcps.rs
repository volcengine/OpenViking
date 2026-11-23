use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use colored::Colorize;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

#[derive(Debug, Deserialize)]
struct McpConfig {
    #[serde(rename = "mcpServers")]
    mcp_servers: BTreeMap<String, McpServerConfig>,
}

#[derive(Debug, Deserialize)]
struct McpServerConfig {
    url: String,
    #[serde(default)]
    headers: BTreeMap<String, String>,
}

fn validate_server_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 64
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(Error::Parse(
            "MCP server name must contain only letters, numbers, '_' or '-' (max 64)".to_string(),
        ));
    }
    Ok(())
}

fn parse_add_config(raw: &str) -> Result<McpConfig> {
    let config: McpConfig = serde_json::from_str(raw)
        .map_err(|error| Error::Parse(format!("Invalid MCP configuration JSON: {error}")))?;
    if config.mcp_servers.is_empty() {
        return Err(Error::Parse(
            "MCP configuration must contain at least one entry in 'mcpServers'".to_string(),
        ));
    }
    for name in config.mcp_servers.keys() {
        validate_server_name(name)?;
    }
    Ok(config)
}

fn parse_arguments(raw: &str) -> Result<Map<String, Value>> {
    let value: Value = serde_json::from_str(raw)
        .map_err(|error| Error::Parse(format!("Invalid JSON for --args: {error}")))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| Error::Parse("--args must be a JSON object".to_string()))
}

fn print_response(response: &Value, output_format: OutputFormat, compact: bool) {
    output_success(response, output_format, compact);
}

fn response_payload(response: &Value) -> &Value {
    response.get("result").unwrap_or(response)
}

pub async fn add(
    client: &HttpClient,
    raw_config: &str,
    wait: bool,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let config = parse_add_config(raw_config)?;
    let mut responses = Vec::with_capacity(config.mcp_servers.len());
    for (name, server) in config.mcp_servers {
        let mut body = json!({
            "name": name,
            "url": server.url,
            "headers": server.headers,
            "wait": wait,
        });
        if let Some(target_uri) = target_uri {
            body["target_uri"] = json!(target_uri);
        }
        let response: Value = client.post("/api/v1/mcps", &body).await?;
        responses.push(response);
    }
    if !wait && matches!(output_format, OutputFormat::Table) {
        eprintln!("Note: MCP tool processing may continue in the background.");
    }
    if responses.len() == 1 {
        print_response(&responses[0], output_format, compact);
    } else {
        print_response(
            &json!({"servers": responses, "total": responses.len()}),
            output_format,
            compact,
        );
    }
    Ok(())
}

pub async fn remove(
    client: &HttpClient,
    name: &str,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    validate_server_name(name)?;
    let params = target_uri
        .map(|uri| vec![("target_uri".to_string(), uri.to_string())])
        .unwrap_or_default();
    let response: Value = client
        .delete(&format!("/api/v1/mcps/{name}"), &params)
        .await?;
    print_response(&response, output_format, compact);
    Ok(())
}

pub async fn list(
    client: &HttpClient,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let params = target_uri
        .map(|uri| vec![("target_uri".to_string(), uri.to_string())])
        .unwrap_or_default();
    let response: Value = client.get("/api/v1/mcps", &params).await?;
    if let Some(rendered) = render_list_for_table(&response, output_format) {
        println!("{rendered}");
    } else {
        print_response(&response, output_format, compact);
    }
    Ok(())
}

fn render_list_for_table(response: &Value, output_format: OutputFormat) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    let payload = response_payload(response);
    let servers = payload.get("servers")?.as_array()?;
    if servers.is_empty() {
        return Some(theme::muted("No MCP servers found.").to_string());
    }

    let total = payload
        .get("total")
        .and_then(Value::as_u64)
        .unwrap_or(servers.len() as u64);
    let noun = if total == 1 {
        "MCP server"
    } else {
        "MCP servers"
    };
    let mut lines = vec![
        theme::heading(format!("{total} {noun}")).bold().to_string(),
        String::new(),
    ];

    for (index, server) in servers.iter().enumerate() {
        let server = server.as_object()?;
        let name = server.get("name")?.as_str()?;
        let url = server.get("url")?.as_str()?;
        let tool_count = server
            .get("tool_count")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let tool_noun = if tool_count == 1 { "tool" } else { "tools" };

        if index > 0 {
            lines.push(String::new());
        }
        lines.push(format!(
            "{}. {} · {}",
            theme::command((index + 1).to_string()).bold(),
            theme::heading(name).bold(),
            theme::body(format!("{tool_count} {tool_noun}"))
        ));
        lines.push(format!("   {}", theme::sky_value(url).bold()));
        if let Some(root_uri) = server.get("root_uri").and_then(Value::as_str) {
            lines.push(format!("   {}", theme::muted(format!("root: {root_uri}"))));
        }

        let header_names = server
            .get("header_names")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .filter(|value| !value.is_empty());
        if let Some(header_names) = header_names {
            lines.push(format!(
                "   {}",
                theme::muted(format!("headers: {header_names}"))
            ));
        }
    }

    Some(lines.join("\n"))
}

pub async fn find(
    client: &HttpClient,
    query: &str,
    limit: u8,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let mut body = json!({"query": query, "limit": limit});
    if let Some(target_uri) = target_uri {
        body["target_uri"] = json!(target_uri);
    }
    let response: Value = client.post("/api/v1/mcps/find", &body).await?;
    super::search::output_mcp_find_results(&response, output_format, compact, i32::from(limit));
    Ok(())
}

pub async fn validate(
    client: &HttpClient,
    raw_config: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let config = parse_add_config(raw_config)?;
    let mut responses = Vec::with_capacity(config.mcp_servers.len());
    for (name, server) in config.mcp_servers {
        let response: Value = client
            .post(
                "/api/v1/mcps/validate",
                &json!({"name": name, "url": server.url, "headers": server.headers}),
            )
            .await?;
        responses.push(response);
    }
    if responses.len() == 1 {
        print_response(&responses[0], output_format, compact);
    } else {
        print_response(
            &json!({"servers": responses, "total": responses.len()}),
            output_format,
            compact,
        );
    }
    Ok(())
}

pub async fn show(
    client: &HttpClient,
    name: &str,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    validate_server_name(name)?;
    let params = target_uri
        .map(|uri| vec![("target_uri".to_string(), uri.to_string())])
        .unwrap_or_default();
    let response: Value = client.get(&format!("/api/v1/mcps/{name}"), &params).await?;
    if let Some(rendered) = render_show_for_table(&response, output_format) {
        println!("{rendered}");
    } else {
        print_response(&response, output_format, compact);
    }
    Ok(())
}

fn render_show_for_table(response: &Value, output_format: OutputFormat) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    let server = response_payload(response).as_object()?;
    let tools = server.get("tools")?.as_array()?;
    let mut lines = vec!["[metadata]".to_string()];
    push_show_field(&mut lines, "name", server.get("name"));
    push_show_field(&mut lines, "root_uri", server.get("root_uri"));
    push_show_field(&mut lines, "url", server.get("url"));
    push_show_field(&mut lines, "tool_count", server.get("tool_count"));
    push_show_field(&mut lines, "header_names", server.get("header_names"));
    push_show_field(&mut lines, "created_at", server.get("created_at"));
    push_show_field(&mut lines, "updated_at", server.get("updated_at"));
    lines.push(String::new());
    lines.push("[tools]".to_string());

    if tools.is_empty() {
        lines.push("(none)".to_string());
        return Some(lines.join("\n"));
    }

    for (index, document) in tools.iter().enumerate() {
        let document = document.as_object()?;
        let tool = document.get("tool")?.as_object()?;
        let name = tool.get("name")?.as_str()?;
        lines.push(format!("{}. {}", index + 1, name));
        push_indented_field(&mut lines, "title", tool.get("title"));
        push_indented_field(&mut lines, "description", tool.get("description"));
        push_indented_field(&mut lines, "uri", document.get("uri"));
        push_tool_arguments(&mut lines, tool.get("inputSchema"));
        if index + 1 < tools.len() {
            lines.push(String::new());
        }
    }

    Some(lines.join("\n"))
}

fn push_show_field(lines: &mut Vec<String>, label: &str, value: Option<&Value>) {
    let Some(value) = value else {
        return;
    };
    let rendered = match value {
        Value::Array(items) => {
            let rendered = items
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ");
            if rendered.is_empty() {
                return;
            }
            rendered
        }
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Bool(value) => value.to_string(),
        _ => return,
    };
    lines.push(format!("{label}: {rendered}"));
}

fn push_indented_field(lines: &mut Vec<String>, label: &str, value: Option<&Value>) {
    let Some(value) = value
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    else {
        return;
    };
    lines.push(format!("   {label}: {value}"));
}

fn push_tool_arguments(lines: &mut Vec<String>, schema: Option<&Value>) {
    let Some(schema) = schema.and_then(Value::as_object) else {
        return;
    };
    let Some(properties) = schema.get("properties").and_then(Value::as_object) else {
        return;
    };
    if properties.is_empty() {
        lines.push("   arguments: (none)".to_string());
        return;
    }
    let required = schema
        .get("required")
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(Value::as_str).collect::<Vec<_>>())
        .unwrap_or_default();
    lines.push("   arguments:".to_string());
    for (name, property) in properties {
        let property_type = property
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("any");
        let requirement = if required.contains(&name.as_str()) {
            "required"
        } else {
            "optional"
        };
        let description = property
            .get("description")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(|value| format!(": {value}"))
            .unwrap_or_default();
        lines.push(format!(
            "     - {name} ({property_type}, {requirement}){description}"
        ));
    }
}

pub async fn sync(
    client: &HttpClient,
    name: &str,
    wait: bool,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    validate_server_name(name)?;
    let mut body = json!({"wait": wait});
    if let Some(target_uri) = target_uri {
        body["target_uri"] = json!(target_uri);
    }
    let response: Value = client
        .post(&format!("/api/v1/mcps/{name}/sync"), &body)
        .await?;
    if !wait && matches!(output_format, OutputFormat::Table) {
        eprintln!("Note: MCP tool processing may continue in the background.");
    }
    if let Some(rendered) = render_sync_for_table(&response, output_format) {
        println!("{rendered}");
    } else {
        print_response(&response, output_format, compact);
    }
    Ok(())
}

fn render_sync_for_table(response: &Value, output_format: OutputFormat) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    let payload = response_payload(response).as_object()?;
    let name = payload.get("name")?.as_str()?;
    let tool_count = payload.get("tool_count")?.as_u64()?;
    let changes = payload.get("changes")?.as_object()?;
    let added = sync_change_names(changes, "added")?;
    let changed = sync_change_names(changes, "changed")?;
    let unchanged = sync_change_names(changes, "unchanged")?;
    let removed = sync_change_names(changes, "removed")?;
    let affected = payload.get("affected_mounts")?.as_array()?;
    let tool_noun = if tool_count == 1 { "tool" } else { "tools" };

    let mut lines = vec![
        theme::success(format!("Synced {name}: {tool_count} {tool_noun}"))
            .bold()
            .to_string(),
        format!(
            "{} added, {} changed, {} unchanged, {} removed",
            added.len(),
            changed.len(),
            unchanged.len(),
            removed.len()
        ),
    ];
    push_sync_change_line(&mut lines, "Added", &added);
    push_sync_change_line(&mut lines, "Changed", &changed);
    push_sync_change_line(&mut lines, "Unchanged", &unchanged);
    push_sync_change_line(&mut lines, "Removed", &removed);

    if affected.is_empty() {
        lines.push(String::new());
        lines.push(theme::muted("No affected live context mounts.").to_string());
        return Some(lines.join("\n"));
    }

    lines.push(String::new());
    lines.push(
        theme::warning(format!(
            "{} affected live context mount{}",
            affected.len(),
            if affected.len() == 1 { "" } else { "s" }
        ))
        .bold()
        .to_string(),
    );
    for (index, mount) in affected.iter().enumerate() {
        let mount = mount.as_object()?;
        let node_uri = mount.get("node_uri")?.as_str()?;
        let tool_uri = mount.get("tool_uri")?.as_str()?;
        let status = mount.get("status")?.as_str()?;
        let reason = mount.get("reason")?.as_str()?;
        let rendered_status = match status {
            "INVALID" | "REMOVED" => theme::error(status).bold().to_string(),
            _ => theme::warning(status).bold().to_string(),
        };
        lines.push(format!("{}. [{rendered_status}] {node_uri}", index + 1));
        lines.push(format!("   tool: {tool_uri}"));
        lines.push(format!("   reason: {reason}"));
    }
    Some(lines.join("\n"))
}

fn sync_change_names<'a>(
    changes: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Option<Vec<&'a str>> {
    changes
        .get(key)?
        .as_array()?
        .iter()
        .map(Value::as_str)
        .collect()
}

fn push_sync_change_line(lines: &mut Vec<String>, label: &str, names: &[&str]) {
    if !names.is_empty() {
        lines.push(format!("{label}: {}", names.join(", ")));
    }
}

pub async fn status(
    client: &HttpClient,
    name: &str,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    validate_server_name(name)?;
    let mut params = vec![("name".to_string(), name.to_string())];
    if let Some(target_uri) = target_uri {
        params.push(("target_uri".to_string(), target_uri.to_string()));
    }
    let response: Value = client.get("/api/v1/mcps/status", &params).await?;
    print_response(&response, output_format, compact);
    Ok(())
}

pub async fn mount(
    client: &HttpClient,
    tool_uri: &str,
    node_uri: &str,
    title: &str,
    description: Option<&str>,
    timeout_seconds: u64,
    raw_arguments: &str,
    wait: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let arguments = parse_arguments(raw_arguments)?;
    let response: Value = client
        .post(
            "/api/v1/mcps/mount",
            &json!({
                "tool_uri": tool_uri,
                "node_uri": node_uri,
                "title": title,
                "description": description,
                "timeout_seconds": timeout_seconds,
                "arguments": arguments,
                "wait": wait,
            }),
        )
        .await?;
    print_response(&response, output_format, compact);
    Ok(())
}

pub async fn unmount(
    client: &HttpClient,
    node_uri: &str,
    wait: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let params = vec![
        ("node_uri".to_string(), node_uri.to_string()),
        ("wait".to_string(), wait.to_string()),
    ];
    let response: Value = client.delete("/api/v1/mcps/mount", &params).await?;
    print_response(&response, output_format, compact);
    Ok(())
}

pub async fn mounts(
    client: &HttpClient,
    mcp: &str,
    tool: Option<&str>,
    target_uri: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    validate_server_name(mcp)?;
    let mut params = vec![("mcp".to_string(), mcp.to_string())];
    if let Some(tool) = tool {
        params.push(("tool".to_string(), tool.to_string()));
    }
    if let Some(target_uri) = target_uri {
        params.push(("target_uri".to_string(), target_uri.to_string()));
    }
    let response: Value = client.get("/api/v1/mcps/mounts", &params).await?;
    if let Some(rendered) = render_mounts_for_table(&response, output_format) {
        println!("{rendered}");
    } else {
        print_response(&response, output_format, compact);
    }
    Ok(())
}

fn render_mounts_for_table(response: &Value, output_format: OutputFormat) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    let payload = response_payload(response);
    let mounts = payload.get("mounts")?.as_array()?;
    if mounts.is_empty() {
        return Some(theme::muted("No live context mounts found.").to_string());
    }

    let total = payload
        .get("total")
        .and_then(Value::as_u64)
        .unwrap_or(mounts.len() as u64);
    let noun = if total == 1 { "mount" } else { "mounts" };
    let mut lines = vec![
        theme::heading(format!("{total} live context {noun}"))
            .bold()
            .to_string(),
        String::new(),
    ];
    for (index, mount) in mounts.iter().enumerate() {
        let mount = mount.as_object()?;
        let node_uri = mount.get("node_uri")?.as_str()?;
        let tool_uri = mount.get("tool_uri")?.as_str()?;
        if index > 0 {
            lines.push(String::new());
        }
        lines.push(format!(
            "{}. {}",
            theme::command((index + 1).to_string()).bold(),
            theme::heading(node_uri).bold()
        ));
        lines.push(format!("   {}", theme::sky_value(tool_uri).bold()));
        if let Some(created_at) = mount.get("created_at").and_then(Value::as_str) {
            lines.push(format!(
                "   {}",
                theme::muted(format!("created: {created_at}"))
            ));
        }
    }
    Some(lines.join("\n"))
}

pub async fn invoke(
    client: &HttpClient,
    uri: &str,
    raw_arguments: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let arguments = parse_arguments(raw_arguments)?;
    let response: Value = client
        .post(
            "/api/v1/mcps/invoke",
            &json!({"uri": uri, "arguments": arguments}),
        )
        .await?;
    match crate::output::output_tool_result(&response, output_format, compact) {
        Some(true) => Err(Error::AlreadyReported),
        Some(false) => Ok(()),
        None => {
            print_response(&response, output_format, compact);
            Ok(())
        }
    }
}
