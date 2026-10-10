//! Logical path mapping and account partition routing.

use std::collections::HashSet;
use std::sync::Arc;

use xxhash_rust::xxh3::xxh3_64;

use crate::core::errors::{Error, Result};
use crate::multibackend::constants::{
    CHECKPOINTS_DIR, MANIFEST_FILE, MULTIWRITE_MOUNT_PREFIX, MULTIWRITE_PROTOCOL_FILE,
    PARTITIONS_DIR, SEGMENTS_DIR, SYSTEM_DIR, VBUCKETS,
};
use crate::multibackend::meta::MetadataStore;
use crate::multibackend::model::{PartitionContext, PartitionsManifest, RouteEntry, ScopeKey};

/// Maps canonical logical metadata paths to raw backend paths.
#[derive(Debug, Clone)]
pub struct MultiWritePaths {
    mount_prefix: String,
}

impl MultiWritePaths {
    /// Create a path mapper for one canonical absolute mount prefix.
    pub fn new(mount_prefix: &str) -> Result<Self> {
        validate_canonical_path(mount_prefix)?;
        if mount_prefix != MULTIWRITE_MOUNT_PREFIX {
            return Err(Error::invalid_path(format!(
                "unsupported multi-write mount prefix: {mount_prefix}"
            )));
        }
        Ok(Self {
            mount_prefix: mount_prefix.to_string(),
        })
    }

    /// Return logical and backend paths for the mount protocol manifest.
    pub fn mount_protocol(&self) -> (String, String) {
        self.pair(&format!(
            "{}/{SYSTEM_DIR}/{MULTIWRITE_PROTOCOL_FILE}",
            self.mount_prefix
        ))
    }

    /// Return logical and backend paths for an account partitions directory.
    pub fn account_partitions(&self, account_id: &str) -> Result<(String, String)> {
        validate_account(account_id)?;
        Ok(self.pair(&format!(
            "{}/{account_id}/{SYSTEM_DIR}/{PARTITIONS_DIR}",
            self.mount_prefix
        )))
    }

    /// Return logical and backend paths for an account partitions manifest.
    pub fn account_manifest(&self, account_id: &str) -> Result<(String, String)> {
        let (logical, _) = self.account_partitions(account_id)?;
        Ok(self.pair(&format!("{logical}/{MANIFEST_FILE}")))
    }

    /// Return logical and backend paths for one partition manifest.
    pub fn partition_manifest(
        &self,
        account_id: &str,
        partition_id: u32,
    ) -> Result<(String, String)> {
        let (logical, _) = self.account_partitions(account_id)?;
        Ok(self.pair(&format!("{logical}/{partition_id}/{MANIFEST_FILE}")))
    }

    /// Return logical and backend paths for one partition segments directory.
    pub fn segments_dir(&self, account_id: &str, partition_id: u32) -> Result<(String, String)> {
        let (logical, _) = self.account_partitions(account_id)?;
        Ok(self.pair(&format!("{logical}/{partition_id}/{SEGMENTS_DIR}")))
    }

    /// Return logical and backend paths for one segment manifest.
    pub fn segment_manifest(
        &self,
        account_id: &str,
        partition_id: u32,
    ) -> Result<(String, String)> {
        let (logical, _) = self.segments_dir(account_id, partition_id)?;
        Ok(self.pair(&format!("{logical}/{MANIFEST_FILE}")))
    }

    /// Return logical and backend paths for one partition checkpoints directory.
    pub fn checkpoints_dir(&self, account_id: &str, partition_id: u32) -> Result<(String, String)> {
        let (logical, _) = self.account_partitions(account_id)?;
        Ok(self.pair(&format!("{logical}/{partition_id}/{CHECKPOINTS_DIR}")))
    }

    /// Return logical and backend paths for one checkpoints manifest.
    pub fn checkpoints_manifest(
        &self,
        account_id: &str,
        partition_id: u32,
    ) -> Result<(String, String)> {
        let (logical, _) = self.checkpoints_dir(account_id, partition_id)?;
        Ok(self.pair(&format!("{logical}/{MANIFEST_FILE}")))
    }

    /// Convert a canonical logical account path to its raw backend path.
    pub fn backend_path(&self, account_id: &str, logical_path: &str) -> Result<String> {
        validate_account(account_id)?;
        let account_root = format!("{}/{account_id}", self.mount_prefix);
        if logical_path != account_root
            && !logical_path
                .strip_prefix(&account_root)
                .is_some_and(|suffix| suffix.starts_with('/'))
        {
            return Err(Error::invalid_path(format!(
                "logical path does not belong to account '{account_id}': {logical_path}"
            )));
        }
        self.raw_backend_path(logical_path)
    }

    /// Convert any canonical logical mount path to its raw backend path.
    pub fn raw_backend_path(&self, logical_path: &str) -> Result<String> {
        validate_canonical_path(logical_path)?;
        let suffix = logical_path
            .strip_prefix(&self.mount_prefix)
            .filter(|suffix| suffix.starts_with('/'))
            .ok_or_else(|| {
                Error::invalid_path(format!(
                    "logical path is outside mount '{}': {logical_path}",
                    self.mount_prefix
                ))
            })?;
        Ok(suffix.to_string())
    }

    /// Hash a canonical full logical account path into its fixed vbucket.
    pub fn vbucket(&self, account_id: &str, logical_path: &str) -> Result<u32> {
        self.backend_path(account_id, logical_path)?;
        Ok((xxh3_64(logical_path.as_bytes()) & u64::from(VBUCKETS - 1)) as u32)
    }

    /// Pair one validated logical path with its raw backend form.
    fn pair(&self, logical_path: &str) -> (String, String) {
        (
            logical_path.to_string(),
            logical_path[self.mount_prefix.len()..].to_string(),
        )
    }
}

/// Routes account paths through the current persisted partitions manifest.
pub struct AccountRouter {
    store: Arc<MetadataStore>,
}

impl AccountRouter {
    /// Create an account router backed by the supplied metadata store.
    pub fn new(store: Arc<MetadataStore>) -> Self {
        Self { store }
    }

    /// Route one canonical logical account path to its partition context.
    pub async fn route(&self, account_id: &str, logical_path: &str) -> Result<PartitionContext> {
        let bucket = self.store.paths().vbucket(account_id, logical_path)?;
        let manifest_path = self.store.paths().account_manifest(account_id)?.0;
        let manifest: PartitionsManifest = self.store.read_json(&manifest_path).await?;
        manifest.validate()?;
        let index = manifest.routes.partition_point(|route| route.end < bucket);
        let route = manifest
            .routes
            .get(index)
            .ok_or_else(|| Error::Serialization("vbucket route is missing".to_string()))?;
        let segment_dir = self
            .store
            .paths()
            .segments_dir(account_id, route.partition)?
            .1;
        Ok(PartitionContext {
            scope: ScopeKey {
                account_id: account_id.to_string(),
                partition_id: route.partition,
                epoch: manifest.epoch,
            },
            segment_dir,
        })
    }
}

/// Validate compressed complete routes against the known partition identifiers.
pub fn validate_routes(
    routes: &[RouteEntry],
    partition_ids: impl IntoIterator<Item = u32>,
) -> Result<()> {
    let partition_ids = partition_ids.into_iter().collect::<HashSet<_>>();
    let mut next = 0;
    let mut previous_owner = None;
    for route in routes {
        route.validate()?;
        if route.start != next {
            return Err(Error::Serialization(
                "routes are not contiguous".to_string(),
            ));
        }
        if !partition_ids.contains(&route.partition) {
            return Err(Error::Serialization("route owner is unknown".to_string()));
        }
        if previous_owner == Some(route.partition) {
            return Err(Error::Serialization(
                "adjacent routes have the same owner".to_string(),
            ));
        }
        next = route.end + 1;
        previous_owner = Some(route.partition);
    }
    if next != VBUCKETS {
        return Err(Error::Serialization(
            "routes do not cover all vbuckets".to_string(),
        ));
    }
    Ok(())
}

/// Build deterministic balanced routes for the requested initial partition count.
pub fn build_initial_routes(partition_count: u32) -> Result<Vec<RouteEntry>> {
    if !(1..=VBUCKETS).contains(&partition_count) {
        return Err(Error::Serialization(format!(
            "partition count must be between 1 and {VBUCKETS}"
        )));
    }
    let owners = (0..VBUCKETS)
        .map(|bucket| { ((u64::from(bucket) * u64::from(partition_count)) / u64::from(VBUCKETS)) as u32})
        .collect::<Vec<_>>();
    let routes = compress_owners(&owners);
    validate_routes(&routes, 0..partition_count)?;
    Ok(routes)
}

/// Compress a complete owner table into inclusive route ranges.
pub(crate) fn compress_owners(owners: &[u32]) -> Vec<RouteEntry> {
    let mut routes = Vec::new();
    let mut start = 0;
    for index in 1..=owners.len() {
        if index == owners.len() || owners[index] != owners[start] {
            routes.push(RouteEntry {
                start: start as u32,
                end: index as u32 - 1,
                partition: owners[start],
            });
            start = index;
        }
    }
    routes
}

/// Validate an account identifier as one canonical path component.
fn validate_account(account_id: &str) -> Result<()> {
    if account_id.is_empty() || account_id == "." || account_id == ".." || account_id.contains('/')
    {
        return Err(Error::invalid_path(format!(
            "invalid account identifier: {account_id}"
        )));
    }
    Ok(())
}

/// Validate an absolute path without empty, dot, or trailing components.
fn validate_canonical_path(path: &str) -> Result<()> {
    if !path.starts_with('/')
        || path.len() <= 1
        || path.ends_with('/')
        || path.split('/').skip(1).any(|part| {
            part.is_empty() || part == "." || part == ".."
        })
    {
        return Err(Error::invalid_path(format!("non-canonical path: {path}")));
    }
    Ok(())
}
