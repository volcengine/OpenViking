use crate::client::{CompileAccepted, HttpClient};
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};

pub async fn run(
    client: &HttpClient,
    from_uris: Vec<String>,
    to: String,
    skill: String,
    reason: Option<String>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let sources = normalize_sources(from_uris)?;
    let reason = reason
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let accepted = client
        .create_compile(&sources, to.trim(), skill.trim(), reason)
        .await?;
    render_accepted(&accepted, to.trim(), output_format, compact);
    Ok(())
}

fn normalize_sources(values: Vec<String>) -> Result<Vec<String>> {
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
    use super::normalize_sources;

    #[test]
    fn expands_comma_separated_and_repeated_sources_stably() {
        let result = normalize_sources(vec![
            "viking://resources/a,viking://resources/b".into(),
            "viking://resources/a".into(),
        ])
        .expect("sources should be valid");
        assert_eq!(result, vec!["viking://resources/a", "viking://resources/b"]);
    }

    #[test]
    fn rejects_empty_source_items() {
        assert!(normalize_sources(vec!["viking://resources/a,".into()]).is_err());
    }
}
