//! Git module config types loaded from the [git] section of the binding TOML.

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct GitConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "default_backend")]
    pub backend: String,
    #[serde(default = "default_branch")]
    pub default_branch: String,
    #[serde(default = "default_author_name")]
    pub author_name: String,
    #[serde(default = "default_author_email")]
    pub author_email: String,

    #[serde(default)]
    pub local: Option<GitLocalConfig>,
    #[serde(default)]
    pub s3: Option<GitS3ConfigPy>,

    #[serde(default)]
    pub tuning: GitTuningConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct GitLocalConfig {
    pub base_dir: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct GitS3ConfigPy {
    pub bucket: String,
    #[serde(default = "default_s3_prefix")]
    pub prefix: String,
    pub region: String,
    #[serde(default)]
    pub endpoint: String,
    #[serde(default)]
    pub access_key: Option<String>,
    #[serde(default)]
    pub secret_key: Option<String>,
    #[serde(default = "default_cas_mode")]
    pub cas_mode: String,
    #[serde(default)]
    pub redis_lock_url: Option<String>,
    #[serde(default = "default_true")]
    pub use_path_style: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct GitTuningConfig {
    #[serde(default = "default_upload_concurrency")]
    pub upload_concurrency: usize,
    #[serde(default = "default_restore_concurrency")]
    pub restore_concurrency: usize,
    #[serde(default = "default_ref_cas_max_retry")]
    pub ref_cas_max_retry: u32,
    #[serde(default = "default_ref_cas_backoff_ms")]
    pub ref_cas_backoff_ms: u64,
    /// Enable Fast Path 1: skip read+SHA-1 for files whose `(size, mtime_ns)`
    /// match the previous commit's persisted index. Defaults to `true`; set
    /// to `false` to force the slow path on every commit (useful for tests
    /// and for environments with unreliable mtimes).
    #[serde(default = "default_true")]
    pub commit_index_enabled: bool,
    /// Enable Fast Path 3: on the commit slow path, run an `exists()` precheck
    /// before compressing and putting a blob, skipping the write when the
    /// object already exists. Defaults to `true`. `put` is idempotent, so this
    /// only affects backend call counts, never commit results.
    #[serde(default = "default_true")]
    pub blob_exists_precheck_enabled: bool,
}

impl Default for GitTuningConfig {
    fn default() -> Self {
        Self {
            upload_concurrency: default_upload_concurrency(),
            restore_concurrency: default_restore_concurrency(),
            ref_cas_max_retry: default_ref_cas_max_retry(),
            ref_cas_backoff_ms: default_ref_cas_backoff_ms(),
            commit_index_enabled: default_true(),
            blob_exists_precheck_enabled: default_true(),
        }
    }
}

fn default_backend() -> String {
    "local".to_string()
}
fn default_branch() -> String {
    "main".to_string()
}
fn default_author_name() -> String {
    "openviking-bot".to_string()
}
fn default_author_email() -> String {
    "bot@openviking.local".to_string()
}
fn default_s3_prefix() -> String {
    ".ovgit".to_string()
}
fn default_cas_mode() -> String {
    "native".to_string()
}
fn default_upload_concurrency() -> usize {
    64
}
fn default_restore_concurrency() -> usize {
    32
}
fn default_ref_cas_max_retry() -> u32 {
    3
}
fn default_ref_cas_backoff_ms() -> u64 {
    50
}
fn default_true() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_minimal_local_config() {
        let toml_src = r#"
            enabled = true
            backend = "local"

            [local]
            base_dir = "/tmp/ov-git"
        "#;
        let cfg: GitConfig = toml::from_str(toml_src).unwrap();
        assert!(cfg.enabled);
        assert_eq!(cfg.backend, "local");
        assert_eq!(cfg.default_branch, "main");
        assert_eq!(cfg.author_name, "openviking-bot");
        assert_eq!(cfg.author_email, "bot@openviking.local");
        assert_eq!(cfg.local.as_ref().unwrap().base_dir, "/tmp/ov-git");
        assert!(cfg.s3.is_none());
        assert_eq!(cfg.tuning.upload_concurrency, 64);
        assert_eq!(cfg.tuning.restore_concurrency, 32);
        assert_eq!(cfg.tuning.ref_cas_max_retry, 3);
        assert_eq!(cfg.tuning.ref_cas_backoff_ms, 50);
        assert!(cfg.tuning.commit_index_enabled);
        assert!(cfg.tuning.blob_exists_precheck_enabled);
    }

    #[test]
    fn parses_s3_config_with_overrides() {
        let toml_src = r#"
            enabled = true
            backend = "s3"
            default_branch = "trunk"
            author_name = "alice"
            author_email = "alice@example.com"

            [s3]
            bucket = "ov-bucket"
            region = "us-west-2"
            endpoint = "https://s3.example.com"
            access_key = "AKxxx"
            secret_key = "SKxxx"

            [tuning]
            upload_concurrency = 128
        "#;
        let cfg: GitConfig = toml::from_str(toml_src).unwrap();
        assert_eq!(cfg.backend, "s3");
        assert_eq!(cfg.default_branch, "trunk");
        let s3 = cfg.s3.as_ref().unwrap();
        assert_eq!(s3.bucket, "ov-bucket");
        assert_eq!(s3.prefix, ".ovgit");
        assert_eq!(s3.region, "us-west-2");
        assert_eq!(s3.cas_mode, "native");
        assert_eq!(cfg.tuning.upload_concurrency, 128);
        assert_eq!(cfg.tuning.restore_concurrency, 32);
    }

    #[test]
    fn defaults_when_section_minimal() {
        let cfg: GitConfig = toml::from_str("").unwrap();
        assert!(!cfg.enabled);
        assert_eq!(cfg.backend, "local");
    }
}
