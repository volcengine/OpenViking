use serde::de::DeserializeOwned;
use serde_json::Value;
use std::env;
use std::path::Path;

pub use crate::base_client::{BaseClient, FileUploader, TimeoutConfig};

use crate::error::{Error, Result};

// ============ HttpClient ============

/// High-level HTTP client for OpenViking API
#[derive(Clone)]
pub struct HttpClient {
    base: BaseClient,
}

impl HttpClient {
    pub fn new(
        base_url: impl Into<String>,
        api_key: Option<String>,
        account: Option<String>,
        user: Option<String>,
        timeout_secs: f64,
        profile_enabled: bool,
        extra_headers: Option<std::collections::HashMap<String, String>>,
    ) -> Self {
        Self {
            base: BaseClient::new(
                base_url,
                api_key,
                account,
                user,
                timeout_secs,
                profile_enabled,
                extra_headers,
            ),
        }
    }

    pub fn user_id(&self) -> Option<&str> {
        self.base.user_id()
    }

    pub fn api_key(&self) -> Option<&str> {
        self.base.api_key()
    }

    fn upload_mode(&self) -> Option<String> {
        match env::var("OPENVIKING_UPLOAD_MODE") {
            Ok(value) => {
                let normalized = value.trim().to_ascii_lowercase();
                if normalized == "shared" || normalized == "local" {
                    Some(normalized)
                } else {
                    None
                }
            }
            Err(_) => None,
        }
    }

    // ============ HTTP Methods ============

    pub async fn get<T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        params: &[(String, String)],
    ) -> Result<T> {
        self.base.get(path, params).await
    }

    pub async fn post<B: serde::Serialize, T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.base.post(path, body).await
    }

    pub async fn put<B: serde::Serialize, T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.base.put(path, body).await
    }

    pub async fn delete<T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        params: &[(String, String)],
    ) -> Result<T> {
        self.base.delete(path, params).await
    }

    pub async fn delete_with_body<B: serde::Serialize, T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.base.delete_with_body(path, body).await
    }

    pub async fn patch<B: serde::Serialize, T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        body: &B,
        params: &[(String, String)],
    ) -> Result<T> {
        self.base.patch(path, body, params).await
    }

    pub async fn post_with_query<B: serde::Serialize, T: DeserializeOwned + 'static>(
        &self,
        path: &str,
        body: &B,
        params: &[(String, String)],
    ) -> Result<T> {
        self.base.post_with_query(path, body, params).await
    }

    // ============ File Helper Methods ============

    fn create_uploader(&self) -> FileUploader<'_> {
        FileUploader::new(&self.base).with_upload_mode(self.upload_mode())
    }

    fn zip_directory(
        &self,
        dir_path: &Path,
        ignore_dirs: Option<&str>,
    ) -> Result<tempfile::NamedTempFile> {
        self.create_uploader().zip_directory(dir_path, ignore_dirs)
    }

    fn zip_directory_with_progress(
        &self,
        dir_path: &Path,
        verbose: bool,
        ignore_dirs: Option<&str>,
    ) -> Result<tempfile::NamedTempFile> {
        self.create_uploader()
            .zip_directory_with_progress(dir_path, verbose, ignore_dirs)
    }

    async fn upload_temp_file(&self, file_path: &Path) -> Result<String> {
        self.create_uploader().upload_temp_file(file_path).await
    }

    async fn upload_temp_file_with_progress(
        &self,
        file_path: &Path,
        verbose: bool,
    ) -> Result<String> {
        self.create_uploader()
            .upload_temp_file_with_progress(file_path, verbose)
            .await
    }

    // ============ Content Methods ============

    pub async fn read(&self, uri: &str) -> Result<String> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/read", &params).await
    }

    pub async fn read_profiled(&self, uri: &str) -> Result<Value> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/read", &params).await
    }

    pub async fn abstract_content(&self, uri: &str) -> Result<String> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/abstract", &params).await
    }

    pub async fn abstract_content_profiled(&self, uri: &str) -> Result<Value> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/abstract", &params).await
    }

    pub async fn overview(&self, uri: &str) -> Result<String> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/overview", &params).await
    }

    pub async fn overview_profiled(&self, uri: &str) -> Result<Value> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/overview", &params).await
    }

    pub async fn write(
        &self,
        uri: &str,
        content: &str,
        mode: &str,
        wait: bool,
        timeout: Option<f64>,
    ) -> Result<serde_json::Value> {
        let body = Self::build_write_body(uri, content, mode, wait, timeout);
        self.post("/api/v1/content/write", &body).await
    }

    fn build_write_body(
        uri: &str,
        content: &str,
        mode: &str,
        wait: bool,
        timeout: Option<f64>,
    ) -> Value {
        serde_json::json!({
            "uri": uri,
            "content": content,
            "mode": mode,
            "wait": wait,
            "timeout": timeout,
        })
    }

    pub async fn reindex(&self, uri: &str, mode: &str, wait: bool) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
            "mode": mode,
            "wait": wait,
        });
        self.post("/api/v1/content/reindex", &body).await
    }

    pub async fn consistency(&self, uri: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
        });
        self.post("/api/v1/system/consistency", &body).await
    }

    pub async fn backend_sync_status(&self, uri: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
        });
        self.post("/api/v1/system/backend/sync-status", &body).await
    }

    pub async fn backend_sync_retry(&self, uri: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
        });
        self.post("/api/v1/system/backend/sync-retry", &body).await
    }

    /// Download file as raw bytes
    pub async fn get_bytes(&self, uri: &str) -> Result<Vec<u8>> {
        let url = format!("{}/api/v1/content/download", self.base.base_url);
        let params = vec![
            ("uri".to_string(), uri.to_string()),
            ("profile".to_string(), "0".to_string()),
        ];

        let response = self
            .base
            .http
            .get(&url)
            .headers(self.base.build_headers())
            .query(&params)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        let status = response.status();
        if !status.is_success() {
            let bytes = response
                .bytes()
                .await
                .map_err(|e| Error::Network(format!("Failed to read error response: {}", e)))?;

            let error_msg = match serde_json::from_slice::<serde_json::Value>(&bytes) {
                Ok(json) => json
                    .get("error")
                    .and_then(|e| e.get("message"))
                    .and_then(|m| m.as_str())
                    .map(|s| s.to_string())
                    .or_else(|| {
                        json.get("detail")
                            .and_then(|d| d.as_str())
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_else(|| format!("HTTP error {}", status)),
                Err(_) => {
                    let body_str = String::from_utf8_lossy(&bytes);
                    format!("HTTP error {}\n\nRaw response body:\n{}", status, body_str)
                }
            };

            return Err(Error::api(error_msg));
        }

        response
            .bytes()
            .await
            .map(|b| b.to_vec())
            .map_err(|e| Error::Network(format!("Failed to read response bytes: {}", e)))
    }

    // ============ Filesystem Methods ============

    pub async fn ls(
        &self,
        uri: &str,
        simple: bool,
        recursive: bool,
        output: &str,
        abs_limit: i32,
        show_all_hidden: bool,
        node_limit: i32,
    ) -> Result<serde_json::Value> {
        let params = vec![
            ("uri".to_string(), uri.to_string()),
            ("simple".to_string(), simple.to_string()),
            ("recursive".to_string(), recursive.to_string()),
            ("output".to_string(), output.to_string()),
            ("abs_limit".to_string(), abs_limit.to_string()),
            ("show_all_hidden".to_string(), show_all_hidden.to_string()),
            ("node_limit".to_string(), node_limit.to_string()),
        ];
        self.get("/api/v1/fs/ls", &params).await
    }

    pub async fn tree(
        &self,
        uri: &str,
        output: &str,
        abs_limit: i32,
        show_all_hidden: bool,
        node_limit: i32,
        level_limit: i32,
    ) -> Result<serde_json::Value> {
        let params = vec![
            ("uri".to_string(), uri.to_string()),
            ("output".to_string(), output.to_string()),
            ("abs_limit".to_string(), abs_limit.to_string()),
            ("show_all_hidden".to_string(), show_all_hidden.to_string()),
            ("node_limit".to_string(), node_limit.to_string()),
            ("level_limit".to_string(), level_limit.to_string()),
        ];
        self.get("/api/v1/fs/tree", &params).await
    }

    pub async fn mkdir(&self, uri: &str, description: Option<&str>) -> Result<serde_json::Value> {
        let body = match description {
            Some(description) => serde_json::json!({ "uri": uri, "description": description }),
            None => serde_json::json!({ "uri": uri }),
        };
        self.post("/api/v1/fs/mkdir", &body).await
    }

    pub async fn rm(&self, uri: &str, recursive: bool) -> Result<serde_json::Value> {
        let params = vec![
            ("uri".to_string(), uri.to_string()),
            ("recursive".to_string(), recursive.to_string()),
        ];
        self.delete("/api/v1/fs", &params).await
    }

    pub async fn mv(&self, from_uri: &str, to_uri: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "from_uri": from_uri,
            "to_uri": to_uri,
        });
        self.post("/api/v1/fs/mv", &body).await
    }

    pub async fn stat(&self, uri: &str) -> Result<serde_json::Value> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/fs/stat", &params).await
    }

    // ============ Search Methods ============

    pub async fn find(
        &self,
        query: String,
        uri: String,
        node_limit: i32,
        threshold: Option<f64>,
        since: Option<String>,
        until: Option<String>,
        time_field: Option<String>,
        level: Option<Vec<i32>>,
        context_type: Option<Vec<String>>,
        peer_id: Option<String>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "target_uri": uri,
            "limit": node_limit,
            "score_threshold": threshold,
            "since": since,
            "until": until,
            "time_field": time_field,
            "level": level,
            "context_type": context_type,
            "peer_id": peer_id,
        });
        self.post("/api/v1/search/find", &body).await
    }

    pub async fn search(
        &self,
        query: String,
        uri: String,
        session_id: Option<String>,
        node_limit: i32,
        threshold: Option<f64>,
        since: Option<String>,
        until: Option<String>,
        time_field: Option<String>,
        level: Option<Vec<i32>>,
        context_type: Option<Vec<String>>,
        peer_id: Option<String>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "target_uri": uri,
            "session_id": session_id,
            "limit": node_limit,
            "score_threshold": threshold,
            "since": since,
            "until": until,
            "time_field": time_field,
            "level": level,
            "context_type": context_type,
            "peer_id": peer_id,
        });
        self.post("/api/v1/search/search", &body).await
    }

    pub async fn grep(
        &self,
        uri: &str,
        exclude_uri: Option<String>,
        pattern: &str,
        ignore_case: bool,
        node_limit: i32,
        level_limit: i32,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
            "exclude_uri": exclude_uri,
            "pattern": pattern,
            "case_insensitive": ignore_case,
            "node_limit": node_limit,
            "level_limit": level_limit,
        });
        self.post("/api/v1/search/grep", &body).await
    }

    pub async fn glob(
        &self,
        pattern: &str,
        uri: &str,
        node_limit: i32,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "pattern": pattern,
            "uri": uri,
            "node_limit": node_limit,
        });
        self.post("/api/v1/search/glob", &body).await
    }

    // ============ Resource Methods ============

    pub async fn add_resource(
        &self,
        path: &str,
        to: Option<String>,
        parent: Option<String>,
        parent_auto_create: Option<String>,
        reason: &str,
        instruction: &str,
        wait: bool,
        timeout: Option<f64>,
        strict: bool,
        ignore_dirs: Option<String>,
        include: Option<String>,
        exclude: Option<String>,
        directly_upload_media: bool,
        watch_interval: f64,
        show_progress: bool,
        verbose: bool,
    ) -> Result<serde_json::Value> {
        let path_obj = Path::new(path);

        // Determine effective parent and create_parent flag.
        // Only send create_parent when the user explicitly selected
        // --parent-auto-create, so older servers that do not support the
        // field still accept the request.
        let (effective_parent, create_parent) = match (parent, parent_auto_create) {
            (Some(p), None) => (Some(p), false),
            (None, Some(p)) => (Some(p), true),
            (None, None) => (None, false),
            (Some(_), Some(_)) => unreachable!("handled in cli"),
        };

        let build_body = |base: serde_json::Value| {
            let mut body = base;
            if create_parent {
                body.as_object_mut()
                    .expect("add_resource request body must be an object")
                    .insert("create_parent".to_string(), serde_json::Value::Bool(true));
            }
            body
        };

        if path_obj.exists() {
            if path_obj.is_dir() {
                let source_name = path_obj
                    .file_name()
                    .and_then(|n| n.to_str())
                    .map(|s| s.to_string());
                let zip_file = if show_progress {
                    self.zip_directory_with_progress(path_obj, verbose, ignore_dirs.as_deref())?
                } else {
                    self.zip_directory(path_obj, ignore_dirs.as_deref())?
                };
                let temp_file_id = if show_progress {
                    self.upload_temp_file_with_progress(zip_file.path(), verbose)
                        .await?
                } else {
                    self.upload_temp_file(zip_file.path()).await?
                };

                let body = build_body(serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "source_name": source_name,
                    "to": to,
                    "parent": effective_parent,
                    "reason": reason,
                    "instruction": instruction,
                    "wait": wait,
                    "timeout": timeout,
                    "strict": strict,
                    "ignore_dirs": ignore_dirs,
                    "include": include,
                    "exclude": exclude,
                    "directly_upload_media": directly_upload_media,
                    "watch_interval": watch_interval,
                }));

                let dynamic_timeout =
                    TimeoutConfig::for_resource_processing().calculate(zip_file.path())?;
                self.base
                    .post_with_timeout("/api/v1/resources", &body, dynamic_timeout)
                    .await
            } else if path_obj.is_file() {
                let source_name = path_obj
                    .file_name()
                    .and_then(|n| n.to_str())
                    .map(|s| s.to_string());
                let temp_file_id = if show_progress {
                    self.upload_temp_file_with_progress(path_obj, verbose)
                        .await?
                } else {
                    self.upload_temp_file(path_obj).await?
                };

                let body = build_body(serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "source_name": source_name,
                    "to": to,
                    "parent": effective_parent,
                    "reason": reason,
                    "instruction": instruction,
                    "wait": wait,
                    "timeout": timeout,
                    "strict": strict,
                    "ignore_dirs": ignore_dirs,
                    "include": include,
                    "exclude": exclude,
                    "directly_upload_media": directly_upload_media,
                    "watch_interval": watch_interval,
                }));

                let dynamic_timeout =
                    TimeoutConfig::for_resource_processing().calculate(path_obj)?;
                self.base
                    .post_with_timeout("/api/v1/resources", &body, dynamic_timeout)
                    .await
            } else {
                let body = build_body(serde_json::json!({
                    "path": path,
                    "to": to,
                    "parent": effective_parent,
                    "reason": reason,
                    "instruction": instruction,
                    "wait": wait,
                    "timeout": timeout,
                    "strict": strict,
                    "ignore_dirs": ignore_dirs,
                    "include": include,
                    "exclude": exclude,
                    "directly_upload_media": directly_upload_media,
                    "watch_interval": watch_interval,
                }));

                self.post("/api/v1/resources", &body).await
            }
        } else {
            let body = build_body(serde_json::json!({
                "path": path,
                "to": to,
                "parent": effective_parent,
                "reason": reason,
                "instruction": instruction,
                "wait": wait,
                "timeout": timeout,
                "strict": strict,
                "ignore_dirs": ignore_dirs,
                "include": include,
                "exclude": exclude,
                "directly_upload_media": directly_upload_media,
                "watch_interval": watch_interval,
            }));

            self.post("/api/v1/resources", &body).await
        }
    }

    pub async fn add_skill(
        &self,
        data: &str,
        wait: bool,
        timeout: Option<f64>,
        show_progress: bool,
        verbose: bool,
        source_metadata: Option<Value>,
    ) -> Result<serde_json::Value> {
        let path_obj = Path::new(data);

        if path_obj.exists() {
            if path_obj.is_dir() {
                let zip_file = if show_progress {
                    self.zip_directory_with_progress(path_obj, verbose, None)?
                } else {
                    self.zip_directory(path_obj, None)?
                };
                let temp_file_id = if show_progress {
                    self.upload_temp_file_with_progress(zip_file.path(), verbose)
                        .await?
                } else {
                    self.upload_temp_file(zip_file.path()).await?
                };

                let mut body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "wait": wait,
                    "timeout": timeout,
                });
                if let Some(source_metadata) = source_metadata.clone() {
                    body["source_metadata"] = source_metadata;
                }
                let dynamic_timeout =
                    TimeoutConfig::for_resource_processing().calculate(zip_file.path())?;
                self.base
                    .post_with_timeout("/api/v1/skills", &body, dynamic_timeout)
                    .await
            } else if path_obj.is_file() {
                let temp_file_id = if show_progress {
                    self.upload_temp_file_with_progress(path_obj, verbose)
                        .await?
                } else {
                    self.upload_temp_file(path_obj).await?
                };

                let mut body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "wait": wait,
                    "timeout": timeout,
                });
                if let Some(source_metadata) = source_metadata.clone() {
                    body["source_metadata"] = source_metadata;
                }
                let dynamic_timeout =
                    TimeoutConfig::for_resource_processing().calculate(path_obj)?;
                self.base
                    .post_with_timeout("/api/v1/skills", &body, dynamic_timeout)
                    .await
            } else {
                let mut body = serde_json::json!({
                    "data": data,
                    "wait": wait,
                    "timeout": timeout,
                });
                if let Some(source_metadata) = source_metadata.clone() {
                    body["source_metadata"] = source_metadata;
                }
                self.post("/api/v1/skills", &body).await
            }
        } else {
            let mut body = serde_json::json!({
                "data": data,
                "wait": wait,
                "timeout": timeout,
            });
            if let Some(source_metadata) = source_metadata {
                body["source_metadata"] = source_metadata;
            }
            self.post("/api/v1/skills", &body).await
        }
    }

    pub async fn skills_list(&self, node_limit: i32) -> Result<serde_json::Value> {
        let params = vec![("node_limit".to_string(), node_limit.to_string())];
        self.get("/api/v1/skills", &params).await
    }

    pub async fn skill_show(
        &self,
        name: &str,
        include_content: bool,
        include_files: bool,
        include_source: bool,
        level: Option<i32>,
    ) -> Result<serde_json::Value> {
        let path = format!("/api/v1/skills/{}", name);
        let mut params = vec![
            ("include_content".to_string(), include_content.to_string()),
            ("include_files".to_string(), include_files.to_string()),
            ("include_source".to_string(), include_source.to_string()),
        ];
        if let Some(level) = level {
            params.push(("level".to_string(), level.to_string()));
        }
        self.get(&path, &params).await
    }

    pub async fn skill_find(
        &self,
        query: &str,
        node_limit: i32,
        threshold: Option<f64>,
        level: Option<Vec<i32>>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "limit": node_limit,
            "score_threshold": threshold,
            "level": level,
        });
        self.post("/api/v1/skills/find", &body).await
    }

    pub async fn skill_validate(&self, path: &str, strict: bool) -> Result<serde_json::Value> {
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
        let body = serde_json::json!({
            "data": content,
            "strict": strict,
            "source_path": skill_file.to_string_lossy(),
            "skill_dir_name": skill_dir_name,
        });
        self.post("/api/v1/skills/validate", &body).await
    }

    pub async fn skill_update(
        &self,
        name: &str,
        data: &str,
        wait: bool,
        timeout: Option<f64>,
        show_progress: bool,
        verbose: bool,
        source_metadata: Option<Value>,
    ) -> Result<serde_json::Value> {
        let endpoint = format!("/api/v1/skills/{}", name);
        let path_obj = Path::new(data);

        if path_obj.exists() {
            if path_obj.is_dir() {
                let zip_file = if show_progress {
                    self.zip_directory_with_progress(path_obj, verbose, None)?
                } else {
                    self.zip_directory(path_obj, None)?
                };
                let temp_file_id = if show_progress {
                    self.upload_temp_file_with_progress(zip_file.path(), verbose)
                        .await?
                } else {
                    self.upload_temp_file(zip_file.path()).await?
                };
                let mut body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "wait": wait,
                    "timeout": timeout,
                });
                if let Some(source_metadata) = source_metadata.clone() {
                    body["source_metadata"] = source_metadata;
                }
                self.put(&endpoint, &body).await
            } else if path_obj.is_file() {
                let temp_file_id = if show_progress {
                    self.upload_temp_file_with_progress(path_obj, verbose)
                        .await?
                } else {
                    self.upload_temp_file(path_obj).await?
                };
                let mut body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "wait": wait,
                    "timeout": timeout,
                });
                if let Some(source_metadata) = source_metadata.clone() {
                    body["source_metadata"] = source_metadata;
                }
                self.put(&endpoint, &body).await
            } else {
                let mut body = serde_json::json!({
                    "data": data,
                    "wait": wait,
                    "timeout": timeout,
                });
                if let Some(source_metadata) = source_metadata.clone() {
                    body["source_metadata"] = source_metadata;
                }
                self.put(&endpoint, &body).await
            }
        } else {
            let mut body = serde_json::json!({
                "data": data,
                "wait": wait,
                "timeout": timeout,
            });
            if let Some(source_metadata) = source_metadata {
                body["source_metadata"] = source_metadata;
            }
            self.put(&endpoint, &body).await
        }
    }

    pub async fn skill_remove(&self, name: &str) -> Result<serde_json::Value> {
        let path = format!("/api/v1/skills/{}", name);
        self.delete(&path, &[]).await
    }

    // ============ Task Methods ============

    pub async fn get_task(&self, task_id: &str) -> Result<serde_json::Value> {
        let path = format!("/api/v1/tasks/{}", task_id);
        self.get(&path, &[]).await
    }

    pub async fn list_tasks(
        &self,
        task_type: Option<&str>,
        status: Option<&str>,
    ) -> Result<serde_json::Value> {
        let mut params: Vec<(String, String)> = Vec::new();
        if let Some(t) = task_type {
            params.push(("task_type".to_string(), t.to_string()));
        }
        if let Some(s) = status {
            params.push(("status".to_string(), s.to_string()));
        }
        self.get("/api/v1/tasks", &params).await
    }

    // ============ Relation Methods ============

    pub async fn relations(&self, uri: &str) -> Result<serde_json::Value> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/relations", &params).await
    }

    pub async fn link(
        &self,
        from_uri: &str,
        to_uris: &[String],
        reason: &str,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "from_uri": from_uri,
            "to_uris": to_uris,
            "reason": reason,
        });
        self.post("/api/v1/relations/link", &body).await
    }

    pub async fn unlink(&self, from_uri: &str, to_uri: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "from_uri": from_uri,
            "to_uri": to_uri,
        });
        self.delete_with_body("/api/v1/relations/link", &body).await
    }

    // ============ Pack Methods ============

    async fn download_pack(
        &self,
        endpoint: &str,
        body: serde_json::Value,
        to: &str,
        default_name: &str,
    ) -> Result<String> {
        let url = format!("{}{}", self.base.base_url, endpoint);
        let response = self
            .base
            .http
            .post(&url)
            .headers(self.base.build_headers())
            .json(&body)
            .query(&[("profile", "0")])
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        let status = response.status();
        if !status.is_success() {
            let bytes = response
                .bytes()
                .await
                .map_err(|e| Error::Network(format!("Failed to read error response: {}", e)))?;

            let error_msg = match serde_json::from_slice::<serde_json::Value>(&bytes) {
                Ok(json) => json
                    .get("error")
                    .and_then(|e| e.get("message"))
                    .and_then(|m| m.as_str())
                    .map(|s| s.to_string())
                    .or_else(|| {
                        json.get("detail")
                            .and_then(|d| d.as_str())
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_else(|| format!("HTTP error {}", status)),
                Err(_) => {
                    let body_str = String::from_utf8_lossy(&bytes);
                    format!("HTTP error {}\n\nRaw response body:\n{}", status, body_str)
                }
            };

            return Err(Error::api(error_msg));
        }

        let bytes = response
            .bytes()
            .await
            .map_err(|e| Error::Network(format!("Failed to read response bytes: {}", e)))?;

        let to_path = Path::new(to);
        let final_path = if to_path.is_dir() {
            to_path.join(format!("{}.ovpack", default_name))
        } else if !to.ends_with(".ovpack") {
            Path::new(&format!("{}.ovpack", to)).to_path_buf()
        } else {
            to_path.to_path_buf()
        };

        if let Some(parent) = final_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        std::fs::write(&final_path, bytes)?;

        Ok(final_path.to_string_lossy().to_string())
    }

    pub async fn export_ovpack(
        &self,
        uri: &str,
        to: &str,
        include_vectors: bool,
    ) -> Result<String> {
        let body = serde_json::json!({
            "uri": uri,
            "include_vectors": include_vectors,
        });
        let base_name = uri
            .trim_end_matches('/')
            .split('/')
            .last()
            .unwrap_or("export");
        self.download_pack("/api/v1/pack/export", body, to, base_name)
            .await
    }

    pub async fn backup_ovpack(&self, to: &str, include_vectors: bool) -> Result<String> {
        self.download_pack(
            "/api/v1/pack/backup",
            serde_json::json!({"include_vectors": include_vectors}),
            to,
            "openviking-backup",
        )
        .await
    }

    pub async fn import_ovpack(
        &self,
        file_path: &str,
        parent: &str,
        on_conflict: Option<&str>,
        vector_mode: Option<&str>,
    ) -> Result<serde_json::Value> {
        let file_path_obj = Path::new(file_path);

        if !file_path_obj.exists() {
            return Err(Error::Client(format!(
                "Local ovpack file not found: {}",
                file_path
            )));
        }
        if !file_path_obj.is_file() {
            return Err(Error::Client(format!("Path is not a file: {}", file_path)));
        }

        let temp_file_id = self.upload_temp_file(file_path_obj).await?;
        let conflict_policy = on_conflict.unwrap_or("fail");
        let body = serde_json::json!({
            "temp_file_id": temp_file_id,
            "parent": parent,
            "on_conflict": conflict_policy,
            "vector_mode": vector_mode.unwrap_or("auto"),
        });
        self.post("/api/v1/pack/import", &body).await
    }

    pub async fn restore_ovpack(
        &self,
        file_path: &str,
        on_conflict: Option<&str>,
        vector_mode: Option<&str>,
    ) -> Result<serde_json::Value> {
        let file_path_obj = Path::new(file_path);

        if !file_path_obj.exists() {
            return Err(Error::Client(format!(
                "Local ovpack file not found: {}",
                file_path
            )));
        }
        if !file_path_obj.is_file() {
            return Err(Error::Client(format!("Path is not a file: {}", file_path)));
        }

        let temp_file_id = self.upload_temp_file(file_path_obj).await?;
        let conflict_policy = on_conflict.unwrap_or("fail");
        let body = serde_json::json!({
            "temp_file_id": temp_file_id,
            "on_conflict": conflict_policy,
            "vector_mode": vector_mode.unwrap_or("auto"),
        });
        self.post("/api/v1/pack/restore", &body).await
    }

    // ============ Admin Methods ============

    pub async fn admin_create_account(
        &self,
        account_id: &str,
        admin_user_id: &str,
    ) -> Result<Value> {
        let body = serde_json::json!({
            "account_id": account_id,
            "admin_user_id": admin_user_id,
        });
        self.post("/api/v1/admin/accounts", &body).await
    }

    pub async fn admin_list_accounts(&self) -> Result<Value> {
        self.get("/api/v1/admin/accounts", &[]).await
    }

    pub async fn admin_delete_account(&self, account_id: &str) -> Result<Value> {
        let path = format!("/api/v1/admin/accounts/{}", account_id);
        self.delete(&path, &[]).await
    }

    pub async fn admin_register_user(
        &self,
        account_id: &str,
        user_id: &str,
        role: &str,
    ) -> Result<Value> {
        let path = format!("/api/v1/admin/accounts/{}/users", account_id);
        let body = serde_json::json!({
            "user_id": user_id,
            "role": role,
        });
        self.post(&path, &body).await
    }

    pub async fn admin_list_users(
        &self,
        account_id: &str,
        limit: u32,
        name: Option<String>,
        role: Option<String>,
    ) -> Result<Value> {
        let path = format!("/api/v1/admin/accounts/{}/users", account_id);
        let mut params = vec![("limit".to_string(), limit.to_string())];
        if let Some(n) = name {
            params.push(("name".to_string(), n));
        }
        if let Some(r) = role {
            params.push(("role".to_string(), r));
        }
        self.get(&path, &params).await
    }

    pub async fn admin_remove_user(&self, account_id: &str, user_id: &str) -> Result<Value> {
        let path = format!("/api/v1/admin/accounts/{}/users/{}", account_id, user_id);
        self.delete(&path, &[]).await
    }

    pub async fn admin_set_role(
        &self,
        account_id: &str,
        user_id: &str,
        role: &str,
    ) -> Result<Value> {
        let path = format!(
            "/api/v1/admin/accounts/{}/users/{}/role",
            account_id, user_id
        );
        let body = serde_json::json!({ "role": role });
        self.put(&path, &body).await
    }

    pub async fn admin_regenerate_key(&self, account_id: &str, user_id: &str) -> Result<Value> {
        let path = format!(
            "/api/v1/admin/accounts/{}/users/{}/key",
            account_id, user_id
        );
        self.post(&path, &serde_json::json!({})).await
    }

    // ============ Debug Vector Methods ============

    /// Get paginated vector records
    pub async fn debug_vector_scroll(
        &self,
        limit: Option<u32>,
        cursor: Option<String>,
        uri_prefix: Option<String>,
    ) -> Result<(Vec<serde_json::Value>, Option<String>)> {
        let mut params = Vec::new();
        if let Some(l) = limit {
            params.push(("limit".to_string(), l.to_string()));
        }
        if let Some(c) = cursor {
            params.push(("cursor".to_string(), c));
        }
        if let Some(u) = uri_prefix {
            params.push(("uri".to_string(), u));
        }

        let result: serde_json::Value = self.get("/api/v1/debug/vector/scroll", &params).await?;
        let records = result["records"]
            .as_array()
            .ok_or_else(|| Error::Parse("Missing records in response".to_string()))?
            .clone();
        let next_cursor = result["next_cursor"].as_str().map(|s| s.to_string());

        Ok((records, next_cursor))
    }

    /// Get count of vector records
    pub async fn debug_vector_count(
        &self,
        filter: Option<&serde_json::Value>,
        uri_prefix: Option<String>,
    ) -> Result<u64> {
        let mut params = Vec::new();
        if let Some(f) = filter {
            params.push(("filter".to_string(), serde_json::to_string(f)?));
        }
        if let Some(u) = uri_prefix {
            params.push(("uri".to_string(), u));
        }

        let result: serde_json::Value = self.get("/api/v1/debug/vector/count", &params).await?;
        let count = result["count"]
            .as_u64()
            .ok_or_else(|| Error::Parse("Missing count in response".to_string()))?;

        Ok(count)
    }

    // ============ Privacy Config Methods ============

    pub async fn privacy_list_categories(&self) -> Result<serde_json::Value> {
        self.get("/api/v1/privacy-configs", &[]).await
    }

    pub async fn privacy_list_targets(&self, category: &str) -> Result<serde_json::Value> {
        let path = format!("/api/v1/privacy-configs/{}", category);
        self.get(&path, &[]).await
    }

    pub async fn privacy_get_current(
        &self,
        category: &str,
        target_key: &str,
    ) -> Result<serde_json::Value> {
        let path = format!("/api/v1/privacy-configs/{}/{}", category, target_key);
        self.get(&path, &[]).await
    }

    pub async fn privacy_upsert(
        &self,
        category: &str,
        target_key: &str,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value> {
        let path = format!("/api/v1/privacy-configs/{}/{}", category, target_key);
        self.post(&path, body).await
    }

    pub async fn privacy_list_versions(
        &self,
        category: &str,
        target_key: &str,
    ) -> Result<serde_json::Value> {
        let path = format!(
            "/api/v1/privacy-configs/{}/{}/versions",
            category, target_key
        );
        self.get(&path, &[]).await
    }

    pub async fn privacy_get_version(
        &self,
        category: &str,
        target_key: &str,
        version: i32,
    ) -> Result<serde_json::Value> {
        let path = format!(
            "/api/v1/privacy-configs/{}/{}/versions/{}",
            category, target_key, version
        );
        self.get(&path, &[]).await
    }

    pub async fn privacy_activate(
        &self,
        category: &str,
        target_key: &str,
        version: i32,
    ) -> Result<serde_json::Value> {
        let path = format!(
            "/api/v1/privacy-configs/{}/{}/activate",
            category, target_key
        );
        let body = serde_json::json!({ "version": version });
        self.post(&path, &body).await
    }

    // ============ Watch Management (RFC #2104) ============

    pub async fn list_watches(&self, active_only: bool) -> Result<serde_json::Value> {
        let mut params = vec![];
        if active_only {
            params.push(("active_only".to_string(), "true".to_string()));
        }
        self.get("/api/v1/watches", &params).await
    }

    pub async fn get_watch_by_id(&self, task_id: &str) -> Result<serde_json::Value> {
        let path = format!("/api/v1/watches/{}", task_id);
        self.get(&path, &[]).await
    }

    pub async fn get_watch_by_uri(&self, to_uri: &str) -> Result<serde_json::Value> {
        let params = vec![("to_uri".to_string(), to_uri.to_string())];
        self.get("/api/v1/watches", &params).await
    }

    pub async fn patch_watch_by_id(
        &self,
        task_id: &str,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value> {
        let path = format!("/api/v1/watches/{}", task_id);
        self.patch(&path, body, &[]).await
    }

    pub async fn patch_watch_by_uri(
        &self,
        to_uri: &str,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value> {
        let params = vec![("to_uri".to_string(), to_uri.to_string())];
        self.patch("/api/v1/watches", body, &params).await
    }

    pub async fn delete_watch_by_id(&self, task_id: &str) -> Result<serde_json::Value> {
        let path = format!("/api/v1/watches/{}", task_id);
        self.delete(&path, &[]).await
    }

    pub async fn delete_watch_by_uri(&self, to_uri: &str) -> Result<serde_json::Value> {
        let params = vec![("to_uri".to_string(), to_uri.to_string())];
        self.delete("/api/v1/watches", &params).await
    }

    pub async fn trigger_watch_by_id(&self, task_id: &str) -> Result<serde_json::Value> {
        let path = format!("/api/v1/watches/{}/trigger", task_id);
        let empty = serde_json::json!({});
        self.post(&path, &empty).await
    }

    pub async fn trigger_watch_by_uri(&self, to_uri: &str) -> Result<serde_json::Value> {
        let params = vec![("to_uri".to_string(), to_uri.to_string())];
        let empty = serde_json::json!({});
        self.post_with_query("/api/v1/watches/trigger", &empty, &params)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::{BaseClient, HttpClient, TimeoutConfig};
    use crate::base_client::api_error_from_envelope;
    use reqwest::StatusCode;
    use serde_json::json;
    use std::collections::HashMap;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::sync::oneshot;

    #[test]
    fn timeout_config_calculation() {
        let config = TimeoutConfig::new(60, 2.0);

        let temp_file = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(temp_file.path(), vec![0u8; 1024 * 1024]).unwrap();

        let timeout = config.calculate(temp_file.path()).unwrap();
        assert_eq!(timeout, std::time::Duration::from_secs(60));

        std::fs::write(temp_file.path(), vec![0u8; 40 * 1024 * 1024]).unwrap();

        let timeout = config.calculate(temp_file.path()).unwrap();
        assert_eq!(timeout, std::time::Duration::from_secs(80));
    }

    #[test]
    fn build_headers_includes_extra_headers_for_base_client() {
        let mut extra_headers = HashMap::new();
        extra_headers.insert("X-Custom-Header".to_string(), "custom-value".to_string());

        let client = BaseClient::new(
            "http://localhost:1933",
            Some("test-key".to_string()),
            Some("acme".to_string()),
            Some("alice".to_string()),
            5.0,
            true,
            Some(extra_headers),
        );

        let headers = client.build_headers();

        assert_eq!(
            headers
                .get("X-API-Key")
                .and_then(|value| value.to_str().ok()),
            Some("test-key")
        );
        assert_eq!(
            headers
                .get("X-Custom-Header")
                .and_then(|value| value.to_str().ok()),
            Some("custom-value")
        );
    }

    #[test]
    fn build_write_body_omits_removed_semantic_flags() {
        let body = HttpClient::build_write_body(
            "viking://resources/demo.md",
            "updated",
            "replace",
            true,
            Some(3.0),
        );

        assert_eq!(
            body,
            json!({
                "uri": "viking://resources/demo.md",
                "content": "updated",
                "mode": "replace",
                "wait": true,
                "timeout": 3.0,
            })
        );
        assert!(body.get("regenerate_semantics").is_none());
        assert!(body.get("revectorize").is_none());
    }

    #[tokio::test]
    async fn ls_does_not_send_display_time_query() {
        let (base_url, request_rx) = spawn_request_capture_server().await;
        let client = HttpClient::new(base_url, None, None, None, 5.0, false, None);

        client
            .ls("viking://resources", false, false, "agent", 256, false, 1)
            .await
            .expect("ls request should succeed");

        let request = request_rx.await.expect("request should be captured");
        assert!(request.starts_with("GET /api/v1/fs/ls?"));
        assert!(!request.contains("tz="));
        assert!(!request.contains("include_mod_time_iso="));
    }

    #[tokio::test]
    async fn tree_does_not_send_display_time_query() {
        let (base_url, request_rx) = spawn_request_capture_server().await;
        let client = HttpClient::new(base_url, None, None, None, 5.0, false, None);

        client
            .tree("viking://resources", "agent", 256, false, 1, 3)
            .await
            .expect("tree request should succeed");

        let request = request_rx.await.expect("request should be captured");
        assert!(request.starts_with("GET /api/v1/fs/tree?"));
        assert!(!request.contains("tz="));
        assert!(!request.contains("include_mod_time_iso="));
    }

    #[test]
    fn standard_error_envelope_formats_api_error() {
        let body = json!({
            "status": "error",
            "error": {
                "code": "PROCESSING_ERROR",
                "message": "Parse error: boom"
            }
        });

        assert_eq!(
            api_error_from_envelope(&body, StatusCode::INTERNAL_SERVER_ERROR),
            "[PROCESSING_ERROR] Parse error: boom"
        );
    }

    #[test]
    fn unwrap_result_preserves_profile_for_non_object_results() {
        let body = json!({
            "status": "ok",
            "result": [
                {"id": "1"}
            ],
            "profile": [
                "line one",
                "line two"
            ]
        });

        let result = crate::base_client::unwrap_success_envelope(body, true);

        assert_eq!(
            result,
            json!({
                "result": [
                    {"id": "1"}
                ],
                "profile": [
                    "line one",
                    "line two"
                ]
            })
        );
    }

    #[test]
    fn unwrap_result_drops_profile_for_scalar_typed_results() {
        let body = json!({
            "status": "ok",
            "result": "content",
            "profile": [
                "line one"
            ]
        });

        let result = crate::base_client::unwrap_success_envelope(body, false);

        assert_eq!(result, json!("content"));
    }

    async fn spawn_request_capture_server() -> (String, oneshot::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test server should bind");
        let addr = listener.local_addr().expect("test server should have addr");
        let (request_tx, request_rx) = oneshot::channel();

        tokio::spawn(async move {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let mut buffer = vec![0; 4096];
            let Ok(read) = stream.read(&mut buffer).await else {
                return;
            };
            let request = String::from_utf8_lossy(&buffer[..read]).to_string();
            let _ = request_tx.send(request);

            let body = r#"{"status":"ok","result":[]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            let _ = stream.write_all(response.as_bytes()).await;
        });

        (format!("http://{addr}"), request_rx)
    }
}
