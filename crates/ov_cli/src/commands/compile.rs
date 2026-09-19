use crate::client::{CompileAccepted, HttpClient};
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde_json::{Map, Value};

/// Sentinel `--skill` value that routes to in-place memory consolidation.
const MEMORY_COMPILE_SKILL: &str = "memory";

pub async fn run(
    client: &HttpClient,
    from_uris: Vec<String>,
    to: String,
    skill: String,
    instruction: Option<String>,
    args: Option<String>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let skill = skill.trim().to_string();
    let memory_mode = skill == MEMORY_COMPILE_SKILL;
    let sources = normalize_sources(from_uris, memory_mode)?;
    let args = parse_args(args.as_deref())?;
    let instruction = instruction
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let accepted = client
        .create_compile(
            &sources,
            to.trim(),
            skill.as_str(),
            instruction,
            args.as_ref(),
        )
        .await?;
    render_accepted(&accepted, to.trim(), output_format, compact);
    Ok(())
}

fn parse_args(value: Option<&str>) -> Result<Option<Map<String, Value>>> {
    let Some(value) = value else {
        return Ok(None);
    };
    let parsed: Value = serde_json::from_str(value)
        .map_err(|err| Error::Client(format!("--args must be valid JSON: {err}")))?;
    let Value::Object(args) = parsed else {
        return Err(Error::Client("--args must be a JSON object".into()));
    };
    Ok((!args.is_empty()).then_some(args))
}

fn normalize_sources(values: Vec<String>, memory_mode: bool) -> Result<Vec<String>> {
    let mut result = Vec::new();
    for value in values {
        for item in value.split(',') {
            let item = item.trim();
            if item.is_empty() {
                return Err(Error::Client("--from contains an empty directory".into()));
            }
            if !result.iter().any(|existing| existing == item) {
                result.push(item.to_string());
            }
        }
    }
    if memory_mode {
        if !result.is_empty() {
            return Err(Error::Client(
                "--skill memory consolidates --to in place and takes no --from".into(),
            ));
        }
        return Ok(result);
    }
    if result.is_empty() {
        return Err(Error::Client(
            "at least one --from directory is required".into(),
        ));
    }
    Ok(result)
}

fn render_accepted(
    value: &CompileAccepted,
    requested_to: &str,
    format: OutputFormat,
    compact: bool,
) {
    if matches!(format, OutputFormat::Json) {
        output_success(value, format, compact);
    } else {
        println!("task_id: {}", value.task_id);
        println!("status: {}", value.status);
        println!("to: {}", value.to.as_deref().unwrap_or(requested_to));
    }
}

#[cfg(test)]
mod tests {
    use super::{normalize_sources, parse_args};

    #[test]
    fn expands_comma_separated_and_repeated_sources_stably() {
        let result = normalize_sources(
            vec![
                "viking://resources/a,viking://resources/b".into(),
                "viking://resources/a".into(),
            ],
            false,
        )
        .expect("sources should be valid");
        assert_eq!(result, vec!["viking://resources/a", "viking://resources/b"]);
    }

    #[test]
    fn rejects_empty_source_items() {
        assert!(normalize_sources(vec!["viking://resources/a,".into()], false).is_err());
        let args = parse_args(Some(r#"{"model_name":"endpoint-1"}"#))
            .expect("args should be valid")
            .expect("args should not be empty");
        assert_eq!(args["model_name"], "endpoint-1");
        assert!(parse_args(Some("[]")).is_err());
    }

    #[test]
    fn memory_mode_allows_no_sources_but_rejects_any() {
        assert!(normalize_sources(vec![], true)
            .expect("memory mode allows empty sources")
            .is_empty());
        assert!(normalize_sources(vec!["viking://resources/a".into()], true).is_err());
        assert!(normalize_sources(vec![], false).is_err());
    }
}
