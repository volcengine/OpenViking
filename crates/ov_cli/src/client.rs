use reqwest::{Client as ReqwestClient, StatusCode};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::fs::File;
use std::path::Path;
use tempfile::{Builder, NamedTempFile};
use zip::CompressionMethod;
use zip::write::FileOptions;

use crate::error::{Error, Result};

/// High-level HTTP client for OpenViking API
#[derive(Clone)]
pub struct HttpClient {
    http: ReqwestClient,
    base_url: String,
    api_key: Option<String>,
    account: Option<String>,
    user: Option<String>,
    agent_id: Option<String>,
}

impl HttpClient {
    /// Create a new HTTP client
    pub fn new(
        base_url: impl Into<String>,
        api_key: Option<String>,
        agent_id: Option<String>,
        account: Option<String>,
        user: Option<String>,
        timeout_secs: f64,
    ) -> Self {
        let http = ReqwestClient::builder()
            .timeout(std::time::Duration::from_secs_f64(timeout_secs))
            .build()
            .expect("Failed to build HTTP client");

        Self {
            http,
            base_url: base_url.into().trim_end_matches('/').to_string(),
            api_key,
            account,
            user,
            agent_id,
        }
    }

    /// Zip a directory to a temporary file
    fn zip_directory(&self, dir_path: &Path) -> Result<NamedTempFile> {
        if !dir_path.is_dir() {
            return Err(Error::Network(format!(
                "Path {} is not a directory",
                dir_path.display()
            )));
        }

        let temp_file = Builder::new().suffix(".zip").tempfile()?;
        let file = File::create(temp_file.path())?;
        let mut zip = zip::ZipWriter::new(file);
        let options: FileOptions<'_, ()> =
            FileOptions::default().compression_method(CompressionMethod::Deflated);

        let walkdir = walkdir::WalkDir::new(dir_path);
        let base_name = dir_path.file_name().and_then(|n| n.to_str()).ok_or_else(|| {
            Error::InvalidPath(format!(
                "Non-UTF-8 directory name: {}",
                dir_path.to_string_lossy()
            ))
        })?;
        for entry in walkdir.into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(dir_path).unwrap_or(path);
                let name_str = name.to_str().ok_or_else(|| {
                    Error::InvalidPath(format!("Non-UTF-8 path: {}", name.to_string_lossy()))
                })?;
                let zip_name = if name_str.is_empty() {
                    base_name.to_string()
                } else {
                    format!("{}/{}", base_name, name_str)
                };
                zip.start_file(zip_name, options)?;
                let mut file = File::open(path)?;
                std::io::copy(&mut file, &mut zip)?;
            }
        }

        zip.finish()?;
        Ok(temp_file)
    }

    /// Upload a temporary file and return the temp_file_id
    async fn upload_temp_file(&self, file_path: &Path) -> Result<String> {
        let url = format!("{}/api/v1/resources/temp_upload", self.base_url);
        let file_name = file_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("temp_upload.zip");

        // Read file content
        let file_content = tokio::fs::read(file_path).await?;

        // Create multipart form
        let part = reqwest::multipart::Part::bytes(file_content).file_name(file_name.to_string());

        let part = part
            .mime_str("application/octet-stream")
            .map_err(|e| Error::Network(format!("Failed to set mime type: {}", e)))?;

        let form = reqwest::multipart::Form::new().part("file", part);

        let mut headers = self.build_headers();
        // Remove Content-Type: application/json, let reqwest set multipart/form-data automatically
        headers.remove(reqwest::header::CONTENT_TYPE);

        let response = self
            .http
            .post(&url)
            .headers(headers)
            .multipart(form)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        let result: Value = self.handle_response(response).await?;
        result
            .get("temp_file_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .ok_or_else(|| Error::Parse("Missing temp_file_id in response".to_string()))
    }

    fn build_headers(&self) -> reqwest::header::HeaderMap {
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(
            reqwest::header::CONTENT_TYPE,
            reqwest::header::HeaderValue::from_static("application/json"),
        );
        if let Some(api_key) = &self.api_key {
            if let Ok(value) = reqwest::header::HeaderValue::from_str(api_key) {
                headers.insert("X-API-Key", value);
            }
        }
        if let Some(agent_id) = &self.agent_id {
            if let Ok(value) = reqwest::header::HeaderValue::from_str(agent_id) {
                headers.insert("X-OpenViking-Agent", value);
            }
        }
        if let Some(account) = &self.account {
            if let Ok(value) = reqwest::header::HeaderValue::from_str(account) {
                headers.insert("X-OpenViking-Account", value);
            }
        }
        if let Some(user) = &self.user {
            if let Ok(value) = reqwest::header::HeaderValue::from_str(user) {
                headers.insert("X-OpenViking-User", value);
            }
        }
        headers
    }

    /// Make a GET request
    pub async fn get<T: DeserializeOwned>(
        &self,
        path: &str,
        params: &[(String, String)],
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, path);
        let response = self
            .http
            .get(&url)
            .headers(self.build_headers())
            .query(params)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        self.handle_response(response).await
    }

    /// Make a POST request
    pub async fn post<B: serde::Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, path);
        let response = self
            .http
            .post(&url)
            .headers(self.build_headers())
            .json(body)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        self.handle_response(response).await
    }

    /// Make a PUT request
    pub async fn put<B: serde::Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, path);
        let response = self
            .http
            .put(&url)
            .headers(self.build_headers())
            .json(body)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        self.handle_response(response).await
    }

    /// Make a DELETE request
    pub async fn delete<T: DeserializeOwned>(
        &self,
        path: &str,
        params: &[(String, String)],
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, path);
        let response = self
            .http
            .delete(&url)
            .headers(self.build_headers())
            .query(params)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        self.handle_response(response).await
    }

    /// Make a DELETE request with a JSON body
    pub async fn delete_with_body<B: serde::Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, path);
        let response = self
            .http
            .delete(&url)
            .headers(self.build_headers())
            .json(body)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        self.handle_response(response).await
    }

    async fn handle_response<T: DeserializeOwned>(&self, response: reqwest::Response) -> Result<T> {
        let status = response.status();

        // Handle empty response (204 No Content, etc.)
        if status == StatusCode::NO_CONTENT || status == StatusCode::ACCEPTED {
            return serde_json::from_value(Value::Null)
                .map_err(|e| Error::Parse(format!("Failed to parse empty response: {}", e)));
        }

        let json: Value = response
            .json()
            .await
            .map_err(|e| Error::Network(format!("Failed to parse JSON response: {}", e)))?;

        // Handle HTTP errors
        if !status.is_success() {
            let error_msg = json
                .get("error")
                .and_then(|e| e.get("message"))
                .and_then(|m| m.as_str())
                .map(|s| s.to_string())
                .or_else(|| {
                    json.get("detail")
                        .and_then(|d| d.as_str())
                        .map(|s| s.to_string())
                })
                .unwrap_or_else(|| format!("HTTP error {}", status));
            return Err(Error::Api(error_msg));
        }

        // Handle API errors (status == success but body has error)
        if let Some(error) = json.get("error") {
            if !error.is_null() {
                let code = error
                    .get("code")
                    .and_then(|c| c.as_str())
                    .unwrap_or("UNKNOWN");
                let message = error
                    .get("message")
                    .and_then(|m| m.as_str())
                    .unwrap_or("Unknown error");
                return Err(Error::Api(format!("[{}] {}", code, message)));
            }
        }

        // Extract result from wrapped response or use the whole response
        let result = if let Some(result) = json.get("result") {
            result.clone()
        } else {
            json
        };

        serde_json::from_value(result)
            .map_err(|e| Error::Parse(format!("Failed to deserialize response: {}", e)))
    }

    // ============ Content Methods ============

    pub async fn read(&self, uri: &str) -> Result<String> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/read", &params).await
    }

    pub async fn abstract_content(&self, uri: &str) -> Result<String> {
        let params = vec![("uri".to_string(), uri.to_string())];
        self.get("/api/v1/content/abstract", &params).await
    }

    pub async fn overview(&self, uri: &str) -> Result<String> {
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

    pub async fn reindex(
        &self,
        uri: &str,
        regenerate: bool,
        wait: bool,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
            "regenerate": regenerate,
            "wait": wait,
        });
        self.post("/api/v1/content/reindex", &body).await
    }

    /// Download file as raw bytes
    pub async fn get_bytes(&self, uri: &str) -> Result<Vec<u8>> {
        let url = format!("{}/api/v1/content/download", self.base_url);
        let params = vec![("uri".to_string(), uri.to_string())];

        let response = self
            .http
            .get(&url)
            .headers(self.build_headers())
            .query(&params)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        let status = response.status();
        if !status.is_success() {
            // Try to parse error message as JSON
            let json_result: Result<serde_json::Value> = response
                .json()
                .await
                .map_err(|e| Error::Network(format!("Failed to parse error response: {}", e)));

            let error_msg = match json_result {
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
                Err(_) => format!("HTTP error {}", status),
            };

            return Err(Error::Api(error_msg));
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

    pub async fn mkdir(&self, uri: &str) -> Result<()> {
        let body = serde_json::json!({ "uri": uri });
        let _: serde_json::Value = self.post("/api/v1/fs/mkdir", &body).await?;
        Ok(())
    }

    pub async fn rm(&self, uri: &str, recursive: bool) -> Result<()> {
        let params = vec![
            ("uri".to_string(), uri.to_string()),
            ("recursive".to_string(), recursive.to_string()),
        ];
        let _: serde_json::Value = self.delete("/api/v1/fs", &params).await?;
        Ok(())
    }

    pub async fn mv(&self, from_uri: &str, to_uri: &str) -> Result<()> {
        let body = serde_json::json!({
            "from_uri": from_uri,
            "to_uri": to_uri,
        });
        let _: serde_json::Value = self.post("/api/v1/fs/mv", &body).await?;
        Ok(())
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
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "target_uri": uri,
            "limit": node_limit,
            "score_threshold": threshold,
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
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "target_uri": uri,
            "session_id": session_id,
            "limit": node_limit,
            "score_threshold": threshold,
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
    ) -> Result<serde_json::Value> {
        let path_obj = Path::new(path);

        if path_obj.exists() {
            if path_obj.is_dir() {
                let source_name = path_obj
                    .file_name()
                    .and_then(|n| n.to_str())
                    .map(|s| s.to_string());
                let zip_file = self.zip_directory(path_obj)?;
                let temp_file_id = self.upload_temp_file(zip_file.path()).await?;

                let body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "source_name": source_name,
                    "to": to,
                    "parent": parent,
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
                });

                self.post("/api/v1/resources", &body).await
            } else if path_obj.is_file() {
                let source_name = path_obj
                    .file_stem()
                    .and_then(|n| n.to_str())
                    .map(|s| s.to_string());
                let temp_file_id = self.upload_temp_file(path_obj).await?;

                let body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "source_name": source_name,
                    "to": to,
                    "parent": parent,
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
                });

                self.post("/api/v1/resources", &body).await
            } else {
                let body = serde_json::json!({
                    "path": path,
                    "to": to,
                    "parent": parent,
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
                });

                self.post("/api/v1/resources", &body).await
            }
        } else {
            let body = serde_json::json!({
                "path": path,
                "to": to,
                "parent": parent,
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
            });

            self.post("/api/v1/resources", &body).await
        }
    }

    pub async fn add_skill(
        &self,
        data: &str,
        wait: bool,
        timeout: Option<f64>,
    ) -> Result<serde_json::Value> {
        let path_obj = Path::new(data);

        if path_obj.exists() {
            if path_obj.is_dir() {
                let zip_file = self.zip_directory(path_obj)?;
                let temp_file_id = self.upload_temp_file(zip_file.path()).await?;

                let body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "wait": wait,
                    "timeout": timeout,
                });
                self.post("/api/v1/skills", &body).await
            } else if path_obj.is_file() {
                let temp_file_id = self.upload_temp_file(path_obj).await?;

                let body = serde_json::json!({
                    "temp_file_id": temp_file_id,
                    "wait": wait,
                    "timeout": timeout,
                });
                self.post("/api/v1/skills", &body).await
            } else {
                let body = serde_json::json!({
                    "data": data,
                    "wait": wait,
                    "timeout": timeout,
                });
                self.post("/api/v1/skills", &body).await
            }
        } else {
            let body = serde_json::json!({
                "data": data,
                "wait": wait,
                "timeout": timeout,
            });
            self.post("/api/v1/skills", &body).await
        }
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

    pub async fn export_ovpack(&self, uri: &str, to: &str) -> Result<String> {
        let body = serde_json::json!({
            "uri": uri,
        });

        let url = format!("{}/api/v1/pack/export", self.base_url);
        let response = self
            .http
            .post(&url)
            .headers(self.build_headers())
            .json(&body)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        let status = response.status();
        if !status.is_success() {
            // Try to parse error message as JSON
            let json_result: Result<serde_json::Value> = response
                .json()
                .await
                .map_err(|e| Error::Network(format!("Failed to parse error response: {}", e)));

            let error_msg = match json_result {
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
                Err(_) => format!("HTTP error {}", status),
            };

            return Err(Error::Api(error_msg));
        }

        // Download the file content
        let bytes = response
            .bytes()
            .await
            .map_err(|e| Error::Network(format!("Failed to read response bytes: {}", e)))?;

        // Determine target path
        let to_path = Path::new(to);
        let final_path = if to_path.is_dir() {
            let base_name = uri.trim_end_matches('/').split('/').last().unwrap_or("export");
            to_path.join(format!("{}.ovpack", base_name))
        } else if !to.ends_with(".ovpack") {
            Path::new(&format!("{}.ovpack", to)).to_path_buf()
        } else {
            to_path.to_path_buf()
        };

        // Ensure parent directory exists
        if let Some(parent) = final_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Write file
        std::fs::write(&final_path, bytes)?;

        Ok(final_path.to_string_lossy().to_string())
    }

    pub async fn import_ovpack(
        &self,
        file_path: &str,
        parent: &str,
        force: bool,
        vectorize: bool,
    ) -> Result<serde_json::Value> {
        let file_path_obj = Path::new(file_path);

        if !file_path_obj.exists() {
            return Err(Error::Client(format!(
                "Local ovpack file not found: {}",
                file_path
            )));
        }
        if !file_path_obj.is_file() {
            return Err(Error::Client(format!(
                "Path is not a file: {}",
                file_path
            )));
        }

        let temp_file_id = self.upload_temp_file(file_path_obj).await?;
        let body = serde_json::json!({
            "temp_file_id": temp_file_id,
            "parent": parent,
            "force": force,
            "vectorize": vectorize,
        });
        self.post("/api/v1/pack/import", &body).await
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

    pub async fn admin_list_users(&self, account_id: &str) -> Result<Value> {
        let path = format!("/api/v1/admin/accounts/{}/users", account_id);
        self.get(&path, &[]).await
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
}

#[cfg(test)]
mod tests {
    use super::HttpClient;
    use serde_json::json;

    #[test]
    fn build_headers_includes_tenant_identity_headers() {
        let client = HttpClient::new(
            "http://localhost:1933",
            Some("test-key".to_string()),
            Some("assistant-1".to_string()),
            Some("acme".to_string()),
            Some("alice".to_string()),
            5.0,
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
                .get("X-OpenViking-Agent")
                .and_then(|value| value.to_str().ok()),
            Some("assistant-1")
        );
        assert_eq!(
            headers
                .get("X-OpenViking-Account")
                .and_then(|value| value.to_str().ok()),
            Some("acme")
        );
        assert_eq!(
            headers
                .get("X-OpenViking-User")
                .and_then(|value| value.to_str().ok()),
            Some("alice")
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
}
