use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use crate::theme;
use colored::Colorize;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::io::{self, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::TempDir;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};
use url::Url;

enum PreparedSource {
    Raw(String),
    Path {
        path: PathBuf,
        _temp_dir: Option<TempDir>,
        origin: SourceOrigin,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct GitSource {
    clone_url: String,
    ref_name: Option<String>,
    subdir: Option<PathBuf>,
}

#[derive(Debug, Clone)]
enum SourceOrigin {
    Local { source: String },
    Git(GitSource),
}

#[derive(Debug)]
struct AddTarget {
    data: String,
    source: Option<SkillSourceRecord>,
    _temp_dir: Option<TempDir>,
}

#[derive(Debug, Clone)]
struct InstalledSkillSummary {
    name: String,
    description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
struct SkillSourceRecord {
    #[serde(rename = "type")]
    source_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    clone_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    ref_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    subdir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    skill_name: Option<String>,
}

const SOURCE_METADATA_FILENAME: &str = ".source.json";

impl PreparedSource {
    fn path(&self) -> Option<&Path> {
        match self {
            Self::Path { path, .. } => Some(path.as_path()),
            Self::Raw(_) => None,
        }
    }
}

pub async fn add(
    client: &HttpClient,
    data: &str,
    skill_names: Vec<String>,
    list_only: bool,
    wait: bool,
    yes: bool,
    show_progress: bool,
    verbose: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let source = prepare_source(data)?;
    if list_only {
        return list_source_skills(&source, output_format, compact);
    }

    let targets = resolve_add_targets(&source, &skill_names)?;
    if targets.is_empty() {
        return Err(Error::Client("No skills to install.".to_string()));
    }
    if targets.len() > 1 && !yes {
        let names = targets.iter().map(skill_target_label).collect::<Vec<_>>();
        if !confirm_action("Install", &names)? {
            output_message_result(
                serde_json::json!({ "cancelled": true, "skills": names }),
                "Aborted.".to_string(),
                output_format,
                compact,
            );
            return Ok(());
        }
    }

    let mut installed = Vec::new();

    for target in targets {
        let result = client
            .add_skill(&target.data, wait, None, show_progress, verbose)
            .await?;
        persist_source_record(client, &result, target.source).await?;
        installed.push(result);
    }

    if !wait && matches!(output_format, OutputFormat::Table) {
        eprintln!("Note: Skill processing may continue in the background.");
        eprintln!(
            "Use 'ov task status <task_id>' to check progress, or 'ov task list' to see all tasks."
        );
    }

    if installed.len() == 1 {
        output_success(installed.remove(0), output_format, compact);
    } else {
        let total = installed.len();
        output_success(
            serde_json::json!({
                "installed": installed,
                "total": total,
            }),
            output_format,
            compact,
        );
    }
    Ok(())
}

pub async fn list(
    client: &HttpClient,
    node_limit: i32,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client.skills_list(node_limit).await?;
    output_success(result, output_format, compact);
    Ok(())
}

pub async fn show(
    client: &HttpClient,
    name: &str,
    level: Option<i32>,
    include_files: bool,
    include_source: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let include_content = level.is_none() || level == Some(2);
    let mut result = client
        .skill_show(name, include_content, include_files, include_source, level)
        .await?;
    if let Some(level) = level {
        filter_skill_show_level(&mut result, level);
    }
    output_skill_show(&result, output_format, compact);
    Ok(())
}

pub async fn find(
    client: &HttpClient,
    query: &str,
    node_limit: i32,
    threshold: Option<f64>,
    level: Option<Vec<i32>>,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = client
        .skill_find(query, node_limit, threshold, level)
        .await?;
    output_success(result, output_format, compact);
    Ok(())
}

pub async fn update(
    client: &HttpClient,
    skill_names: Vec<String>,
    wait: bool,
    yes: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let names = resolve_installed_skill_names(client, skill_names).await?;
    if names.is_empty() {
        output_message_result(
            serde_json::json!({ "updated": [], "total": 0 }),
            "No skills to update.".to_string(),
            output_format,
            compact,
        );
        return Ok(());
    }
    if !yes && !confirm_action("Update", &names)? {
        output_message_result(
            serde_json::json!({ "cancelled": true, "skills": names }),
            "Aborted.".to_string(),
            output_format,
            compact,
        );
        return Ok(());
    }

    let mut updated = Vec::new();
    for name in names {
        let update_target = resolve_update_target(client, &name).await?;
        let result = client
            .skill_update(&name, &update_target.data, wait, None, false, false)
            .await?;
        persist_source_record(client, &result, update_target.source).await?;
        updated.push(result);
    }
    let total = updated.len();
    output_success(
        serde_json::json!({
            "updated": updated,
            "total": total,
        }),
        output_format,
        compact,
    );
    Ok(())
}

pub async fn remove(
    client: &HttpClient,
    skill_names: Vec<String>,
    all: bool,
    yes: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    if all && !skill_names.is_empty() {
        return Err(Error::Client(
            "Pass either skill names or --all, not both.".to_string(),
        ));
    }
    let requested_names = normalize_skill_names(skill_names)?;
    let names = if all {
        resolve_installed_skill_names(client, Vec::new()).await?
    } else if !requested_names.is_empty() {
        requested_names
    } else {
        if yes || !can_prompt() {
            return Err(Error::Client(
                "Specify at least one skill name, or pass --all.".to_string(),
            ));
        }
        match prompt_remove_skill_selection(client).await? {
            Some(selected) => selected,
            None => {
                output_message_result(
                    serde_json::json!({ "cancelled": true, "skills": [] }),
                    "Aborted.".to_string(),
                    output_format,
                    compact,
                );
                return Ok(());
            }
        }
    };
    if names.is_empty() {
        output_message_result(
            serde_json::json!({ "removed": [], "total": 0 }),
            "No skills to remove.".to_string(),
            output_format,
            compact,
        );
        return Ok(());
    }
    if !yes && !confirm_action("Remove", &names)? {
        output_message_result(
            serde_json::json!({ "cancelled": true, "skills": names }),
            "Aborted.".to_string(),
            output_format,
            compact,
        );
        return Ok(());
    }

    let removed_names = names.clone();
    let mut removed = Vec::new();
    for name in names {
        removed.push(client.skill_remove(&name).await?);
    }
    let total = removed.len();
    output_message_result(
        serde_json::json!({
            "removed": removed,
            "removed_names": removed_names,
            "total": total,
        }),
        format!(
            "Removed {} skill(s): {}.",
            total,
            format_name_list(&removed_names)
        ),
        output_format,
        compact,
    );
    Ok(())
}

pub async fn validate(
    _client: &HttpClient,
    path: &str,
    strict: bool,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let result = validate_skill_path(path, strict)?;
    if result
        .get("valid")
        .and_then(Value::as_bool)
        .is_some_and(|valid| !valid)
    {
        let errors = result
            .get("errors")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| {
                        item.as_str()
                            .or_else(|| item.get("message").and_then(Value::as_str))
                    })
                    .collect::<Vec<_>>()
                    .join("; ")
            })
            .filter(|text| !text.is_empty())
            .unwrap_or_else(|| "Skill validation failed".to_string());
        if matches!(output_format, OutputFormat::Json) {
            output_success(result, output_format, compact);
        }
        return Err(Error::Client(errors));
    }
    output_skill_validate_success(&result, output_format, compact);
    Ok(())
}

fn validate_skill_path(path: &str, strict: bool) -> Result<Value> {
    let path_obj = Path::new(path);
    if !path_obj.exists() {
        return Err(Error::Client(format!(
            "Skill path '{}' does not exist.",
            path
        )));
    }

    let skill_file = if path_obj.is_dir() {
        let skill_file = path_obj.join("SKILL.md");
        if !skill_file.is_file() {
            return Err(Error::Client(format!(
                "SKILL.md not found in '{}'.",
                path_obj.display()
            )));
        }
        skill_file
    } else if path_obj.is_file() {
        if path_obj.file_name().and_then(|name| name.to_str()) != Some("SKILL.md") {
            return Err(Error::Client(
                "Validate expects a SKILL.md file or a skill directory.".to_string(),
            ));
        }
        path_obj.to_path_buf()
    } else {
        return Err(Error::Client(format!(
            "Skill path '{}' is not a file or directory.",
            path
        )));
    };

    let content = std::fs::read_to_string(&skill_file).map_err(|e| {
        Error::Client(format!(
            "Failed to read skill file '{}': {}",
            skill_file.display(),
            e
        ))
    })?;
    let skill_dir_name = skill_file
        .parent()
        .and_then(|parent| parent.file_name())
        .and_then(|name| name.to_str())
        .unwrap_or("")
        .to_string();

    Ok(validate_skill_content(
        &content,
        strict,
        &skill_file.to_string_lossy(),
        &skill_dir_name,
    ))
}

fn validate_skill_content(
    content: &str,
    strict: bool,
    source_path: &str,
    skill_dir_name: &str,
) -> Value {
    let mut errors = Vec::new();
    let mut warnings = Vec::new();

    let parsed = match parse_skill_md(content) {
        Ok(parsed) => parsed,
        Err(message) => {
            return json!({
                "valid": false,
                "strict": strict,
                "message": "Skill validation failed.",
                "name": "",
                "description": "",
                "tags": [],
                "allowed_tools": [],
                "body_lines": 0,
                "source_path": source_path,
                "skill_dir_name": skill_dir_name,
                "errors": [validation_issue("yaml_format", &message, "data")],
                "warnings": [],
            });
        }
    };

    let name = yaml_mapping_get_str(&parsed.meta, "name")
        .unwrap_or("")
        .trim()
        .to_string();
    let description = yaml_mapping_get_str(&parsed.meta, "description")
        .unwrap_or("")
        .trim()
        .to_string();
    let tags = yaml_value_as_string_array(yaml_mapping_get(&parsed.meta, "tags"));
    let allowed_tools = yaml_value_as_string_array(
        yaml_mapping_get(&parsed.meta, "allowed_tools")
            .or_else(|| yaml_mapping_get(&parsed.meta, "allowed-tools")),
    );

    if name.is_empty() {
        errors.push(validation_issue(
            "name_required",
            "name is required",
            "name",
        ));
    }
    if description.is_empty() {
        errors.push(validation_issue(
            "description_required",
            "description is required",
            "description",
        ));
    }

    if !name.is_empty() {
        if !skill_dir_name.is_empty() && name != skill_dir_name {
            push_mode_issue(
                &mut errors,
                &mut warnings,
                strict,
                "name_matches_directory",
                &format!(
                    "name '{}' does not match directory name '{}'",
                    name, skill_dir_name
                ),
                "name",
            );
        }
        if name.chars().count() > 64 {
            push_mode_issue(
                &mut errors,
                &mut warnings,
                strict,
                "name_max_length",
                "name must not exceed 64 characters",
                "name",
            );
        }
        if !is_valid_skill_name(&name) {
            push_mode_issue(
                &mut errors,
                &mut warnings,
                strict,
                "name_allowed_characters",
                "name may only contain letters, numbers, underscores, and hyphens",
                "name",
            );
        }
    }

    if description.chars().count() > 1024 {
        push_mode_issue(
            &mut errors,
            &mut warnings,
            strict,
            "description_max_length",
            "description must not exceed 1024 characters",
            "description",
        );
    }

    let body_lines = parsed.body.lines().count();
    if strict && body_lines > 500 {
        warnings.push(validation_issue(
            "body_max_lines",
            "SKILL.md body exceeds 500 lines",
            "content",
        ));
    }

    json!({
        "valid": errors.is_empty(),
        "strict": strict,
        "message": if errors.is_empty() { "Skill validation succeeded." } else { "Skill validation failed." },
        "name": name,
        "description": description,
        "tags": tags,
        "allowed_tools": allowed_tools,
        "body_lines": body_lines,
        "source_path": source_path,
        "skill_dir_name": skill_dir_name,
        "errors": errors,
        "warnings": warnings,
    })
}

struct ParsedSkillMd {
    meta: serde_yaml::Mapping,
    body: String,
}

fn parse_skill_md(content: &str) -> std::result::Result<ParsedSkillMd, String> {
    let rest = content
        .strip_prefix("---\n")
        .or_else(|| content.strip_prefix("---\r\n"))
        .ok_or_else(|| "SKILL.md must have YAML frontmatter".to_string())?;
    let Some((frontmatter, body)) = split_frontmatter(rest) else {
        return Err("SKILL.md must have closing YAML frontmatter delimiter".to_string());
    };
    let meta: serde_yaml::Value = serde_yaml::from_str(frontmatter)
        .map_err(|e| format!("Invalid YAML frontmatter: {}", e))?;
    let Some(meta) = meta.as_mapping().cloned() else {
        return Err("Invalid YAML frontmatter".to_string());
    };
    Ok(ParsedSkillMd {
        meta,
        body: body.trim().to_string(),
    })
}

fn split_frontmatter(rest: &str) -> Option<(&str, &str)> {
    if let Some(index) = rest.find("\n---\n") {
        return Some((&rest[..index], &rest[index + "\n---\n".len()..]));
    }
    if let Some(index) = rest.find("\r\n---\r\n") {
        return Some((&rest[..index], &rest[index + "\r\n---\r\n".len()..]));
    }
    None
}

fn yaml_mapping_get<'a>(
    mapping: &'a serde_yaml::Mapping,
    key: &str,
) -> Option<&'a serde_yaml::Value> {
    mapping.get(serde_yaml::Value::String(key.to_string()))
}

fn yaml_mapping_get_str<'a>(mapping: &'a serde_yaml::Mapping, key: &str) -> Option<&'a str> {
    yaml_mapping_get(mapping, key).and_then(serde_yaml::Value::as_str)
}

fn yaml_value_as_string_array(value: Option<&serde_yaml::Value>) -> Vec<String> {
    match value {
        Some(serde_yaml::Value::Sequence(items)) => items
            .iter()
            .filter_map(serde_yaml::Value::as_str)
            .map(ToString::to_string)
            .collect(),
        Some(value) => value
            .as_str()
            .map(|value| vec![value.to_string()])
            .unwrap_or_default(),
        None => Vec::new(),
    }
}

fn is_valid_skill_name(name: &str) -> bool {
    name.chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
}

fn validation_issue(rule: &str, message: &str, field: &str) -> Value {
    json!({
        "rule": rule,
        "message": message,
        "field": field,
    })
}

fn push_mode_issue(
    errors: &mut Vec<Value>,
    warnings: &mut Vec<Value>,
    strict: bool,
    rule: &str,
    message: &str,
    field: &str,
) {
    let issue = validation_issue(rule, message, field);
    if strict {
        errors.push(issue);
    } else {
        warnings.push(issue);
    }
}

fn output_skill_validate_success(result: &Value, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Json) {
        output_success(result, output_format, compact);
        return;
    }

    let name = result.get("name").and_then(Value::as_str).unwrap_or("");
    let description = result
        .get("description")
        .and_then(Value::as_str)
        .unwrap_or("");
    let mut lines = vec![
        "Skill validation succeeded.".to_string(),
        format!("name: {}", name),
        format!("description: {}", description),
    ];

    if let Some(warnings) = result.get("warnings").and_then(Value::as_array)
        && !warnings.is_empty()
    {
        lines.push(String::new());
        lines.push("warnings:".to_string());
        for warning in warnings {
            let message = warning
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("Validation warning");
            lines.push(format!("- {}", message));
        }
    }

    println!("{}", lines.join("\n"));
}

fn prepare_source(data: &str) -> Result<PreparedSource> {
    let path = Path::new(data);
    if path.exists() {
        return Ok(PreparedSource::Path {
            path: path.to_path_buf(),
            _temp_dir: None,
            origin: SourceOrigin::Local {
                source: data.to_string(),
            },
        });
    }
    if let Some(git_source) = parse_git_source(data) {
        return prepare_git_source(git_source);
    }
    Ok(PreparedSource::Raw(data.to_string()))
}

fn prepare_git_source(git_source: GitSource) -> Result<PreparedSource> {
    let temp_dir = tempfile::tempdir()?;
    let status = Command::new("git")
        .arg("clone")
        .arg("--depth")
        .arg("1")
        .args(
            git_source
                .ref_name
                .iter()
                .flat_map(|ref_name| ["--branch", ref_name.as_str()]),
        )
        .arg(&git_source.clone_url)
        .arg(temp_dir.path())
        .status()
        .map_err(|e| Error::Client(format!("Failed to run git clone: {}", e)))?;
    if !status.success() {
        return Err(Error::Client(format!(
            "Failed to clone skill source: {}",
            git_source.clone_url
        )));
    }
    let path = if let Some(subdir) = git_source.subdir.as_ref() {
        let path = temp_dir.path().join(&subdir);
        if !path.exists() {
            return Err(Error::Client(format!(
                "Skill path '{}' was not found in cloned repository '{}'.",
                subdir.display(),
                git_source.clone_url
            )));
        }
        path
    } else {
        temp_dir.path().to_path_buf()
    };
    Ok(PreparedSource::Path {
        path,
        _temp_dir: Some(temp_dir),
        origin: SourceOrigin::Git(git_source),
    })
}

fn parse_git_source(data: &str) -> Option<GitSource> {
    if let Some(source) = parse_github_tree_source(data) {
        return Some(source);
    }
    let is_plain_git_source = data.starts_with("git@")
        || data.starts_with("ssh://")
        || data.starts_with("git://")
        || ((data.starts_with("https://") || data.starts_with("http://"))
            && (data.ends_with(".git")
                || data.contains("github.com/")
                || data.contains("gitlab.com/")
                || data.contains("bitbucket.org/")));
    is_plain_git_source.then(|| GitSource {
        clone_url: data.to_string(),
        ref_name: None,
        subdir: None,
    })
}

fn parse_github_tree_source(data: &str) -> Option<GitSource> {
    let url = Url::parse(data).ok()?;
    if url.host_str()? != "github.com" {
        return None;
    }

    let segments = url.path_segments()?.collect::<Vec<_>>();
    if segments.len() < 5 || segments.get(2) != Some(&"tree") {
        return None;
    }

    let owner = segments[0];
    let repo = segments[1].trim_end_matches(".git");
    let branch = segments[3];
    let subdir = segments[4..]
        .iter()
        .fold(PathBuf::new(), |mut path, segment| {
            path.push(segment);
            path
        });
    if owner.is_empty() || repo.is_empty() || branch.is_empty() || subdir.as_os_str().is_empty() {
        return None;
    }

    Some(GitSource {
        clone_url: format!("https://github.com/{owner}/{repo}.git"),
        ref_name: Some(branch.to_string()),
        subdir: Some(subdir),
    })
}

fn resolve_add_targets(source: &PreparedSource, skill_names: &[String]) -> Result<Vec<AddTarget>> {
    if skill_names.is_empty() {
        return match source {
            PreparedSource::Raw(data) => Ok(vec![AddTarget {
                data: data.clone(),
                source: None,
                _temp_dir: None,
            }]),
            PreparedSource::Path { path, .. } => resolve_default_add_targets(source, path),
        };
    }

    let Some(root) = source.path() else {
        return Err(Error::Client(
            "--skill can only be used with a local or git skill source.".to_string(),
        ));
    };
    if !root.is_dir() {
        return Err(Error::Client(
            "--skill requires a directory source.".to_string(),
        ));
    }

    let requested = normalize_skill_names(skill_names.to_vec())?;
    if requested.iter().any(|name| name == "*") {
        if requested.len() > 1 {
            return Err(Error::Client(
                "Use --skill '*' by itself when installing all skills.".to_string(),
            ));
        }
        let targets = discover_skill_dirs(root)?;
        if targets.is_empty() {
            return Err(Error::Client(format!(
                "No skill directories found under '{}'.",
                root.display()
            )));
        }
        return targets
            .into_iter()
            .map(|path| add_target_from_path(source, root, path))
            .collect();
    }

    requested
        .into_iter()
        .map(|name| {
            resolve_named_skill_dir(root, &name)
                .and_then(|path| add_target_from_path(source, root, path))
        })
        .collect()
}

fn resolve_default_add_targets(source: &PreparedSource, path: &Path) -> Result<Vec<AddTarget>> {
    if !path.is_dir() {
        return Ok(vec![add_target_from_path(
            source,
            path,
            path.to_path_buf(),
        )?]);
    }
    if path.join("SKILL.md").is_file() {
        return Ok(vec![add_target_from_path(
            source,
            path,
            path.to_path_buf(),
        )?]);
    }

    let targets = discover_skill_dirs(path)?;
    if targets.is_empty() {
        return Err(Error::Client(format!(
            "SKILL.md not found in '{}'.",
            path.display()
        )));
    }
    targets
        .into_iter()
        .map(|target| add_target_from_path(source, path, target))
        .collect()
}

fn add_target_from_path(
    source: &PreparedSource,
    root: &Path,
    target: PathBuf,
) -> Result<AddTarget> {
    Ok(AddTarget {
        data: path_to_string(&target),
        source: source_record_for_target(source, root, &target)?,
        _temp_dir: None,
    })
}

fn source_record_for_target(
    source: &PreparedSource,
    root: &Path,
    target: &Path,
) -> Result<Option<SkillSourceRecord>> {
    let PreparedSource::Path { origin, .. } = source else {
        return Ok(None);
    };

    match origin {
        SourceOrigin::Local { source } => Ok(Some(SkillSourceRecord {
            source_type: "local".to_string(),
            source: Some(source.clone()),
            path: Some(path_to_string(target)),
            clone_url: None,
            ref_name: None,
            subdir: None,
            skill_name: None,
        })),
        SourceOrigin::Git(git_source) => {
            let subdir = git_subdir_for_target(git_source.subdir.as_ref(), root, target)?;
            Ok(Some(SkillSourceRecord {
                source_type: "git".to_string(),
                source: Some(git_source_source(git_source)),
                path: None,
                clone_url: Some(git_source.clone_url.clone()),
                ref_name: git_source.ref_name.clone(),
                subdir: subdir.map(|path| path_to_string(&path)),
                skill_name: None,
            }))
        }
    }
}

fn git_subdir_for_target(
    source_subdir: Option<&PathBuf>,
    root: &Path,
    target: &Path,
) -> Result<Option<PathBuf>> {
    let relative = target
        .strip_prefix(root)
        .ok()
        .filter(|path| !path.as_os_str().is_empty());
    let mut subdir = source_subdir.cloned().unwrap_or_default();
    if let Some(relative) = relative {
        subdir.push(relative);
    }
    if subdir.as_os_str().is_empty() {
        Ok(None)
    } else {
        Ok(Some(subdir))
    }
}

fn git_source_source(git_source: &GitSource) -> String {
    if git_source.clone_url.contains("github.com/")
        && let (Some(ref_name), Some(subdir)) = (&git_source.ref_name, &git_source.subdir)
    {
        let repo = git_source
            .clone_url
            .trim_end_matches(".git")
            .trim_start_matches("https://github.com/");
        return format!(
            "https://github.com/{}/tree/{}/{}",
            repo,
            ref_name,
            path_to_string(subdir)
        );
    }
    git_source.clone_url.clone()
}

fn list_source_skills(
    source: &PreparedSource,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let Some(root) = source.path() else {
        return Err(Error::Client(
            "--list can only be used with a local or git skill source.".to_string(),
        ));
    };
    if !root.is_dir() {
        return Err(Error::Client(
            "--list requires a directory skill source.".to_string(),
        ));
    }

    let dirs = discover_skill_dirs(root)?;
    let skills = dirs
        .iter()
        .map(|dir| skill_dir_summary(dir, root))
        .collect::<Result<Vec<_>>>()?;
    output_success(
        serde_json::json!({
            "source": path_to_string(root),
            "skills": skills,
            "total": skills.len(),
        }),
        output_format,
        compact,
    );
    Ok(())
}

fn skill_dir_summary(dir: &Path, root: &Path) -> Result<Value> {
    let skill_md = dir.join("SKILL.md");
    let content = std::fs::read_to_string(&skill_md).map_err(|e| {
        Error::Client(format!(
            "Failed to read skill file '{}': {}",
            skill_md.display(),
            e
        ))
    })?;
    let parsed = parse_skill_md(&content).ok();
    let name = parsed
        .as_ref()
        .and_then(|parsed| yaml_mapping_get_str(&parsed.meta, "name"))
        .map(ToString::to_string)
        .or_else(|| {
            dir.file_name()
                .and_then(|name| name.to_str())
                .map(ToString::to_string)
        })
        .unwrap_or_default();
    let description = parsed
        .as_ref()
        .and_then(|parsed| yaml_mapping_get_str(&parsed.meta, "description"))
        .unwrap_or("")
        .to_string();
    let relative_path = dir.strip_prefix(root).unwrap_or(dir);

    Ok(serde_json::json!({
        "name": name,
        "description": description,
        "path": path_to_string(relative_path),
    }))
}

fn skill_target_label(target: &AddTarget) -> String {
    let path = Path::new(&target.data);
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(&target.data)
        .to_string()
}

async fn resolve_update_target(client: &HttpClient, name: &str) -> Result<AddTarget> {
    if let Some(record) = read_skill_source_record(client, name).await? {
        return update_target_from_record(&record, name);
    }

    if let Some(target) = prompt_update_source(name)? {
        return Ok(target);
    }

    Err(Error::Client(format!(
        "Skill '{}' has no recorded source metadata. Reinstall it with 'ov skills add <source>' or run update interactively to provide a new source.",
        name
    )))
}

async fn read_skill_source_record(
    client: &HttpClient,
    name: &str,
) -> Result<Option<SkillSourceRecord>> {
    let result = client.skill_show(name, false, false, true, Some(0)).await?;
    let Some(source) = result.get("source") else {
        return Ok(None);
    };
    if !source
        .get("tracked")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Ok(None);
    }
    let mut record: SkillSourceRecord = serde_json::from_value(source.clone())
        .map_err(|e| Error::Parse(format!("Invalid source metadata for '{}': {}", name, e)))?;
    record.skill_name = Some(name.to_string());
    Ok(Some(record))
}

fn update_target_from_record(record: &SkillSourceRecord, name: &str) -> Result<AddTarget> {
    match record.source_type.as_str() {
        "git" => {
            let prepared = prepare_source_from_git_record(record)?;
            let PreparedSource::Path {
                path, _temp_dir, ..
            } = prepared
            else {
                return Err(Error::Parse(format!(
                    "Skill '{}' git source did not resolve to a path",
                    name
                )));
            };
            Ok(AddTarget {
                data: path_to_string(&path),
                source: Some(record.clone()),
                _temp_dir,
            })
        }
        "local" => {
            let path = record.path.as_deref().ok_or_else(|| {
                Error::Parse(format!("Skill '{}' source metadata missing path", name))
            })?;
            let path_obj = Path::new(path);
            if !path_obj.exists() {
                if let Some(target) = prompt_update_source(name)? {
                    return Ok(target);
                }
                return Err(Error::Client(format!(
                    "Recorded source path for skill '{}' no longer exists: {}",
                    name, path
                )));
            }
            Ok(AddTarget {
                data: path.to_string(),
                source: Some(record.clone()),
                _temp_dir: None,
            })
        }
        other => Err(Error::Parse(format!(
            "Unsupported source type '{}' for skill '{}'",
            other, name
        ))),
    }
}

fn prepare_source_from_git_record(record: &SkillSourceRecord) -> Result<PreparedSource> {
    let clone_url = record
        .clone_url
        .as_deref()
        .ok_or_else(|| Error::Parse("Git source metadata missing clone_url".to_string()))?;
    let git_source = GitSource {
        clone_url: clone_url.to_string(),
        ref_name: record.ref_name.clone(),
        subdir: record.subdir.as_ref().map(PathBuf::from),
    };
    prepare_git_source(git_source)
}

fn prompt_update_source(name: &str) -> Result<Option<AddTarget>> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return Ok(None);
    }

    print!(
        "Source for skill '{}' is missing or untracked. Enter a local path or git source to update it (blank to abort): ",
        name
    );
    io::stdout().flush()?;

    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    let source_text = answer.trim();
    if source_text.is_empty() {
        return Ok(None);
    }
    let source = prepare_source(source_text)?;
    let targets = resolve_add_targets(&source, &[name.to_string()])?;
    let mut target = targets.into_iter().next().ok_or_else(|| {
        Error::Client(format!(
            "Skill '{}' was not found in source '{}'.",
            name, source_text
        ))
    })?;
    if let PreparedSource::Path { _temp_dir, .. } = source {
        target._temp_dir = _temp_dir;
    }
    Ok(Some(target))
}

async fn persist_source_record(
    client: &HttpClient,
    result: &Value,
    source: Option<SkillSourceRecord>,
) -> Result<()> {
    let Some(mut record) = source else {
        return Ok(());
    };
    let name = result
        .get("name")
        .and_then(Value::as_str)
        .or_else(|| record.skill_name.as_deref());
    let Some(name) = name else {
        return Ok(());
    };
    record.skill_name = Some(name.to_string());
    let Some(root_uri) = result.get("root_uri").and_then(Value::as_str) else {
        return Ok(());
    };
    let uri = format!(
        "{}/{}",
        root_uri.trim_end_matches('/'),
        SOURCE_METADATA_FILENAME
    );
    let content = serde_json::to_string_pretty(&record)
        .map_err(|e| Error::Parse(format!("Failed to serialize source metadata: {}", e)))?;
    if client
        .write(&uri, &content, "replace", true, None)
        .await
        .is_err()
    {
        client.write(&uri, &content, "create", true, None).await?;
    }
    Ok(())
}

fn normalize_skill_names(names: Vec<String>) -> Result<Vec<String>> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for name in names {
        let trimmed = name.trim();
        if trimmed.is_empty() {
            return Err(Error::Client("Skill name cannot be empty.".to_string()));
        }
        if seen.insert(trimmed.to_string()) {
            out.push(trimmed.to_string());
        }
    }
    Ok(out)
}

fn discover_skill_dirs(root: &Path) -> Result<Vec<PathBuf>> {
    let mut dirs = Vec::new();
    if root.join("SKILL.md").is_file() {
        dirs.push(root.to_path_buf());
    }
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() && path.join("SKILL.md").is_file() {
            dirs.push(path);
        }
    }
    dirs.sort();
    dirs.dedup();
    Ok(dirs)
}

fn resolve_named_skill_dir(root: &Path, name: &str) -> Result<PathBuf> {
    let child = root.join(name);
    if child.is_dir() && child.join("SKILL.md").is_file() {
        return Ok(child);
    }

    if root.join("SKILL.md").is_file()
        && root
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|root_name| root_name == name)
    {
        return Ok(root.to_path_buf());
    }

    Err(Error::Client(format!(
        "Skill '{}' was not found under '{}'.",
        name,
        root.display()
    )))
}

async fn resolve_installed_skill_names(
    client: &HttpClient,
    requested: Vec<String>,
) -> Result<Vec<String>> {
    let requested = normalize_skill_names(requested)?;
    if !requested.is_empty() {
        return Ok(requested);
    }

    Ok(list_installed_skills(client)
        .await?
        .into_iter()
        .map(|skill| skill.name)
        .collect())
}

async fn list_installed_skills(client: &HttpClient) -> Result<Vec<InstalledSkillSummary>> {
    let result = client.skills_list(10000).await?;
    let skills = result
        .get("skills")
        .and_then(Value::as_array)
        .ok_or_else(|| Error::Parse("skills list response missing skills array".to_string()))?
        .iter()
        .filter_map(|item| {
            let name = item.get("name").and_then(Value::as_str)?.to_string();
            let description = item
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            Some(InstalledSkillSummary { name, description })
        })
        .collect();
    Ok(skills)
}

async fn prompt_remove_skill_selection(client: &HttpClient) -> Result<Option<Vec<String>>> {
    let skills = list_installed_skills(client).await?;
    if skills.is_empty() {
        return Ok(Some(Vec::new()));
    }
    prompt_multi_select_skills("Select skill(s) to remove", &skills)
}

fn can_prompt() -> bool {
    io::stdin().is_terminal() && io::stdout().is_terminal()
}

fn prompt_multi_select_skills(
    prompt: &str,
    skills: &[InstalledSkillSummary],
) -> Result<Option<Vec<String>>> {
    use crossterm::{
        cursor,
        event::{self, Event, KeyCode, KeyModifiers},
        execute,
        terminal::{self, Clear, ClearType},
    };

    if skills.is_empty() {
        return Ok(Some(Vec::new()));
    }

    struct RawGuard {
        hide_cursor: bool,
    }

    impl RawGuard {
        fn enter() -> Result<Self> {
            terminal::enable_raw_mode()?;
            let mut stdout = io::stdout();
            if let Err(error) = execute!(stdout, cursor::Hide) {
                let _ = terminal::disable_raw_mode();
                return Err(error.into());
            }
            Ok(Self { hide_cursor: true })
        }
    }

    impl Drop for RawGuard {
        fn drop(&mut self) {
            let _ = terminal::disable_raw_mode();
            if self.hide_cursor {
                let _ = execute!(io::stdout(), cursor::Show);
            }
        }
    }

    let _raw_guard = RawGuard::enter()?;
    let mut current = 0usize;
    let mut checked = vec![false; skills.len()];
    let mut rendered_lines = 0usize;

    loop {
        clear_rendered_lines(rendered_lines)?;
        let lines = skill_multi_select_lines(prompt, skills, current, &checked);
        rendered_lines = lines.len();
        print!("{}", live_select_block(&lines));
        io::stdout().flush()?;

        if let Event::Key(key) = event::read()? {
            match key.code {
                KeyCode::Up => {
                    current = if current == 0 {
                        skills.len().saturating_sub(1)
                    } else {
                        current - 1
                    };
                }
                KeyCode::Down => current = (current + 1) % skills.len(),
                KeyCode::Char(' ') => checked[current] = !checked[current],
                KeyCode::Char('a') | KeyCode::Char('A') => {
                    let select_all = checked.iter().any(|value| !*value);
                    checked.fill(select_all);
                }
                KeyCode::Enter | KeyCode::Char('\n') | KeyCode::Char('\r') => {
                    clear_rendered_lines(rendered_lines)?;
                    let selected = selected_skill_names(skills, current, &checked);
                    return Ok(Some(selected));
                }
                KeyCode::Esc => {
                    clear_rendered_lines(rendered_lines)?;
                    return Ok(None);
                }
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    clear_rendered_lines(rendered_lines)?;
                    return Err(Error::Client("Aborted.".to_string()));
                }
                _ => {}
            }
        }
    }

    fn clear_rendered_lines(lines: usize) -> Result<()> {
        if lines == 0 {
            return Ok(());
        }
        let mut stdout = io::stdout();
        execute!(
            stdout,
            cursor::MoveUp(lines as u16),
            cursor::MoveToColumn(0)
        )?;
        for line in 0..lines {
            execute!(
                stdout,
                cursor::MoveToColumn(0),
                Clear(ClearType::CurrentLine)
            )?;
            if line + 1 < lines {
                execute!(stdout, cursor::MoveDown(1))?;
            }
        }
        execute!(
            stdout,
            cursor::MoveUp(lines.saturating_sub(1) as u16),
            cursor::MoveToColumn(0)
        )?;
        Ok(())
    }
}

fn selected_skill_names(
    skills: &[InstalledSkillSummary],
    current: usize,
    checked: &[bool],
) -> Vec<String> {
    let mut selected = skills
        .iter()
        .zip(checked)
        .filter_map(|(skill, checked)| checked.then(|| skill.name.clone()))
        .collect::<Vec<_>>();
    if selected.is_empty()
        && let Some(skill) = skills.get(current)
    {
        selected.push(skill.name.clone());
    }
    selected
}

fn skill_multi_select_lines(
    prompt: &str,
    skills: &[InstalledSkillSummary],
    current: usize,
    checked: &[bool],
) -> Vec<String> {
    let terminal_width = crossterm::terminal::size()
        .map(|(columns, _)| columns as usize)
        .unwrap_or(100)
        .clamp(48, 140);
    let item_width = terminal_width.saturating_sub(8).max(32);
    let mut lines = Vec::new();
    lines.push(format!(
        "{} {}",
        theme::prompt("?").bold(),
        theme::strong(prompt)
    ));
    lines.push(format!(
        "  {}",
        theme::muted("Up/Down move, Space toggle, Enter confirm, A toggle all, Esc cancel")
    ));
    lines.push(String::new());
    for (index, skill) in skills.iter().enumerate() {
        let active = index == current;
        let cursor = if active { ">" } else { " " };
        let check = if checked.get(index).copied().unwrap_or(false) {
            "[x]"
        } else {
            "[ ]"
        };
        let label = skill_selection_label(skill, item_width);
        let label = if active {
            theme::selection(label).bold().to_string()
        } else {
            theme::body(label).to_string()
        };
        lines.push(format!("  {cursor} {check} {label}"));
    }
    lines
}

fn skill_selection_label(skill: &InstalledSkillSummary, width: usize) -> String {
    let label = if skill.description.is_empty() {
        skill.name.clone()
    } else {
        format!("{} - {}", skill.name, skill.description)
    };
    truncate_display_width(&label, width)
}

fn truncate_display_width(text: &str, max_width: usize) -> String {
    if UnicodeWidthStr::width(text) <= max_width {
        return text.to_string();
    }

    let ellipsis = "...";
    let target_width = max_width.saturating_sub(ellipsis.len());
    let mut width = 0usize;
    let mut out = String::new();
    for ch in text.chars() {
        let ch_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if width + ch_width > target_width {
            break;
        }
        out.push(ch);
        width += ch_width;
    }
    out.push_str(ellipsis);
    out
}

fn live_select_block(lines: &[String]) -> String {
    if lines.is_empty() {
        return String::new();
    }

    let mut rendered = lines.join("\r\n");
    rendered.push_str("\r\n");
    rendered
}

fn confirm_action(action: &str, names: &[String]) -> Result<bool> {
    if !can_prompt() {
        return Err(Error::Client(format!(
            "{} requires confirmation. Pass --yes to skip the prompt.",
            action
        )));
    }

    print!(
        "{} {} skill(s): {}? [y/N]: ",
        action,
        names.len(),
        format_name_list(names)
    );
    io::stdout().flush()?;

    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    let answer = answer.trim().to_ascii_lowercase();
    Ok(answer == "y" || answer == "yes")
}

fn format_name_list(names: &[String]) -> String {
    const MAX_NAMES: usize = 5;
    if names.len() <= MAX_NAMES {
        return names.join(", ");
    }
    format!(
        "{}, and {} more",
        names[..MAX_NAMES].join(", "),
        names.len() - MAX_NAMES
    )
}

fn path_to_string(path: &Path) -> String {
    path.to_string_lossy().to_string()
}

fn output_skill_show(result: &Value, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Table)
        && let Some(rendered) = render_skill_show_for_table(result)
    {
        println!(
            "{}",
            crate::output::append_profile_to_rendered(rendered, result)
        );
        return;
    }
    output_success(result, output_format, compact);
}

fn filter_skill_show_level(value: &mut Value, level: i32) {
    let Some(obj) = value.as_object_mut() else {
        return;
    };

    match level {
        0 => {
            obj.remove("overview");
            obj.remove("content");
        }
        1 => {
            obj.remove("abstract");
            obj.remove("content");
        }
        2 => {
            obj.remove("abstract");
            obj.remove("overview");
        }
        _ => {}
    }
}

fn render_skill_show_for_table(value: &Value) -> Option<String> {
    let obj = value.as_object()?;
    if !obj.contains_key("skill_md_uri") && !obj.contains_key("abstract") {
        return None;
    }

    let mut lines = Vec::new();
    push_section(&mut lines, "metadata");
    push_kv(&mut lines, "name", obj.get("name"));
    push_kv(&mut lines, "description", obj.get("description"));
    push_kv(&mut lines, "uri", obj.get("uri"));
    push_kv(&mut lines, "root_uri", obj.get("root_uri"));
    push_kv(&mut lines, "skill_md_uri", obj.get("skill_md_uri"));
    push_kv(&mut lines, "tags", obj.get("tags"));
    push_kv(&mut lines, "allowed_tools", obj.get("allowed_tools"));

    if let Some(abstract_text) = obj.get("abstract").and_then(Value::as_str) {
        push_text_section(&mut lines, "L0 abstract", abstract_text);
    }
    if let Some(overview) = obj.get("overview").and_then(Value::as_str) {
        push_text_section(&mut lines, "L1 overview", overview);
    }
    if let Some(content) = obj.get("content").and_then(Value::as_str) {
        push_text_section(&mut lines, "L2 SKILL.md", content);
    }
    if let Some(files) = obj.get("files").and_then(Value::as_array) {
        push_section(&mut lines, "auxiliary files");
        if files.is_empty() {
            lines.push("(none)".to_string());
        } else {
            for file in files {
                let name = file
                    .get("path")
                    .or_else(|| file.get("name"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let kind = file.get("kind").and_then(Value::as_str).unwrap_or("file");
                let uri = file.get("uri").and_then(Value::as_str).unwrap_or("");
                lines.push(format!("- {} [{}]", name, kind));
                if !uri.is_empty() {
                    lines.push(format!("  uri: {}", uri));
                }
            }
        }
    }
    if let Some(source) = obj.get("source").and_then(Value::as_object) {
        push_section(&mut lines, "source");
        for (key, value) in source {
            push_kv(&mut lines, key, Some(value));
        }
    }

    Some(lines.join("\n"))
}

fn push_section(lines: &mut Vec<String>, title: &str) {
    if !lines.is_empty() {
        lines.push(String::new());
    }
    lines.push(format!("[{}]", title));
}

fn push_text_section(lines: &mut Vec<String>, title: &str, text: &str) {
    push_section(lines, title);
    lines.push(text.trim_end().to_string());
}

fn push_kv(lines: &mut Vec<String>, key: &str, value: Option<&Value>) {
    let Some(value) = value else {
        return;
    };
    if value.is_null() {
        return;
    }
    lines.push(format!("{}: {}", key, format_show_value(value)));
}

fn format_show_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(items) => {
            if items.is_empty() {
                "[]".to_string()
            } else {
                items
                    .iter()
                    .map(format_show_value)
                    .collect::<Vec<_>>()
                    .join(", ")
            }
        }
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::Object(_) => serde_json::to_string(value).unwrap_or_default(),
        Value::Null => String::new(),
    }
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
            println!(
                "{}",
                crate::output::append_profile_to_rendered(message, &result)
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        PreparedSource, SourceOrigin, filter_skill_show_level, parse_github_tree_source,
        render_skill_show_for_table, resolve_add_targets,
    };
    use serde_json::json;
    use std::path::Path;

    #[test]
    fn skill_show_table_renders_complete_skill_information() {
        let result = json!({
            "name": "demo-skill",
            "description": "Demo skill",
            "uri": "viking://agent/skills/demo-skill",
            "root_uri": "viking://agent/skills/demo-skill",
            "skill_md_uri": "viking://agent/skills/demo-skill/SKILL.md",
            "tags": ["test"],
            "allowed_tools": ["shell"],
            "abstract": "name: demo-skill\ndescription: Demo skill",
            "overview": "Overview text",
            "content": "# demo-skill\n\nUse it.",
            "files": [
                {
                    "name": "SKILL.md",
                    "path": "SKILL.md",
                    "uri": "viking://agent/skills/demo-skill/SKILL.md",
                    "kind": "definition",
                    "is_dir": false
                },
                {
                    "name": "helper.md",
                    "path": "references/helper.md",
                    "uri": "viking://agent/skills/demo-skill/references/helper.md",
                    "kind": "auxiliary",
                    "is_dir": false
                }
            ],
            "source": {
                "tracked": false,
                "message": "Skill source metadata is not tracked yet."
            }
        });

        let rendered = render_skill_show_for_table(&result).expect("skill show table");
        assert!(rendered.contains("[metadata]"));
        assert!(rendered.contains("name: demo-skill"));
        assert!(rendered.contains("[L0 abstract]"));
        assert!(rendered.contains("[L1 overview]"));
        assert!(rendered.contains("[L2 SKILL.md]"));
        assert!(rendered.contains("[auxiliary files]"));
        assert!(rendered.contains("references/helper.md [auxiliary]"));
        assert!(rendered.contains("[source]"));
    }

    #[test]
    fn skill_show_level_one_filters_other_levels_before_rendering() {
        let mut result = json!({
            "name": "demo-skill",
            "skill_md_uri": "viking://agent/skills/demo-skill/SKILL.md",
            "abstract": "L0 text",
            "overview": "L1 text",
            "content": "# L2 text"
        });

        filter_skill_show_level(&mut result, 1);

        let rendered = render_skill_show_for_table(&result).expect("skill show table");
        assert!(!rendered.contains("[L0 abstract]"));
        assert!(!rendered.contains("L0 text"));
        assert!(rendered.contains("[L1 overview]"));
        assert!(rendered.contains("L1 text"));
        assert!(!rendered.contains("[L2 SKILL.md]"));
        assert!(!rendered.contains("# L2 text"));
    }

    #[test]
    fn github_tree_skill_url_resolves_to_repo_and_subdir() {
        let source = parse_github_tree_source(
            "https://github.com/anthropics/skills/tree/main/skills/algorithmic-art",
        )
        .expect("github tree source");

        assert_eq!(source.clone_url, "https://github.com/anthropics/skills.git");
        assert_eq!(source.ref_name.as_deref(), Some("main"));
        assert_eq!(
            source.subdir.as_deref(),
            Some(Path::new("skills/algorithmic-art"))
        );
    }

    #[test]
    fn default_add_targets_expand_skill_collection_directory() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let root = temp_dir.path().join("skills");
        let skill_a = root.join("skill-a");
        let skill_b = root.join("skill-b");
        std::fs::create_dir_all(&skill_a).expect("skill-a dir");
        std::fs::create_dir_all(&skill_b).expect("skill-b dir");
        std::fs::write(
            skill_a.join("SKILL.md"),
            "---\nname: skill-a\ndescription: A\n---\n",
        )
        .expect("skill-a md");
        std::fs::write(
            skill_b.join("SKILL.md"),
            "---\nname: skill-b\ndescription: B\n---\n",
        )
        .expect("skill-b md");

        let source = PreparedSource::Path {
            path: root,
            _temp_dir: None,
            origin: SourceOrigin::Local {
                source: temp_dir.path().to_string_lossy().to_string(),
            },
        };
        let targets = resolve_add_targets(&source, &[]).expect("targets");

        assert_eq!(targets.len(), 2);
        assert!(
            targets
                .iter()
                .any(|target| target.data.ends_with("skill-a"))
        );
        assert!(
            targets
                .iter()
                .any(|target| target.data.ends_with("skill-b"))
        );
    }
}
