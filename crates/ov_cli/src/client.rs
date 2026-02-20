use reqwest::{Client as ReqwestClient, StatusCode};
use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::error::{Error, Result};

/// High-level HTTP client for OpenViking API
#[derive(Clone)]
pub struct HttpClient {
    http: ReqwestClient,
    base_url: String,
    api_key: Option<String>,
}

impl HttpClient {
    /// Create a new HTTP client
    pub fn new(base_url: impl Into<String>, api_key: Option<String>) -> Self {
        let http = ReqwestClient::builder()
            .timeout(std::time::Duration::from_secs(60))
            .build()
            .expect("Failed to build HTTP client");

        Self {
            http,
            base_url: base_url.into().trim_end_matches('/').to_string(),
            api_key,
        }
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

    async fn handle_response<T: DeserializeOwned>(
        &self,
        response: reqwest::Response,
    ) -> Result<T> {
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
                .or_else(|| json.get("detail").and_then(|d| d.as_str()).map(|s| s.to_string()))
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

    // ============ Filesystem Methods ============

    pub async fn ls(&self, uri: &str, simple: bool, recursive: bool, output: &str, abs_limit: i32, show_all_hidden: bool, node_limit: i32) -> Result<serde_json::Value> {
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

    pub async fn tree(&self, uri: &str, output: &str, abs_limit: i32, show_all_hidden: bool, node_limit: i32) -> Result<serde_json::Value> {
        let params = vec![
            ("uri".to_string(), uri.to_string()),
            ("output".to_string(), output.to_string()),
            ("abs_limit".to_string(), abs_limit.to_string()),
            ("show_all_hidden".to_string(), show_all_hidden.to_string()),
            ("node_limit".to_string(), node_limit.to_string()),
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
        limit: i32,
        threshold: Option<f64>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "target_uri": uri,
            "limit": limit,
            "score_threshold": threshold,
        });
        self.post("/api/v1/search/find", &body).await
    }

    pub async fn search(
        &self,
        query: String,
        uri: String,
        session_id: Option<String>,
        limit: i32,
        threshold: Option<f64>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "query": query,
            "target_uri": uri,
            "session_id": session_id,
            "limit": limit,
            "score_threshold": threshold,
        });
        self.post("/api/v1/search/search", &body).await
    }

    pub async fn grep(&self, uri: &str, pattern: &str, ignore_case: bool) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
            "pattern": pattern,
            "case_insensitive": ignore_case,
        });
        self.post("/api/v1/search/grep", &body).await
    }

    pub async fn glob(&self, pattern: &str, uri: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "pattern": pattern,
            "uri": uri,
        });
        self.post("/api/v1/search/glob", &body).await
    }

    // ============ Resource Methods ============

    pub async fn add_resource(
        &self,
        path: &str,
        target: Option<String>,
        reason: &str,
        instruction: &str,
        wait: bool,
        timeout: Option<f64>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "path": path,
            "target": target,
            "reason": reason,
            "instruction": instruction,
            "wait": wait,
            "timeout": timeout,
        });
        self.post("/api/v1/resources", &body).await
    }

    pub async fn add_skill(
        &self,
        data: &str,
        wait: bool,
        timeout: Option<f64>,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "data": data,
            "wait": wait,
            "timeout": timeout,
        });
        self.post("/api/v1/skills", &body).await
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

    pub async fn export_ovpack(&self, uri: &str, to: &str) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "uri": uri,
            "to": to,
        });
        self.post("/api/v1/pack/export", &body).await
    }

    pub async fn import_ovpack(
        &self,
        file_path: &str,
        parent: &str,
        force: bool,
        vectorize: bool,
    ) -> Result<serde_json::Value> {
        let body = serde_json::json!({
            "file_path": file_path,
            "parent": parent,
            "force": force,
            "vectorize": vectorize,
        });
        self.post("/api/v1/pack/import", &body).await
    }
}
