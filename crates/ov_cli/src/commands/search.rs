use super::render_utils::{append_profile_lines, wrap_display_text};
use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{OutputFormat, output_success};
use crate::theme;
use colored::Colorize;
use serde_json::Value;
use std::io::IsTerminal;
use unicode_width::UnicodeWidthStr;

const SEARCH_TEXT_WIDTH: usize = 96;
const SEARCH_MIN_TEXT_WIDTH: usize = 32;
const SEARCH_MAX_ABSTRACT_LINES: usize = 2;
const SEARCH_MAX_URI_LINES: usize = 2;
const SEARCH_INDENT: &str = "   ";
const SEARCH_RESULT_COLLECTION_KEYS: &[&str] =
    &["memories", "resources", "skills", "results", "items"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SearchRenderMode {
    Find,
    Search,
}

#[derive(Debug, Clone, Copy)]
struct SearchRenderContext {
    mode: SearchRenderMode,
    node_limit: i32,
}

impl SearchRenderContext {
    fn find(node_limit: i32) -> Self {
        Self {
            mode: SearchRenderMode::Find,
            node_limit,
        }
    }

    fn search(node_limit: i32) -> Self {
        Self {
            mode: SearchRenderMode::Search,
            node_limit,
        }
    }
}

pub async fn find(
    client: &HttpClient,
    query: &str,
    uri: &str,
    node_limit: i32,
    threshold: Option<f64>,
    since: Option<&str>,
    until: Option<&str>,
    time_field: Option<&str>,
    level: Option<Vec<i32>>,
    context_type: Option<Vec<String>>,
    peer_id: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .find(
            query.to_string(),
            uri.to_string(),
            node_limit,
            threshold,
            since.map(|s| s.to_string()),
            until.map(|s| s.to_string()),
            time_field.map(|s| s.to_string()),
            level,
            context_type,
            peer_id.map(|s| s.to_string()),
        )
        .await?;
    output_search_results(
        &result,
        output_format,
        compact,
        SearchRenderContext::find(node_limit),
    );
    Ok(())
}

pub async fn search(
    client: &HttpClient,
    query: &str,
    uri: &str,
    session_id: Option<String>,
    node_limit: i32,
    threshold: Option<f64>,
    since: Option<&str>,
    until: Option<&str>,
    time_field: Option<&str>,
    level: Option<Vec<i32>>,
    context_type: Option<Vec<String>>,
    peer_id: Option<&str>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .search(
            query.to_string(),
            uri.to_string(),
            session_id,
            node_limit,
            threshold,
            since.map(|s| s.to_string()),
            until.map(|s| s.to_string()),
            time_field.map(|s| s.to_string()),
            level,
            context_type,
            peer_id.map(|s| s.to_string()),
        )
        .await?;
    output_search_results(
        &result,
        output_format,
        compact,
        SearchRenderContext::search(node_limit),
    );
    Ok(())
}

fn output_search_results(
    result: &Value,
    output_format: OutputFormat,
    compact: bool,
    context: SearchRenderContext,
) {
    if let Some(rendered) =
        render_search_output_for_table_with_context(result, output_format, Some(context))
    {
        println!("{rendered}");
    } else {
        output_success(result, output_format, compact);
    }
}

#[cfg(test)]
fn render_search_output_for_table(value: &Value, output_format: OutputFormat) -> Option<String> {
    render_search_output_for_table_with_context(value, output_format, None)
}

fn render_search_output_for_table_with_context(
    value: &Value,
    output_format: OutputFormat,
    context: Option<SearchRenderContext>,
) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }
    render_search_results_for_table_with_context(value, context)
}

#[cfg(test)]
fn render_search_results_for_table(value: &Value) -> Option<String> {
    render_search_results_for_table_with_context(value, None)
}

fn render_search_results_for_table_with_context(
    value: &Value,
    context: Option<SearchRenderContext>,
) -> Option<String> {
    let (items, profile) = search_result_items(value)?;
    let mut lines = Vec::new();
    let text_width = search_card_text_width();

    if items.is_empty() {
        lines.push(theme::muted("No results found.").to_string());
        lines.push(
            theme::body("Try a broader query, lower --threshold, or search a wider --uri.")
                .to_string(),
        );
        return Some(lines.join("\n"));
    }

    let pass_count = search_pass_count(value);
    lines.push(
        theme::heading(search_result_summary(items.len(), context, pass_count))
            .bold()
            .to_string(),
    );
    lines.push(theme::body(search_ranking_line(context, pass_count)).to_string());
    lines.push(String::new());

    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            lines.push(String::new());
        }
        render_search_result_card(index + 1, item, text_width, &mut lines);
    }

    append_profile_lines(profile, &mut lines);
    Some(lines.join("\n"))
}

fn search_result_summary(
    item_count: usize,
    context: Option<SearchRenderContext>,
    pass_count: Option<usize>,
) -> String {
    let mut summary = format!(
        "{} {}",
        item_count,
        pluralize(item_count as u64, "result", "results")
    );

    if matches!(
        context.map(|context| context.mode),
        Some(SearchRenderMode::Search)
    ) && let Some(pass_count) = pass_count.filter(|count| *count > 1)
    {
        summary.push_str(&format!(
            " from {pass_count} {}",
            pluralize(pass_count as u64, "search pass", "search passes")
        ));
    }

    summary
}

fn search_ranking_line(context: Option<SearchRenderContext>, pass_count: Option<usize>) -> String {
    let Some(context) = context.filter(|context| context.node_limit > 0) else {
        return "Ranked by relevance".to_string();
    };

    let suffix = match context.mode {
        SearchRenderMode::Find => format!(
            "limit {} final {}",
            context.node_limit,
            pluralize(context.node_limit as u64, "result", "results")
        ),
        SearchRenderMode::Search if pass_count.is_some_and(|count| count > 1) => {
            format!("limit {} per pass", context.node_limit)
        }
        SearchRenderMode::Search => format!("limit {} per search pass", context.node_limit),
    };

    format!("Ranked by relevance · {suffix}")
}

fn search_pass_count(value: &Value) -> Option<usize> {
    fn count_from_object(object: &serde_json::Map<String, Value>) -> Option<usize> {
        object
            .get("query_plan")?
            .get("queries")?
            .as_array()
            .map(Vec::len)
    }

    let object = value.as_object()?;
    count_from_object(object).or_else(|| {
        object
            .get("result")
            .and_then(Value::as_object)
            .and_then(count_from_object)
    })
}

fn search_result_items(value: &Value) -> Option<(Vec<&Value>, Option<&Value>)> {
    if let Some(items) = value.as_array() {
        return Some((items.iter().collect(), None));
    }

    let object = value.as_object()?;
    let profile = object.get("profile").filter(|profile| !profile.is_null());

    if let Some(items) = object.get("result").and_then(Value::as_array) {
        return Some((items.iter().collect(), profile));
    }

    if let Some(result_object) = object.get("result").and_then(Value::as_object) {
        let nested_profile = result_object
            .get("profile")
            .filter(|profile| !profile.is_null())
            .or(profile);
        return collect_search_result_collections(result_object, nested_profile);
    }

    collect_search_result_collections(object, profile)
}

fn collect_search_result_collections<'a>(
    object: &'a serde_json::Map<String, Value>,
    profile: Option<&'a Value>,
) -> Option<(Vec<&'a Value>, Option<&'a Value>)> {
    let mut saw_collection = false;
    let mut items = Vec::new();

    for key in SEARCH_RESULT_COLLECTION_KEYS {
        if let Some(collection) = object.get(*key).and_then(Value::as_array) {
            saw_collection = true;
            items.extend(collection.iter());
        }
    }

    if saw_collection {
        Some((items, profile))
    } else {
        None
    }
}

fn render_search_result_card(
    rank: usize,
    item: &Value,
    text_width: usize,
    lines: &mut Vec<String>,
) {
    let object = item.as_object();
    let mut metadata = vec![
        theme::heading(search_result_kind(object))
            .bold()
            .to_string(),
    ];

    if let Some(level) = search_result_level(object) {
        metadata.push(theme::value(level).bold().to_string());
    }

    if let Some(score) = search_result_score(object) {
        metadata.push(theme::warning(score).bold().to_string());
    }

    lines.push(format!(
        "{}. {}",
        theme::command(rank.to_string()).bold(),
        metadata.join(" · ")
    ));

    if let Some(uri) = search_result_uri(object) {
        for line in wrap_display_text(uri, text_width, SEARCH_MAX_URI_LINES) {
            lines.push(format!("{SEARCH_INDENT}{}", theme::sky_value(line).bold()));
        }
    }

    let abstract_text = search_result_text(item, object);
    let wrapped = wrap_display_text(&abstract_text, text_width, SEARCH_MAX_ABSTRACT_LINES);
    if wrapped.is_empty() {
        lines.push(format!(
            "{SEARCH_INDENT}{}",
            theme::muted("No abstract available.")
        ));
    } else {
        for line in wrapped {
            lines.push(format!("{SEARCH_INDENT}{}", theme::body(line)));
        }
    }
}

fn search_card_text_width() -> usize {
    if std::io::stdout().is_terminal()
        && let Ok((columns, _)) = crossterm::terminal::size()
    {
        return usize::from(columns)
            .saturating_sub(SEARCH_INDENT.width())
            .clamp(SEARCH_MIN_TEXT_WIDTH, SEARCH_TEXT_WIDTH);
    }

    SEARCH_TEXT_WIDTH
}

fn search_result_kind(object: Option<&serde_json::Map<String, Value>>) -> &str {
    object
        .and_then(|object| object.get("context_type").or_else(|| object.get("type")))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("result")
}

fn search_result_level(object: Option<&serde_json::Map<String, Value>>) -> Option<String> {
    let value = object?.get("level")?;
    if let Some(level) = value.as_i64() {
        return Some(format!("Level {level}"));
    }
    value.as_str().and_then(|level| {
        let level = level.trim();
        if level.is_empty() {
            None
        } else {
            Some(format!("Level {}", normalize_level_value(level)))
        }
    })
}

fn normalize_level_value(level: &str) -> &str {
    level
        .strip_prefix("Level ")
        .or_else(|| level.strip_prefix("level "))
        .or_else(|| level.strip_prefix('L'))
        .or_else(|| level.strip_prefix('l'))
        .unwrap_or(level)
        .trim()
}

fn search_result_score(object: Option<&serde_json::Map<String, Value>>) -> Option<String> {
    let value = object?.get("score")?;
    let score = value.as_f64().or_else(|| {
        value
            .as_str()
            .and_then(|value| value.trim().parse::<f64>().ok())
    })?;
    Some(format!("score {score:.3}"))
}

fn search_result_uri(object: Option<&serde_json::Map<String, Value>>) -> Option<&str> {
    object?
        .get("uri")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn search_result_text(item: &Value, object: Option<&serde_json::Map<String, Value>>) -> String {
    for key in ["abstract", "snippet", "content", "text"] {
        if let Some(text) = object
            .and_then(|object| object.get(key))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            return text.to_string();
        }
    }

    if object.is_some() {
        return "No abstract available.".to_string();
    }

    match item {
        Value::String(value) => value.clone(),
        other => other.to_string(),
    }
}

pub async fn grep(
    client: &HttpClient,
    uri: &str,
    exclude_uri: Option<String>,
    pattern: &str,
    ignore_case: bool,
    node_limit: i32,
    level_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .grep(
            uri,
            exclude_uri,
            pattern,
            ignore_case,
            node_limit,
            level_limit,
        )
        .await?;
    output_grep_results(&result, output_format, compact);
    Ok(())
}

fn output_grep_results(result: &Value, output_format: OutputFormat, compact: bool) {
    if let Some(rendered) = render_grep_output_for_table(result, output_format) {
        println!("{rendered}");
    } else {
        output_success(result, output_format, compact);
    }
}

fn render_grep_output_for_table(value: &Value, output_format: OutputFormat) -> Option<String> {
    if matches!(output_format, OutputFormat::Json) {
        return None;
    }

    let object = value.as_object()?;
    let matches = object.get("matches")?.as_array()?;
    let profile = object.get("profile").filter(|profile| !profile.is_null());
    let mut lines = Vec::new();
    let text_width = search_card_text_width();

    if matches.is_empty() {
        lines.push(theme::muted("No matches found.").to_string());
        lines.push(theme::body("Try a broader pattern or search a wider --uri.").to_string());
        append_profile_lines(profile, &mut lines);
        return Some(lines.join("\n"));
    }

    let match_count = object
        .get("match_count")
        .or_else(|| object.get("count"))
        .and_then(Value::as_u64)
        .unwrap_or(matches.len() as u64);
    let files_scanned = object.get("files_scanned").and_then(Value::as_u64);
    let mut summary = format!(
        "{match_count} {}",
        pluralize(match_count, "match", "matches")
    );
    if let Some(files_scanned) = files_scanned {
        summary.push_str(&format!(
            " · {files_scanned} {} scanned",
            pluralize(files_scanned, "file", "files")
        ));
    }
    lines.push(theme::heading(summary).bold().to_string());

    for (index, item) in matches.iter().enumerate() {
        lines.push(String::new());
        render_grep_match_card(index + 1, item, text_width, &mut lines);
    }

    append_profile_lines(profile, &mut lines);
    Some(lines.join("\n"))
}

fn render_grep_match_card(rank: usize, item: &Value, text_width: usize, lines: &mut Vec<String>) {
    let object = item.as_object();
    let line_number = object
        .and_then(|object| object.get("line"))
        .and_then(Value::as_i64)
        .map(|line| line.to_string())
        .unwrap_or_else(|| "?".to_string());

    lines.push(format!(
        "{}. {} {}",
        theme::command(rank.to_string()).bold(),
        theme::heading("line").bold(),
        theme::value(line_number).bold()
    ));

    if let Some(uri) = object
        .and_then(|object| object.get("uri"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        for line in wrap_display_text(uri, text_width, SEARCH_MAX_URI_LINES) {
            lines.push(format!("{SEARCH_INDENT}{}", theme::sky_value(line).bold()));
        }
    }

    if let Some(content) = object
        .and_then(|object| object.get("content"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        for line in wrap_display_text(content, text_width, SEARCH_MAX_ABSTRACT_LINES) {
            lines.push(format!("{SEARCH_INDENT}{}", theme::body(line)));
        }
    }
}

fn pluralize<'a>(count: u64, singular: &'a str, plural: &'a str) -> &'a str {
    if count == 1 { singular } else { plural }
}

pub async fn glob(
    client: &HttpClient,
    pattern: &str,
    uri: &str,
    node_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.glob(pattern, uri, node_limit).await?;
    output_success(&result, output_format, compact);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn search_result_cards_render_ranked_scannable_rows() {
        let results = json!([
            {
                "context_type": "memory",
                "uri": "viking://user/default/memories/entities/.overview.md",
                "level": 1,
                "score": 0.3805481195449829,
                "abstract": "Entity memories from user's world. Each entity has its own subdirectory including projects, people, concepts, etc."
            }
        ]);

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));

        assert!(rendered.starts_with("1 result\nRanked by relevance\n\n"));
        assert!(rendered.contains("1. memory · Level 1 · score 0.381"));
        assert!(rendered.contains("viking://user/default/memories/entities/.overview.md"));
        assert!(rendered.contains("Entity memories from user's world."));
        assert!(!rendered.contains("peop\n   le"));
        assert!(!rendered.contains("context_type  uri"));
    }

    #[test]
    fn search_result_cards_wrap_and_truncate_long_mixed_language_abstracts() {
        let results = json!([
            {
                "type": "resource",
                "uri": "viking://resources/openviking-conversation-2026-06-01.md",
                "score": 0.37084102630615234,
                "abstract": "本资源包含大量 OpenViking CLI 设置和调试记录，涵盖配置、认证、错误提示、命令输出和用户体验改进。This document is intentionally long enough to require wrapping and truncation in terminal output."
            }
        ]);

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));
        let abstract_lines: Vec<&str> = rendered
            .lines()
            .filter(|line| {
                line.trim_start().starts_with("本资源") || line.trim_start().starts_with("This")
            })
            .collect();

        assert!(!abstract_lines.is_empty());
        assert!(rendered.contains("..."));
        for line in rendered.lines() {
            assert!(
                line.chars().count() < 140,
                "line should not sprawl horizontally: {line}"
            );
        }
    }

    #[test]
    fn search_result_cards_handle_missing_optional_fields() {
        let results = json!([
            {"uri": "viking://resources/only-uri.md"}
        ]);

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));

        assert!(rendered.contains("1. result"));
        assert!(rendered.contains("viking://resources/only-uri.md"));
        assert!(rendered.contains("No abstract available."));
        assert!(!rendered.contains("score"));
        assert!(!rendered.contains("Level 0"));
    }

    #[test]
    fn search_result_cards_expand_compact_string_levels() {
        let results = json!([
            {
                "context_type": "memory",
                "uri": "viking://user/default/memories/events/.abstract.md",
                "level": "l0",
                "score": 0.42,
                "abstract": "User event records."
            },
            {
                "context_type": "memory",
                "uri": "viking://user/default/memories/.overview.md",
                "level": "Level 1",
                "score": 0.41,
                "abstract": "User memory overview."
            }
        ]);

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));

        assert!(rendered.starts_with("2 results\nRanked by relevance\n\n"));
        assert!(rendered.contains("1. memory · Level 0 · score 0.420"));
        assert!(rendered.contains("2. memory · Level 1 · score 0.410"));
        assert!(!rendered.contains(" L0 "));
        assert!(!rendered.contains(" L1 "));
    }

    #[test]
    fn search_result_cards_explain_multi_pass_search_limits() {
        let results = json!({
            "query_plan": {
                "queries": [
                    {"query": "codename", "context_type": "memory"},
                    {"query": "codename", "context_type": "resource"}
                ]
            },
            "memories": [
                {
                    "context_type": "memory",
                    "uri": "viking://user/default/memories/entities/.overview.md",
                    "level": 1,
                    "score": 0.452,
                    "abstract": "Entity memories from user's world."
                }
            ],
            "resources": [
                {
                    "context_type": "resource",
                    "uri": "viking://resources/reference.md",
                    "level": 0,
                    "score": 0.401,
                    "abstract": "Reference resource."
                }
            ]
        });

        let rendered = strip_ansi(
            &render_search_results_for_table_with_context(
                &results,
                Some(SearchRenderContext::search(10)),
            )
            .expect("cards"),
        );

        assert!(rendered.starts_with(
            "2 results from 2 search passes\nRanked by relevance · limit 10 per pass\n\n"
        ));
    }

    #[test]
    fn search_result_cards_explain_search_limit_without_query_plan() {
        let results = json!([
            {
                "context_type": "memory",
                "uri": "viking://user/default/memories/entities/.overview.md",
                "level": 1,
                "score": 0.452,
                "abstract": "Entity memories from user's world."
            }
        ]);

        let rendered = strip_ansi(
            &render_search_results_for_table_with_context(
                &results,
                Some(SearchRenderContext::search(10)),
            )
            .expect("cards"),
        );

        assert!(
            rendered.starts_with("1 result\nRanked by relevance · limit 10 per search pass\n\n")
        );
    }

    #[test]
    fn find_result_cards_explain_final_result_limit() {
        let results = json!([
            {
                "context_type": "memory",
                "uri": "viking://user/default/memories/entities/.overview.md",
                "level": 1,
                "score": 0.452,
                "abstract": "Entity memories from user's world."
            }
        ]);

        let rendered = strip_ansi(
            &render_search_results_for_table_with_context(
                &results,
                Some(SearchRenderContext::find(10)),
            )
            .expect("cards"),
        );

        assert!(rendered.starts_with("1 result\nRanked by relevance · limit 10 final results\n\n"));
    }

    #[test]
    fn search_result_cards_show_empty_state() {
        let rendered = strip_ansi(&render_search_results_for_table(&json!([])).expect("empty"));

        assert!(rendered.contains("No results found."));
        assert!(
            rendered.contains("Try a broader query, lower --threshold, or search a wider --uri.")
        );
    }

    #[test]
    fn search_result_cards_render_result_object_with_profile() {
        let results = json!({
            "result": [
                {
                    "context_type": "memory",
                    "uri": "viking://user/default/memories/events/.abstract.md",
                    "abstract": "User event records.",
                }
            ],
            "profile": ["vector search: 12ms"]
        });

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));

        assert!(rendered.contains("1. memory"));
        assert!(rendered.contains("viking://user/default/memories/events/.abstract.md"));
        assert!(rendered.contains("profile"));
        assert!(rendered.contains("vector search: 12ms"));
    }

    #[test]
    fn search_result_cards_render_categorized_find_response() {
        let results = json!({
            "memories": [
                {
                    "context_type": "memory",
                    "uri": "viking://user/default/memories/entities/football_player/cristiano_ronaldo.md",
                    "level": 2,
                    "score": 0.5194366574287415,
                    "abstract": "- 8 major individual awards\n- 35 career trophies"
                }
            ],
            "resources": [],
            "skills": [],
            "total": 1
        });

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));

        assert!(rendered.contains("1. memory · Level 2 · score 0.519"));
        assert!(rendered.contains(
            "viking://user/default/memories/entities/football_player/cristiano_ronaldo.md"
        ));
        assert!(rendered.contains("- 8 major individual awards - 35 career trophies"));
        assert!(!rendered.contains("context_type  uri"));
    }

    #[test]
    fn search_result_cards_wrap_long_uris() {
        let long_uri = format!(
            "viking://resources/{}",
            "very-long-path-segment-".repeat(10)
        );
        let results = json!([
            {
                "type": "resource",
                "uri": long_uri,
                "abstract": "Short result."
            }
        ]);

        let rendered = strip_ansi(&render_search_results_for_table(&results).expect("cards"));

        assert!(rendered.contains("..."));
        for line in rendered.lines() {
            assert!(
                line.chars().count() < 140,
                "line should not sprawl horizontally: {line}"
            );
        }
    }

    #[test]
    fn search_result_renderer_skips_json_output() {
        let results = json!([
            {"uri": "viking://resources/raw.md", "abstract": "raw"}
        ]);

        assert!(render_search_output_for_table(&results, OutputFormat::Json).is_none());
    }

    #[test]
    fn grep_table_output_renders_match_cards() {
        let result = json!({
            "matches": [
                {
                    "line": 1,
                    "uri": "viking://user/haozhe/memories/events/2026/05/26/gent_canoeing.md",
                    "content": "On 2026-05-26, user Haozhe provided a temporary codename and recorded a travel activity of canoeing in Ghent. The assistant noted these details and inquired whether this activity was part of Haozhe's 2025 European trip after Sweden."
                },
                {
                    "line": 14,
                    "uri": "viking://user/haozhe/memories/events/2026/05/26/gent_canoeing.md",
                    "content": "\"event_name\": \"gent_canoeing\","
                }
            ],
            "count": 2,
            "match_count": 2,
            "files_scanned": 1
        });

        let rendered =
            strip_ansi(&render_grep_output_for_table(&result, OutputFormat::Table).expect("grep"));

        assert!(rendered.contains("2 matches · 1 file scanned"));
        assert!(rendered.contains("1. line 1"));
        assert!(
            rendered.contains("viking://user/haozhe/memories/events/2026/05/26/gent_canoeing.md")
        );
        assert!(rendered.contains("2. line 14"));
        assert!(!rendered.contains("line  uri"));
        for line in rendered.lines() {
            assert!(
                line.chars().count() < 140,
                "line should not sprawl horizontally: {line}"
            );
        }
    }

    #[test]
    fn grep_table_output_handles_empty_matches() {
        let result = json!({
            "matches": [],
            "count": 0,
            "match_count": 0,
            "files_scanned": 3
        });

        let rendered =
            strip_ansi(&render_grep_output_for_table(&result, OutputFormat::Table).expect("grep"));

        assert!(rendered.contains("No matches found."));
        assert!(rendered.contains("Try a broader pattern or search a wider --uri."));
    }

    #[test]
    fn grep_renderer_skips_json_output() {
        let result = json!({"matches": []});

        assert!(render_grep_output_for_table(&result, OutputFormat::Json).is_none());
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();
        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next == 'm' {
                        break;
                    }
                }
            } else {
                output.push(ch);
            }
        }
        output
    }
}
