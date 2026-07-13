use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde_json::{Map, Value};

pub async fn get_memory(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response: Value = client.get("/api/v1/user-settings/memory", &[]).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub async fn patch_memory(
    client: &HttpClient,
    memory_types: Option<Vec<String>>,
    clear_memory_types: bool,
    agent_evolution_enabled: Option<bool>,
    clear_agent_evolution_enabled: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let mut body = Map::new();
    if clear_memory_types {
        body.insert("memory_types".into(), Value::Null);
    } else if let Some(memory_types) = memory_types {
        body.insert(
            "memory_types".into(),
            Value::Array(memory_types.into_iter().map(Value::String).collect()),
        );
    }
    if clear_agent_evolution_enabled {
        body.insert("agent_evolution_enabled".into(), Value::Null);
    } else if let Some(enabled) = agent_evolution_enabled {
        body.insert("agent_evolution_enabled".into(), Value::Bool(enabled));
    }
    if body.is_empty() {
        return Err(Error::Client(
            "set-memory requires at least one setting or clear flag".to_string(),
        ));
    }

    let response: Value = client
        .patch("/api/v1/user-settings/memory", &Value::Object(body), &[])
        .await?;
    output_success(&response, output_format, compact);
    Ok(())
}
