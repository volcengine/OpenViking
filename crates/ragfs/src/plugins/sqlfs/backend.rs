//! Database backend abstraction for SQLFS
//!
//! This module provides an abstraction over different database backends
//! (SQLite, MySQL/TiDB) to allow SQLFS to work with multiple databases.

use crate::core::{ConfigValue, Error, Result};
use rusqlite::{params, Connection};
use std::collections::HashMap;
use std::sync::Mutex;

/// Maximum file size in bytes (5MB, same as Go version)
pub const MAX_FILE_SIZE: usize = 5 * 1024 * 1024;
/// Maximum file size in MB (for display)
pub const MAX_FILE_SIZE_MB: usize = 5;

/// Database backend trait
///
/// All database backends must implement this trait to provide
/// uniform access to different database systems.
pub trait DatabaseBackend: Send + Sync {
    /// Get the driver name for logging and metadata
    fn driver_name(&self) -> &'static str;

    /// Check if this path exists
    fn path_exists(&self, path: &str) -> Result<bool>;

    /// Check if a path is a directory
    fn is_directory(&self, path: &str) -> Result<bool>;

    /// Create a new file entry
    fn create_file(&self, path: &str, mode: u32, data: &[u8]) -> Result<()>;

    /// Create a new directory entry
    fn create_directory(&self, path: &str, mode: u32) -> Result<()>;

    /// Delete a file or directory entry
    fn delete_entry(&self, path: &str) -> Result<()>;

    /// Delete entries matching a pattern (for recursive delete)
    fn delete_entries_by_pattern(
        &self,
        pattern: &str,
        exclude_path: Option<&str>,
    ) -> Result<usize>;

    /// Read file data
    fn read_file(&self, path: &str) -> Result<Option<(bool, Vec<u8>)>>;

    /// Update file data
    fn update_file(&self, path: &str, data: &[u8]) -> Result<()>;

    /// Get file metadata
    fn get_metadata(&self, path: &str) -> Result<Option<FileMetadata>>;

    /// Update file mode
    fn update_mode(&self, path: &str, mode: u32) -> Result<()>;

    /// Rename a path (file or directory)
    fn rename_path(&self, old_path: &str, new_path: &str) -> Result<()>;

    /// Rename all children under a path (for directory rename)
    fn rename_children(&self, old_path: &str, new_path: &str) -> Result<()>;

    /// List directory contents (direct children only)
    fn list_directory(&self, path: &str) -> Result<Vec<FileMetadata>>;

    /// Count entries matching a pattern
    fn count_by_pattern(&self, pattern: &str) -> Result<i64>;

    /// Get parent path
    fn parent_path(&self, path: &str) -> String;
}

/// File metadata from database
#[derive(Debug, Clone)]
pub struct FileMetadata {
    /// Full path of the file or directory
    pub path: String,
    /// Whether this entry is a directory
    pub is_dir: bool,
    /// Unix-style file permissions
    pub mode: u32,
    /// File size in bytes
    pub size: i64,
    /// Last modification time as Unix timestamp
    pub mod_time: i64,
    /// File content data (None for metadata-only queries)
    pub data: Option<Vec<u8>>,
}

/// SQLite backend implementation
///
/// Uses `Mutex<Connection>` to satisfy `Send + Sync` requirements.
/// rusqlite's `Connection` is not `Sync` due to internal `RefCell` usage,
/// so we wrap it in a `Mutex` for thread-safe access.
pub struct SQLiteBackend {
    conn: Mutex<Connection>,
}

impl SQLiteBackend {
    /// Create a new SQLite backend
    ///
    /// Initializes the database schema and applies optimizations (WAL mode, etc.)
    pub fn new(db_path: Option<&str>) -> Result<Self> {
        let path = db_path.unwrap_or(":memory:");
        let conn = Connection::open(path)
            .map_err(|e| Error::internal(format!("sqlite connection error: {}", e)))?;

        // Initialize schema
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                is_dir INTEGER NOT NULL,
                mode INTEGER NOT NULL,
                size INTEGER NOT NULL,
                mod_time INTEGER NOT NULL,
                data BLOB
            );
            CREATE INDEX IF NOT EXISTS idx_parent ON files(path);
            "#,
        )
        .map_err(|e| Error::internal(format!("schema init error: {}", e)))?;

        // Apply optimizations
        conn.execute_batch(
            r#"
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA cache_size=-64000;
            "#,
        )
        .map_err(|e| Error::internal(format!("optimization error: {}", e)))?;

        // Ensure root directory exists
        let now = chrono::Utc::now().timestamp();
        conn.execute(
            "INSERT OR IGNORE INTO files (path, is_dir, mode, size, mod_time, data) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["/", 1, 0o755, 0i64, now, None::<Vec<u8>>],
        )
        .map_err(|e| Error::internal(format!("root init error: {}", e)))?;

        Ok(Self {
            conn: Mutex::new(conn),
        })
    }
}

impl DatabaseBackend for SQLiteBackend {
    fn driver_name(&self) -> &'static str {
        "sqlite3"
    }

    fn path_exists(&self, path: &str) -> Result<bool> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let mut stmt = conn
            .prepare_cached("SELECT COUNT(*) FROM files WHERE path = ?1")
            .map_err(|e| Error::internal(format!("prepare error: {}", e)))?;

        let count: i64 = match stmt.query_row(params![path], |row| row.get(0)) {
            Ok(count) => count,
            Err(rusqlite::Error::QueryReturnedNoRows) => 0,
            Err(e) => return Err(Error::internal(format!("query error: {}", e))),
        };

        Ok(count > 0)
    }

    fn is_directory(&self, path: &str) -> Result<bool> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let mut stmt = conn
            .prepare_cached("SELECT is_dir FROM files WHERE path = ?1")
            .map_err(|e| Error::internal(format!("prepare error: {}", e)))?;

        match stmt.query_row(params![path], |row| row.get::<_, i32>(0)) {
            Ok(is_dir) => Ok(is_dir == 1),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(false),
            Err(e) => Err(Error::internal(format!("query error: {}", e))),
        }
    }

    fn create_file(&self, path: &str, mode: u32, data: &[u8]) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let now = chrono::Utc::now().timestamp();
        conn.execute(
            "INSERT INTO files (path, is_dir, mode, size, mod_time, data) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![path, 0, mode, data.len() as i64, now, data],
        )
        .map_err(|e| Error::internal(format!("insert error: {}", e)))?;
        Ok(())
    }

    fn create_directory(&self, path: &str, mode: u32) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let now = chrono::Utc::now().timestamp();
        conn.execute(
            "INSERT INTO files (path, is_dir, mode, size, mod_time, data) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![path, 1, mode, 0i64, now, None::<Vec<u8>>],
        )
        .map_err(|e| Error::internal(format!("insert error: {}", e)))?;
        Ok(())
    }

    fn delete_entry(&self, path: &str) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        conn.execute("DELETE FROM files WHERE path = ?1", params![path])
            .map_err(|e| Error::internal(format!("delete error: {}", e)))?;
        Ok(())
    }

    fn delete_entries_by_pattern(
        &self,
        pattern: &str,
        exclude_path: Option<&str>,
    ) -> Result<usize> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;

        let result = if let Some(exclude) = exclude_path {
            conn.execute(
                "DELETE FROM files WHERE path LIKE ?1 AND path != ?2",
                params![pattern, exclude],
            )
            .map_err(|e| Error::internal(format!("delete error: {}", e)))?
        } else {
            conn.execute("DELETE FROM files WHERE path LIKE ?1", params![pattern])
                .map_err(|e| Error::internal(format!("delete error: {}", e)))?
        };

        Ok(result)
    }

    fn read_file(&self, path: &str) -> Result<Option<(bool, Vec<u8>)>> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let mut stmt = conn
            .prepare_cached("SELECT is_dir, data FROM files WHERE path = ?1")
            .map_err(|e| Error::internal(format!("prepare error: {}", e)))?;

        match stmt.query_row(params![path], |row| {
            let is_dir: i32 = row.get(0)?;
            let data: Option<Vec<u8>> = row.get(1)?;
            Ok((is_dir == 1, data.unwrap_or_default()))
        }) {
            Ok(result) => Ok(Some(result)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(Error::internal(format!("query error: {}", e))),
        }
    }

    fn update_file(&self, path: &str, data: &[u8]) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let now = chrono::Utc::now().timestamp();
        conn.execute(
            "UPDATE files SET data = ?1, size = ?2, mod_time = ?3 WHERE path = ?4",
            params![data, data.len() as i64, now, path],
        )
        .map_err(|e| Error::internal(format!("update error: {}", e)))?;
        Ok(())
    }

    fn get_metadata(&self, path: &str) -> Result<Option<FileMetadata>> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let mut stmt = conn
            .prepare_cached(
                "SELECT path, is_dir, mode, size, mod_time FROM files WHERE path = ?1",
            )
            .map_err(|e| Error::internal(format!("prepare error: {}", e)))?;

        match stmt.query_row(params![path], |row| {
            Ok(FileMetadata {
                path: row.get(0)?,
                is_dir: row.get::<_, i32>(1)? == 1,
                mode: row.get(2)?,
                size: row.get(3)?,
                mod_time: row.get(4)?,
                data: None,
            })
        }) {
            Ok(meta) => Ok(Some(meta)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(Error::internal(format!("query error: {}", e))),
        }
    }

    fn update_mode(&self, path: &str, mode: u32) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let now = chrono::Utc::now().timestamp();
        conn.execute(
            "UPDATE files SET mode = ?1, mod_time = ?2 WHERE path = ?3",
            params![mode, now, path],
        )
        .map_err(|e| Error::internal(format!("update error: {}", e)))?;
        Ok(())
    }

    fn rename_path(&self, old_path: &str, new_path: &str) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        conn.execute(
            "UPDATE files SET path = ?1 WHERE path = ?2",
            params![new_path, old_path],
        )
        .map_err(|e| Error::internal(format!("rename error: {}", e)))?;
        Ok(())
    }

    fn rename_children(&self, old_path: &str, new_path: &str) -> Result<()> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let old_pattern = format!("{}/%", old_path);
        let old_len = (old_path.len() + 1) as i32;
        let sql = "UPDATE files SET path = ?1 || SUBSTR(path, ?2) WHERE path LIKE ?3";
        conn.execute(sql, params![new_path, old_len, old_pattern])
            .map_err(|e| Error::internal(format!("rename children error: {}", e)))?;
        Ok(())
    }

    fn list_directory(&self, path: &str) -> Result<Vec<FileMetadata>> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;

        // Build pattern for direct children only
        // For root "/": children are like "/<name>" (no further slashes)
        // For "/dir": children are like "/dir/<name>" (no further slashes)
        let prefix = if path == "/" {
            "/".to_string()
        } else {
            format!("{}/", path)
        };

        // Query all entries that start with the prefix,
        // excluding the directory itself
        let sql = "SELECT path, is_dir, mode, size, mod_time FROM files WHERE path LIKE ?1 AND path != ?2 ORDER BY path";
        let like_pattern = format!("{}%", prefix);

        let mut stmt = conn
            .prepare_cached(sql)
            .map_err(|e| Error::internal(format!("prepare error: {}", e)))?;

        let mut results = Vec::new();
        let prefix_len = prefix.len();

        let rows = stmt
            .query_map(params![like_pattern, path], |row| {
                Ok(FileMetadata {
                    path: row.get(0)?,
                    is_dir: row.get::<_, i32>(1)? == 1,
                    mode: row.get(2)?,
                    size: row.get(3)?,
                    mod_time: row.get(4)?,
                    data: None,
                })
            })
            .map_err(|e| Error::internal(format!("query error: {}", e)))?;

        for row_result in rows {
            let meta =
                row_result.map_err(|e| Error::internal(format!("row error: {}", e)))?;

            // Only include direct children (no further '/' after the prefix)
            let remainder = &meta.path[prefix_len..];
            if !remainder.contains('/') {
                results.push(meta);
            }
        }

        Ok(results)
    }

    fn count_by_pattern(&self, pattern: &str) -> Result<i64> {
        let conn = self.conn.lock().map_err(|e| Error::internal(e.to_string()))?;
        let mut stmt = conn
            .prepare_cached("SELECT COUNT(*) FROM files WHERE path LIKE ?1")
            .map_err(|e| Error::internal(format!("prepare error: {}", e)))?;

        let count: i64 = stmt
            .query_row(params![pattern], |row| row.get(0))
            .map_err(|e| Error::internal(format!("query error: {}", e)))?;

        Ok(count)
    }

    fn parent_path(&self, path: &str) -> String {
        if path == "/" {
            return "/".to_string();
        }

        // Remove trailing slash
        let trimmed = path.trim_end_matches('/');
        if trimmed.is_empty() {
            return "/".to_string();
        }

        // Find last slash
        if let Some(pos) = trimmed.rfind('/') {
            if pos == 0 {
                return "/".to_string();
            }
            return trimmed[..pos].to_string();
        }

        "/".to_string()
    }
}

/// Create a database backend from configuration
pub fn create_backend(config: &HashMap<String, ConfigValue>) -> Result<Box<dyn DatabaseBackend>> {
    let backend_type = config
        .get("backend")
        .and_then(|v| v.as_string())
        .unwrap_or("sqlite");

    match backend_type {
        "sqlite" | "sqlite3" => {
            let db_path = config.get("db_path").and_then(|v| v.as_string());
            let backend = SQLiteBackend::new(db_path)?;
            Ok(Box::new(backend))
        }
        "mysql" | "tidb" => {
            // TODO: Implement MySQL/TiDB backend
            Err(Error::internal("MySQL/TiDB backend not yet implemented"))
        }
        _ => Err(Error::config(format!(
            "unsupported database backend: {} (valid options: sqlite, sqlite3)",
            backend_type
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parent_path() {
        let backend = SQLiteBackend::new(Some(":memory:")).unwrap();
        assert_eq!(backend.parent_path("/"), "/");
        assert_eq!(backend.parent_path("/file.txt"), "/");
        assert_eq!(backend.parent_path("/dir/"), "/");
        assert_eq!(backend.parent_path("/dir/file.txt"), "/dir");
        assert_eq!(backend.parent_path("/a/b/c/file.txt"), "/a/b/c");
    }

    #[test]
    fn test_sqlite_backend_basic() {
        let backend = SQLiteBackend::new(Some(":memory:")).unwrap();

        // Root should already exist
        assert!(backend.path_exists("/").unwrap());
        assert!(backend.is_directory("/").unwrap());

        // Create a directory
        backend.create_directory("/testdir", 0o755).unwrap();
        assert!(backend.path_exists("/testdir").unwrap());
        assert!(backend.is_directory("/testdir").unwrap());

        // Create a file
        backend.create_file("/testdir/file.txt", 0o644, b"hello").unwrap();
        assert!(backend.path_exists("/testdir/file.txt").unwrap());
        assert!(!backend.is_directory("/testdir/file.txt").unwrap());

        // Read file
        let result = backend.read_file("/testdir/file.txt").unwrap();
        assert!(result.is_some());
        let (is_dir, data) = result.unwrap();
        assert!(!is_dir);
        assert_eq!(data, b"hello");

        // List directory - should return only direct children
        let entries = backend.list_directory("/testdir").unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].path, "/testdir/file.txt");
    }

    #[test]
    fn test_list_directory_direct_children() {
        let backend = SQLiteBackend::new(Some(":memory:")).unwrap();

        // Create nested structure: /a/b/c
        backend.create_directory("/a", 0o755).unwrap();
        backend.create_directory("/a/b", 0o755).unwrap();
        backend.create_directory("/a/b/c", 0o755).unwrap();
        backend.create_file("/a/file1.txt", 0o644, b"").unwrap();
        backend.create_file("/a/b/file2.txt", 0o644, b"").unwrap();

        // List /a - should only return /a/b and /a/file1.txt
        let entries = backend.list_directory("/a").unwrap();
        assert_eq!(entries.len(), 2);
        let paths: Vec<&str> = entries.iter().map(|e| e.path.as_str()).collect();
        assert!(paths.contains(&"/a/b"));
        assert!(paths.contains(&"/a/file1.txt"));

        // List / - should only return /a
        let entries = backend.list_directory("/").unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].path, "/a");
    }
}
