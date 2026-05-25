use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};

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
) -> Result<()> {
    let result = client
        .ls(
            uri,
            simple,
            recursive,
            output,
            abs_limit,
            show_all_hidden,
            node_limit,
        )
        .await?;
    output_success(&result, output_format, compact);
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
) -> Result<()> {
    let result = client
        .tree(
            uri,
            output,
            abs_limit,
            show_all_hidden,
            node_limit,
            level_limit,
        )
        .await?;
    output_success(&result, output_format, compact);
    Ok(())
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
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.rm(uri, recursive).await?;

    let message = if let Some(count) = result.get("estimated_deleted_count").and_then(|v| v.as_u64()) {
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

fn output_message_result(
    result: serde_json::Value,
    message: String,
    output_format: OutputFormat,
    compact: bool,
) {
    match output_format {
        OutputFormat::Json => output_success(result, output_format, compact),
        OutputFormat::Table => {
            println!("{}", crate::output::append_profile_to_rendered(message, &result));
        }
    }
}

#[cfg(test)]
mod tests {
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
}
