//! RAGFS Server
//!
//! HTTP server that exposes the RAGFS filesystem through a REST API.

use clap::Parser;
use ragfs::core::MountableFS;
use ragfs::plugins::{KVFSPlugin, MemFSPlugin, QueueFSPlugin, SQLFSPlugin};
#[cfg(feature = "s3")]
use ragfs::plugins::S3FSPlugin;
use ragfs::server::{create_router, AppState, Args};
use std::sync::Arc;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Parse command-line arguments
    let args = Args::parse();

    // Load configuration
    let config = args.load_config()?;

    // Initialize tracing/logging
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| config.log_level.clone().into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    tracing::info!("Starting RAGFS Server v{}", ragfs::VERSION);
    tracing::info!("Configuration: {:?}", config);

    // Create MountableFS
    let fs = Arc::new(MountableFS::new());

    // Register built-in plugins
    tracing::info!("Registering plugins...");
    fs.register_plugin(MemFSPlugin).await;
    tracing::info!("  - memfs: In-memory file system");
    fs.register_plugin(KVFSPlugin).await;
    tracing::info!("  - kvfs: Key-value file system");
    fs.register_plugin(QueueFSPlugin).await;
    tracing::info!("  - queuefs: Message queue file system");
    fs.register_plugin(SQLFSPlugin::new()).await;
    tracing::info!("  - sqlfs: Database-backed file system (SQLite)");
    #[cfg(feature = "s3")]
    {
        fs.register_plugin(S3FSPlugin::new()).await;
        tracing::info!("  - s3fs: S3-backed file system");
    }

    // Create application state
    let state = AppState { fs: fs.clone() };

    // Create router
    let app = create_router(state, config.enable_cors);

    // Parse socket address
    let addr = config.socket_addr()?;

    tracing::info!("Server listening on {}", addr);
    tracing::info!("API endpoints:");
    tracing::info!("  GET    /api/v1/health");
    tracing::info!("  GET    /api/v1/files?path=<path>");
    tracing::info!("  PUT    /api/v1/files?path=<path>");
    tracing::info!("  POST   /api/v1/files?path=<path>");
    tracing::info!("  DELETE /api/v1/files?path=<path>");
    tracing::info!("  GET    /api/v1/stat?path=<path>");
    tracing::info!("  GET    /api/v1/directories?path=<path>");
    tracing::info!("  POST   /api/v1/directories?path=<path>");
    tracing::info!("  GET    /api/v1/mounts");
    tracing::info!("  POST   /api/v1/mount");
    tracing::info!("  POST   /api/v1/unmount");
    tracing::info!("");
    tracing::info!("Example: Mount MemFS");
    tracing::info!("  curl -X POST http://{}//api/v1/mount \\", addr);
    tracing::info!("    -H 'Content-Type: application/json' \\");
    tracing::info!("    -d '{{\"plugin\": \"memfs\", \"path\": \"/memfs\"}}'");

    // Create TCP listener
    let listener = tokio::net::TcpListener::bind(addr).await?;

    // Start server
    axum::serve(listener, app).await?;

    Ok(())
}
