//! Router configuration for RAGFS HTTP server
//!
//! This module sets up all the routes and middleware for the API.

use axum::{
    routing::{delete, get, post, put},
    Router,
};
use tower_http::{
    cors::CorsLayer,
    trace::{DefaultMakeSpan, DefaultOnResponse, TraceLayer},
};
use tracing::Level;

use super::handlers::{
    create_directory, create_file, delete_file, health_check, list_directory, list_mounts,
    mount_filesystem, read_file, stat_file, unmount_filesystem, write_file, AppState,
};

/// Create the main application router
pub fn create_router(state: AppState, enable_cors: bool) -> Router {
    let api_routes = Router::new()
        // File operations
        .route("/files", get(read_file))
        .route("/files", put(write_file))
        .route("/files", post(create_file))
        .route("/files", delete(delete_file))
        .route("/stat", get(stat_file))
        // Directory operations
        .route("/directories", get(list_directory))
        .route("/directories", post(create_directory))
        // Mount management
        .route("/mounts", get(list_mounts))
        .route("/mount", post(mount_filesystem))
        .route("/unmount", post(unmount_filesystem))
        // Health check
        .route("/health", get(health_check));

    let app = Router::new()
        .nest("/api/v1", api_routes)
        .with_state(state);

    // Add tracing middleware
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(DefaultMakeSpan::new().level(Level::INFO))
            .on_response(DefaultOnResponse::new().level(Level::INFO)),
    );

    // Add CORS if enabled
    if enable_cors {
        app.layer(CorsLayer::permissive())
    } else {
        app
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::MountableFS;
    use std::sync::Arc;

    #[test]
    fn test_router_creation() {
        let state = AppState {
            fs: Arc::new(MountableFS::new()),
        };

        let _router = create_router(state, true);
        // If this compiles and runs, the router is correctly configured
    }
}
