//! HTTP handlers for RAGFS API
//!
//! This module implements all HTTP request handlers for the RAGFS REST API.

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::core::{FileSystem, MountableFS, PluginConfig, WriteFlag};

/// Shared application state
#[derive(Clone)]
pub struct AppState {
    /// The mounted filesystem
    pub fs: Arc<MountableFS>,
}

/// Standard API response
#[derive(Debug, Serialize)]
pub struct ApiResponse<T> {
    /// Whether the operation succeeded
    pub success: bool,
    /// Response data (if successful)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<T>,
    /// Error message (if failed)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl<T> ApiResponse<T> {
    /// Create a successful response
    pub fn success(data: T) -> Self {
        Self {
            success: true,
            data: Some(data),
            error: None,
        }
    }

    /// Create an error response
    pub fn error(message: impl Into<String>) -> ApiResponse<()> {
        ApiResponse {
            success: false,
            data: None,
            error: Some(message.into()),
        }
    }
}

/// Query parameters for file operations
#[derive(Debug, Deserialize)]
pub struct FileQuery {
    /// File path
    pub path: String,
    /// Read offset in bytes
    #[serde(default)]
    pub offset: u64,
    /// Number of bytes to read (0 = all)
    #[serde(default)]
    pub size: u64,
}

/// Query parameters for directory operations
#[derive(Debug, Deserialize)]
pub struct DirQuery {
    /// Directory path
    pub path: String,
}

/// Request body for mount operation
#[derive(Debug, Deserialize)]
pub struct MountRequest {
    /// Plugin name
    pub plugin: String,
    /// Mount path
    pub path: String,
    /// Plugin configuration parameters
    #[serde(default)]
    pub params: std::collections::HashMap<String, serde_json::Value>,
}

/// Request body for unmount operation
#[derive(Debug, Deserialize)]
pub struct UnmountRequest {
    /// Mount path to unmount
    pub path: String,
}

/// Health check response
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    /// Health status
    pub status: String,
    /// Server version
    pub version: String,
}

/// Mount info response
#[derive(Debug, Serialize)]
pub struct MountInfo {
    /// Mount path
    pub path: String,
    /// Plugin name
    pub plugin: String,
}

// ============================================================================
// File Operations Handlers
// ============================================================================

/// GET /api/v1/files - Read file
pub async fn read_file(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
) -> Response {
    match state.fs.read(&query.path, query.offset, query.size).await {
        Ok(data) => (StatusCode::OK, data).into_response(),
        Err(e) => (
            StatusCode::NOT_FOUND,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

/// PUT /api/v1/files - Write file
pub async fn write_file(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
    body: bytes::Bytes,
) -> Response {
    match state
        .fs
        .write(&query.path, &body, query.offset, WriteFlag::None)
        .await
    {
        Ok(written) => (
            StatusCode::OK,
            Json(ApiResponse::success(serde_json::json!({
                "bytes_written": written
            }))),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

/// POST /api/v1/files - Create file
pub async fn create_file(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
) -> Response {
    match state.fs.create(&query.path).await {
        Ok(_) => (
            StatusCode::CREATED,
            Json(ApiResponse::success(serde_json::json!({
                "path": query.path
            }))),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

/// DELETE /api/v1/files - Delete file
pub async fn delete_file(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
) -> Response {
    match state.fs.remove(&query.path).await {
        Ok(_) => (
            StatusCode::OK,
            Json(ApiResponse::success(serde_json::json!({
                "path": query.path
            }))),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

/// GET /api/v1/stat - Get file metadata
pub async fn stat_file(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
) -> Response {
    match state.fs.stat(&query.path).await {
        Ok(info) => (StatusCode::OK, Json(ApiResponse::success(info))).into_response(),
        Err(e) => (
            StatusCode::NOT_FOUND,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

// ============================================================================
// Directory Operations Handlers
// ============================================================================

/// GET /api/v1/directories - List directory
pub async fn list_directory(
    State(state): State<AppState>,
    Query(query): Query<DirQuery>,
) -> Response {
    match state.fs.read_dir(&query.path).await {
        Ok(entries) => (StatusCode::OK, Json(ApiResponse::success(entries))).into_response(),
        Err(e) => (
            StatusCode::NOT_FOUND,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

/// POST /api/v1/directories - Create directory
pub async fn create_directory(
    State(state): State<AppState>,
    Query(query): Query<DirQuery>,
) -> Response {
    match state.fs.mkdir(&query.path, 0o755).await {
        Ok(_) => (
            StatusCode::CREATED,
            Json(ApiResponse::success(serde_json::json!({
                "path": query.path
            }))),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

// ============================================================================
// Mount Management Handlers
// ============================================================================

/// GET /api/v1/mounts - List all mounts
pub async fn list_mounts(State(state): State<AppState>) -> Response {
    let mounts = state.fs.list_mounts().await;
    let mount_infos: Vec<MountInfo> = mounts
        .into_iter()
        .map(|(path, plugin)| MountInfo { path, plugin })
        .collect();

    (StatusCode::OK, Json(ApiResponse::success(mount_infos))).into_response()
}

/// POST /api/v1/mount - Mount a filesystem
pub async fn mount_filesystem(
    State(state): State<AppState>,
    Json(req): Json<MountRequest>,
) -> Response {
    // Convert JSON params to ConfigValue
    let params = req
        .params
        .into_iter()
        .map(|(k, v)| {
            let config_value = match v {
                serde_json::Value::String(s) => crate::core::ConfigValue::String(s),
                serde_json::Value::Number(n) => {
                    if let Some(i) = n.as_i64() {
                        crate::core::ConfigValue::Int(i)
                    } else {
                        crate::core::ConfigValue::String(n.to_string())
                    }
                }
                serde_json::Value::Bool(b) => crate::core::ConfigValue::Bool(b),
                serde_json::Value::Array(arr) => {
                    let strings: Vec<String> = arr
                        .into_iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect();
                    crate::core::ConfigValue::StringList(strings)
                }
                _ => crate::core::ConfigValue::String(v.to_string()),
            };
            (k, config_value)
        })
        .collect();

    let config = PluginConfig {
        name: req.plugin.clone(),
        mount_path: req.path.clone(),
        params,
    };

    match state.fs.mount(config).await {
        Ok(_) => (
            StatusCode::OK,
            Json(ApiResponse::success(serde_json::json!({
                "plugin": req.plugin,
                "path": req.path
            }))),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

/// POST /api/v1/unmount - Unmount a filesystem
pub async fn unmount_filesystem(
    State(state): State<AppState>,
    Json(req): Json<UnmountRequest>,
) -> Response {
    match state.fs.unmount(&req.path).await {
        Ok(_) => (
            StatusCode::OK,
            Json(ApiResponse::success(serde_json::json!({
                "path": req.path
            }))),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse::<()>::error(e.to_string())),
        )
            .into_response(),
    }
}

// ============================================================================
// Health Check Handler
// ============================================================================

/// GET /api/v1/health - Health check
pub async fn health_check() -> Response {
    let response = HealthResponse {
        status: "healthy".to_string(),
        version: crate::VERSION.to_string(),
    };

    (StatusCode::OK, Json(ApiResponse::success(response))).into_response()
}
