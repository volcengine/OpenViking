//! Immutable, content-addressed history metadata. The single ref CAS publishes
//! all graft and tombstone changes together.
use super::{util, GitError, ObjectStore, RefStore, RefStoreError};
use gix_hash::ObjectId;
use gix_object::Kind;
use lru::LruCache;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    num::NonZeroUsize,
    sync::{Arc, Mutex},
};

pub const OVERLAY_REF: &str = "refs/ov/history";

#[derive(Default, Serialize, Deserialize, Clone)]
struct Shard {
    grafts: BTreeMap<String, Vec<String>>,
    deleted: BTreeMap<String, i64>,
}

#[derive(Serialize, Deserialize, Clone)]
struct Root {
    version: u32,
    // A purge-only edit inherits the last logical edit's ID. This is an opaque SQL
    // generation token, not a reference to a blob that must be retained by GC.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    path_history_root: Option<String>,
    shards: BTreeMap<String, String>,
}

#[derive(Clone)]
enum Cached {
    Root(Arc<Root>),
    Shard(Arc<Shard>),
}

pub struct OverlayCache(Mutex<LruCache<ObjectId, Cached>>);
impl OverlayCache {
    pub fn new(capacity: usize) -> Self {
        Self(Mutex::new(LruCache::new(
            NonZeroUsize::new(capacity.max(1)).unwrap(),
        )))
    }
    fn get(&self, oid: &ObjectId) -> Option<Cached> {
        self.0.lock().unwrap().get(oid).cloned()
    }
    fn put(&self, oid: ObjectId, entry: Cached) {
        self.0.lock().unwrap().put(oid, entry);
    }
}

#[derive(Clone, Default)]
pub struct OverlayView {
    pub root_oid: Option<ObjectId>,
    /// Generation of path changes visible on retained commits. Tombstone purges preserve it.
    pub(crate) path_history_root: Option<ObjectId>,
    shards: BTreeMap<String, ObjectId>,
    source: Option<(Arc<dyn ObjectStore>, String, Arc<OverlayCache>)>,
}

fn parse_id(s: &str) -> Result<ObjectId, GitError> {
    ObjectId::from_hex(s.as_bytes())
        .map_err(|e| GitError::CorruptedObject(format!("history oid: {e}")))
}
fn shard_key(oid: &ObjectId) -> String {
    format!("{:02x}", oid.as_bytes()[0])
}

async fn read_json<T: DeserializeOwned>(
    objects: &dyn ObjectStore,
    account: &str,
    oid: &ObjectId,
) -> Result<T, GitError> {
    let raw = util::read_object(objects, account, oid).await?;
    let (kind, size, offset) = util::parse_object_header(&raw)?;
    if kind != Kind::Blob {
        return Err(GitError::CorruptedObject(
            "history metadata is not a blob".into(),
        ));
    }
    let data = &raw[offset..];
    if size != data.len() as u64
        || gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data) != *oid
    {
        return Err(GitError::CorruptedObject(format!(
            "corrupt history metadata {oid}"
        )));
    }
    serde_json::from_slice(data)
        .map_err(|e| GitError::CorruptedObject(format!("history metadata: {e}")))
}

async fn write_json(
    objects: &dyn ObjectStore,
    account: &str,
    item: &impl Serialize,
) -> Result<ObjectId, GitError> {
    let bytes = serde_json::to_vec(item).map_err(|e| GitError::Other(e.to_string()))?;
    Ok(util::write_object(objects, account, Kind::Blob, &bytes).await?)
}

pub async fn load_overlay(
    objects: Arc<dyn ObjectStore>,
    refs: &dyn RefStore,
    account: &str,
    cache: &Arc<OverlayCache>,
) -> Result<OverlayView, GitError> {
    let oid = match refs.read(account, OVERLAY_REF).await {
        Ok(oid) => oid,
        Err(RefStoreError::NotFound(_)) => return Ok(OverlayView::empty()),
        Err(e) => return Err(e.into()),
    };
    let root = match cache.get(&oid) {
        Some(Cached::Root(v)) => v,
        _ => {
            let root: Root = read_json(objects.as_ref(), account, &oid).await?;
            if root.version != 1 {
                return Err(GitError::CorruptedObject(format!(
                    "unsupported history version {}",
                    root.version
                )));
            }
            let root = Arc::new(root);
            cache.put(oid, Cached::Root(root.clone()));
            root
        }
    };
    let shards = root
        .shards
        .iter()
        .map(|(k, v)| Ok((k.clone(), parse_id(v)?)))
        .collect::<Result<_, GitError>>()?;
    Ok(OverlayView {
        root_oid: Some(oid),
        path_history_root: Some(
            root.path_history_root
                .as_deref()
                .map(parse_id)
                .transpose()?
                .unwrap_or(oid),
        ),
        shards,
        source: Some((objects, account.into(), cache.clone())),
    })
}

impl OverlayView {
    pub fn empty() -> Self {
        Self::default()
    }
    async fn shard(&self, key: &str) -> Result<Option<Arc<Shard>>, GitError> {
        let Some(oid) = self.shards.get(key) else {
            return Ok(None);
        };
        let (objects, account, cache) = self.source.as_ref().expect("nonempty view has a store");
        if let Some(Cached::Shard(shard)) = cache.get(oid) {
            return Ok(Some(shard));
        }
        let shard = Arc::new(read_json(objects.as_ref(), account, oid).await?);
        cache.put(*oid, Cached::Shard(Arc::clone(&shard)));
        Ok(Some(shard))
    }
    pub async fn deleted_at(&self, oid: &ObjectId) -> Result<Option<i64>, GitError> {
        Ok(self
            .shard(&shard_key(oid))
            .await?
            .and_then(|s| s.deleted.get(&oid.to_string()).copied()))
    }
    pub async fn graft(&self, oid: &ObjectId) -> Result<Option<Vec<ObjectId>>, GitError> {
        let Some(shard) = self.shard(&shard_key(oid)).await? else {
            return Ok(None);
        };
        shard
            .grafts
            .get(&oid.to_string())
            .map(|ps| ps.iter().map(|p| parse_id(p)).collect())
            .transpose()
    }
    pub async fn all_tombstones(&self) -> Result<Vec<(ObjectId, i64)>, GitError> {
        let mut out = Vec::new();
        for key in self.shards.keys() {
            for (id, ts) in &self.shard(key).await?.unwrap().deleted {
                out.push((parse_id(id)?, *ts));
            }
        }
        Ok(out)
    }
    pub async fn all_grafts(&self) -> Result<Vec<(ObjectId, Vec<ObjectId>)>, GitError> {
        let mut out = Vec::new();
        for key in self.shards.keys() {
            for (id, ps) in &self.shard(key).await?.unwrap().grafts {
                out.push((
                    parse_id(id)?,
                    ps.iter().map(|p| parse_id(p)).collect::<Result<_, _>>()?,
                ));
            }
        }
        Ok(out)
    }
    pub fn blob_oids(&self) -> Vec<ObjectId> {
        self.root_oid
            .into_iter()
            .chain(self.shards.values().copied())
            .collect()
    }
}

#[derive(Default)]
pub struct OverlayEdit {
    pub set_grafts: Vec<(ObjectId, Vec<ObjectId>)>,
    pub remove_grafts: Vec<ObjectId>,
    pub add_tombstones: Vec<(ObjectId, i64)>,
    pub remove_tombstones: Vec<ObjectId>,
}

pub async fn commit_overlay_edit(
    objects: &dyn ObjectStore,
    refs: &dyn RefStore,
    account: &str,
    base: &OverlayView,
    edit: &OverlayEdit,
) -> Result<Option<ObjectId>, GitError> {
    let mut modified = BTreeMap::new();
    for id in edit
        .set_grafts
        .iter()
        .map(|(id, _)| id)
        .chain(&edit.remove_grafts)
        .chain(edit.add_tombstones.iter().map(|(id, _)| id))
        .chain(&edit.remove_tombstones)
    {
        let key = shard_key(id);
        if !modified.contains_key(&key) {
            let shard = base
                .shard(&key)
                .await?
                .as_deref()
                .cloned()
                .unwrap_or_default();
            modified.insert(key, shard);
        }
    }
    for id in &edit.remove_grafts {
        modified
            .get_mut(&shard_key(id))
            .unwrap()
            .grafts
            .remove(&id.to_string());
    }
    for id in &edit.remove_tombstones {
        modified
            .get_mut(&shard_key(id))
            .unwrap()
            .deleted
            .remove(&id.to_string());
    }
    for (id, ps) in &edit.set_grafts {
        modified
            .get_mut(&shard_key(id))
            .unwrap()
            .grafts
            .insert(id.to_string(), ps.iter().map(ToString::to_string).collect());
    }
    for (id, ts) in &edit.add_tombstones {
        modified
            .get_mut(&shard_key(id))
            .unwrap()
            .deleted
            .insert(id.to_string(), *ts);
    }
    let mut shards = base.shards.clone();
    for (key, shard) in modified {
        if shard.deleted.is_empty() && shard.grafts.is_empty() {
            shards.remove(&key);
        } else {
            shards.insert(key, write_json(objects, account, &shard).await?);
        }
    }
    let root = Root {
        version: 1,
        path_history_root: if edit.set_grafts.is_empty()
            && edit.remove_grafts.is_empty()
            && edit.add_tombstones.is_empty()
        {
            base.path_history_root.map(|id| id.to_string())
        } else {
            None
        },
        shards: shards
            .into_iter()
            .map(|(k, id)| (k, id.to_string()))
            .collect(),
    };
    let oid = write_json(objects, account, &root).await?;
    match refs
        .cas_update(account, OVERLAY_REF, base.root_oid, oid)
        .await
    {
        Ok(()) => Ok(Some(oid)),
        Err(RefStoreError::Conflict { .. }) => Err(GitError::OverlayConflict),
        Err(e) => Err(e.into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::git::{LocalObjectStore, LocalRefStore};
    #[tokio::test]
    async fn overlay_edit_roundtrip_and_stale_base_conflict() {
        let dir = tempfile::tempdir().unwrap();
        let objects = Arc::new(LocalObjectStore::new(dir.path()));
        let refs = LocalRefStore::new(dir.path());
        let cache = Arc::new(OverlayCache::new(2));
        let base = load_overlay(objects.clone(), &refs, "acct", &cache)
            .await
            .unwrap();
        assert!(base.blob_oids().is_empty());
        let id = parse_id(&"ab".repeat(20)).unwrap();
        let child = parse_id(&"ff".repeat(20)).unwrap();
        let edit = OverlayEdit {
            add_tombstones: vec![(id, 100)],
            set_grafts: vec![(child, vec![])],
            ..Default::default()
        };
        commit_overlay_edit(objects.as_ref(), &refs, "acct", &base, &edit)
            .await
            .unwrap();
        assert!(matches!(
            commit_overlay_edit(objects.as_ref(), &refs, "acct", &base, &edit).await,
            Err(GitError::OverlayConflict)
        ));
        let view = load_overlay(objects.clone(), &refs, "acct", &cache)
            .await
            .unwrap();
        assert_eq!(view.graft(&child).await.unwrap(), Some(vec![]));
        assert_eq!(view.deleted_at(&id).await.unwrap(), Some(100));
        assert_eq!(view.blob_oids().len(), 3);
        let edit = OverlayEdit {
            remove_tombstones: vec![id],
            ..Default::default()
        };
        commit_overlay_edit(objects.as_ref(), &refs, "acct", &view, &edit)
            .await
            .unwrap();
        let updated = load_overlay(objects, &refs, "acct", &cache).await.unwrap();
        assert!(updated.all_tombstones().await.unwrap().is_empty());
        assert_eq!(updated.shards["ff"], view.shards["ff"]);
        assert!(!updated.shards.contains_key("ab"));
        assert_eq!(view.deleted_at(&id).await.unwrap(), Some(100));
    }
}
