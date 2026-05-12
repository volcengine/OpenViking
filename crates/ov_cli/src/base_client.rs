use reqwest::{Client as ReqwestClient, StatusCode};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::fs::File;
use std::path::Path;
use std::str::FromStr;
use tempfile::{Builder, NamedTempFile};
use zip::CompressionMethod;
use zip::write::FileOptions;

use indicatif::{ProgressBar, ProgressStyle};

use crate::error::{Error, Result};

pub fn api_error_from_envelope(json: &Value, status: StatusCode) -> String {
    let error_code = json
        .get("error")
        .and_then(|e| e.get("code"))
        .and_then(|c| c.as_str());
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

    match error_code {
        Some(code) => format!("[{}] {}", code, error_msg),
        None => error_msg,
    }
}

// ============ TimeoutConfig ============

/// Dynamic timeout calculator based on file size
#[derive(Debug, Clone, Copy)]
pub struct TimeoutConfig {
    min_timeout_secs: u64,
    seconds_per_mb: f64,
}

impl TimeoutConfig {
    pub const fn new(min_timeout_secs: u64, seconds_per_mb: f64) -> Self {
        Self {
            min_timeout_secs,
            seconds_per_mb,
        }
    }

    pub fn for_resource_processing() -> Self {
        Self::new(60, 5.0)
    }

    pub fn for_upload() -> Self {
        Self::new(60, 10.0)
    }

    pub fn calculate(&self, file_path: &Path) -> Result<std::time::Duration> {
        let file_size = std::fs::metadata(file_path)?.len();
        let file_size_mb = file_size as f64 / (1024.0 * 1024.0);
        let calculated_timeout = (file_size_mb * self.seconds_per_mb).ceil() as u64;
        let timeout_secs = std::cmp::max(self.min_timeout_secs, calculated_timeout);

        Ok(std::time::Duration::from_secs(timeout_secs))
    }
}

// ============ BaseClient ============

/// Low-level HTTP client with timeout control and header management
#[derive(Clone)]
pub struct BaseClient {
    pub(crate) http: ReqwestClient,
    pub(crate) base_url: String,
    pub(crate) api_key: Option<String>,
    pub(crate) account: Option<String>,
    pub(crate) user: Option<String>,
    pub(crate) agent_id: Option<String>,
    pub(crate) extra_headers: Option<std::collections::HashMap<String, String>>,
}

impl BaseClient {
    /// Create a new BaseClient with minimal configuration for health probing
    pub fn new_simple(base_url: impl Into<String>, timeout_secs: f64) -> Self {
        let http = ReqwestClient::builder()
            .timeout(std::time::Duration::from_secs_f64(timeout_secs))
            .build()
            .expect("Failed to build HTTP client");

        Self {
            http,
            base_url: base_url.into().trim_end_matches('/').to_string(),
            api_key: None,
            account: None,
            user: None,
            agent_id: None,
            extra_headers: None,
        }
    }

    pub fn new(
        base_url: impl Into<String>,
        api_key: Option<String>,
        agent_id: Option<String>,
        account: Option<String>,
        user: Option<String>,
        timeout_secs: f64,
        extra_headers: Option<std::collections::HashMap<String, String>>,
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
            extra_headers,
        }
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn user_id(&self) -> Option<&str> {
        self.user.as_deref()
    }

    pub fn agent_id(&self) -> Option<&str> {
        self.agent_id.as_deref()
    }

    pub fn api_key(&self) -> Option<&str> {
        self.api_key.as_deref()
    }

    pub fn build_headers(&self) -> reqwest::header::HeaderMap {
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
        if let Some(extra_headers) = &self.extra_headers {
            for (key, value) in extra_headers {
                if let Ok(header_name) = reqwest::header::HeaderName::from_str(key) {
                    if let Ok(header_value) = reqwest::header::HeaderValue::from_str(value) {
                        headers.insert(header_name, header_value);
                    }
                }
            }
        }
        headers
    }

    pub(crate) async fn handle_response<T: DeserializeOwned>(&self, response: reqwest::Response) -> Result<T> {
        let status = response.status();

        if status == StatusCode::NO_CONTENT || status == StatusCode::ACCEPTED {
            return serde_json::from_value(Value::Null)
                .map_err(|e| Error::Parse(format!("Failed to parse empty response: {}", e)));
        }

        let bytes = response
            .bytes()
            .await
            .map_err(|e| Error::Network(format!("Failed to read response body: {}", e)))?;

        let json: Value = match serde_json::from_slice(&bytes) {
            Ok(json) => json,
            Err(e) => {
                let body_str = String::from_utf8_lossy(&bytes);
                return Err(Error::Network(format!(
                    "Failed to parse JSON response: {}\n\nRaw response body:\n{}",
                    e, body_str
                )));
            }
        };

        if !status.is_success() {
            return Err(Error::Api(api_error_from_envelope(&json, status)));
        }

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

        let result = if let Some(result) = json.get("result") {
            result.clone()
        } else {
            json.clone()
        };

        serde_json::from_value(result).map_err(|e| {
            Error::Parse(format!(
                "Failed to deserialize response: {}\n\nJSON that failed to parse:\n{}",
                e, json
            ))
        })
    }

    pub(crate) fn create_client_with_timeout(&self, timeout: std::time::Duration) -> Result<ReqwestClient> {
        ReqwestClient::builder()
            .timeout(timeout)
            .build()
            .map_err(|e| Error::Network(format!("Failed to build HTTP client: {}", e)))
    }

    pub(crate) fn create_client_with_connect_timeout(
        &self,
        connect_timeout: std::time::Duration,
        timeout: std::time::Duration,
    ) -> Result<ReqwestClient> {
        ReqwestClient::builder()
            .connect_timeout(connect_timeout)
            .timeout(timeout)
            .build()
            .map_err(|e| Error::Network(format!("Failed to build HTTP client: {}", e)))
    }

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

    pub async fn post_with_timeout<B: serde::Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
        timeout: std::time::Duration,
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, path);
        let client = self.create_client_with_timeout(timeout)?;

        let response = client
            .post(&url)
            .headers(self.build_headers())
            .json(body)
            .send()
            .await
            .map_err(|e| Error::Network(format!("HTTP request failed: {}", e)))?;

        self.handle_response(response).await
    }

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
}

// ============ FileUploader ============

/// Handles file compression and upload logic
pub struct FileUploader<'a> {
    client: &'a BaseClient,
    upload_mode: Option<String>,
}

impl<'a> FileUploader<'a> {
    pub fn new(client: &'a BaseClient) -> Self {
        Self {
            client,
            upload_mode: None,
        }
    }

    pub fn with_upload_mode(mut self, upload_mode: Option<String>) -> Self {
        self.upload_mode = upload_mode;
        self
    }

    pub fn zip_directory(&self, dir_path: &Path) -> Result<NamedTempFile> {
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
        for entry in walkdir.into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(dir_path).unwrap_or(path);
                let name_str = name.to_str().ok_or_else(|| {
                    Error::InvalidPath(format!("Non-UTF-8 path: {}", name.to_string_lossy()))
                })?;
                zip.start_file(name_str, options)?;
                let mut file = File::open(path)?;
                std::io::copy(&mut file, &mut zip)?;
            }
        }

        zip.finish()?;
        Ok(temp_file)
    }

    pub fn zip_directory_with_progress(
        &self,
        dir_path: &Path,
        verbose: bool,
    ) -> Result<NamedTempFile> {
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

        let mut total_size = 0u64;
        let mut total_files = 0u64;
        let walkdir = walkdir::WalkDir::new(dir_path);
        for entry in walkdir.into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_file() {
                if let Ok(meta) = std::fs::metadata(path) {
                    total_size += meta.len();
                    total_files += 1;
                }
            }
        }

        let pb = if total_size > 0 {
            let pb = ProgressBar::new(total_size);
            pb.set_style(
                ProgressStyle::default_bar()
                    .template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {bytes}/{total_bytes} ({eta}) {msg}")
                    .unwrap_or_else(|_| ProgressStyle::default_bar())
                    .progress_chars("#>-"),
            );
            pb.set_message(format!("Compressing {} files", total_files));
            Some(pb)
        } else {
            let pb = ProgressBar::new_spinner();
            pb.set_message("Compressing files...");
            Some(pb)
        };

        let walkdir = walkdir::WalkDir::new(dir_path);
        for entry in walkdir.into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(dir_path).unwrap_or(path);
                let name_str = name.to_str().ok_or_else(|| {
                    Error::InvalidPath(format!("Non-UTF-8 path: {}", name.to_string_lossy()))
                })?;
                if verbose {
                    eprintln!("  Adding: {}", name_str);
                }
                zip.start_file(name_str, options)?;
                let mut file = File::open(path)?;
                let file_size = std::io::copy(&mut file, &mut zip)?;

                if let Some(pb) = &pb {
                    if pb.length().is_some() {
                        pb.inc(file_size);
                    }
                }
            }
        }

        zip.finish()?;

        let zip_size = std::fs::metadata(temp_file.path())?.len();
        let zip_size_mb = zip_size as f64 / 1024.0 / 1024.0;
        let original_size_mb = if total_size > 0 {
            total_size as f64 / 1024.0 / 1024.0
        } else {
            0.0
        };

        if let Some(pb) = pb {
            if total_size > 0 {
                pb.finish_with_message(format!("Compression complete: {:.2} MiB → {:.2} MiB", original_size_mb, zip_size_mb));
            } else {
                pb.finish_with_message(format!("Compression complete: {:.2} MiB", zip_size_mb));
            }
        }

        Ok(temp_file)
    }

    pub async fn upload_temp_file(&self, file_path: &Path) -> Result<String> {
        let url = format!("{}/api/v1/resources/temp_upload", self.client.base_url);
        let file_name = file_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("temp_upload.zip");

        let file_content = tokio::fs::read(file_path).await?;

        let part = reqwest::multipart::Part::bytes(file_content).file_name(file_name.to_string());

        let part = part
            .mime_str("application/octet-stream")
            .map_err(|e| Error::Network(format!("Failed to set mime type: {}", e)))?;

        let mut form = reqwest::multipart::Form::new().part("file", part);
        if let Some(upload_mode) = &self.upload_mode {
            form = form.text("upload_mode", upload_mode.clone());
        }

        let mut headers = self.client.build_headers();
        headers.remove(reqwest::header::CONTENT_TYPE);

        let upload_timeout = TimeoutConfig::for_upload().calculate(file_path)?;
        let long_timeout_client = self.client.create_client_with_connect_timeout(
            std::time::Duration::from_secs(30),
            upload_timeout,
        )?;

        let response = long_timeout_client
            .post(&url)
            .headers(headers)
            .multipart(form)
            .send()
            .await
            .map_err(|e| Error::Network(format!("File upload failed: {}", e)))?;

        let result: Value = self.client.handle_response(response).await?;
        result
            .get("temp_file_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .ok_or_else(|| Error::Parse("Missing temp_file_id in response".to_string()))
    }

    pub async fn upload_temp_file_with_progress(
        &self,
        file_path: &Path,
        verbose: bool,
    ) -> Result<String> {
        use indicatif::ProgressBar;

        let url = format!("{}/api/v1/resources/temp_upload", self.client.base_url);
        let file_name = file_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("temp_upload.zip");

        let file_content = tokio::fs::read(file_path).await?;
        let file_size = file_content.len() as u64;

        if verbose {
            eprintln!("Uploading: {} ({:.2} MB)", file_name, file_size as f64 / 1024.0 / 1024.0);
        }

        let pb = ProgressBar::new_spinner();
        pb.set_message(format!("Uploading {} ({:.2} MB)...", file_name, file_size as f64 / 1024.0 / 1024.0));
        pb.enable_steady_tick(std::time::Duration::from_millis(100));

        let part = reqwest::multipart::Part::bytes(file_content)
            .file_name(file_name.to_string());

        let part = part
            .mime_str("application/octet-stream")
            .map_err(|e| Error::Network(format!("Failed to set mime type: {}", e)))?;

        let mut form = reqwest::multipart::Form::new().part("file", part);
        if let Some(upload_mode) = &self.upload_mode {
            form = form.text("upload_mode", upload_mode.clone());
        }

        let mut headers = self.client.build_headers();
        headers.remove(reqwest::header::CONTENT_TYPE);

        let upload_timeout = TimeoutConfig::for_upload().calculate(file_path)?;
        let long_timeout_client = self.client.create_client_with_connect_timeout(
            std::time::Duration::from_secs(30),
            upload_timeout,
        )?;

        let response = long_timeout_client
            .post(&url)
            .headers(headers)
            .multipart(form)
            .send()
            .await
            .map_err(|e| Error::Network(format!("File upload failed: {}", e)))?;

        pb.finish_with_message("Upload complete");

        let result: Value = self.client.handle_response(response).await?;
        result
            .get("temp_file_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .ok_or_else(|| Error::Parse("Missing temp_file_id in response".to_string()))
    }
}
