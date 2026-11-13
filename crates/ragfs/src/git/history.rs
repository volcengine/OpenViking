//! Rebuildable first-parent commit-graph segments and changed-path filters.
//! Git objects remain authoritative; cache errors always take the exact path.

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn filter_tracks_modes_deletions_and_type_changes() {
        use crate::git::tree_builder::TreeEditor;
        let dir = tempfile::tempdir().unwrap();
        let store = crate::git::LocalObjectStore::new(dir.path());
        let blob = ObjectId::from_bytes_or_panic(&[1; 20]);
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acct", "file", blob).await.unwrap();
        let old = editor.write(&store, "acct").await.unwrap();
        let mut editor = TreeEditor::from_tree(&store, "acct", old).await.unwrap();
        editor.root.values_mut().next().unwrap().mode =
            gix_object::tree::EntryKind::BlobExecutable.into();
        let executable = editor.write(&store, "acct").await.unwrap();
        let filter = build_bloom(
            &store,
            "acct",
            Some(old),
            executable,
            &mut BuildBudget::new(32),
        )
        .await
        .unwrap();
        assert!(
            filter.maybe_contains("file"),
            "mode-only changes must be included"
        );
        let mut editor = TreeEditor::empty();
        editor
            .upsert(&store, "acct", "file/deep/child", blob)
            .await
            .unwrap();
        let directory = editor.write(&store, "acct").await.unwrap();
        for (before, after) in [(executable, directory), (directory, executable)] {
            let filter = build_bloom(
                &store,
                "acct",
                Some(before),
                after,
                &mut BuildBudget::new(32),
            )
            .await
            .unwrap();
            for path in ["file", "file/deep", "file/deep/child"] {
                assert!(filter.maybe_contains(path));
            }
        }
        assert!(build_bloom(
            &store,
            "acct",
            Some(old),
            directory,
            &mut BuildBudget::new(0)
        )
        .await
        .is_none());
    }

    #[tokio::test]
    async fn cache_is_account_scoped_and_missing_or_corrupt_segments_fall_back() {
        let dir = tempfile::tempdir().unwrap();
        let store = crate::git::LocalObjectStore::new(dir.path());
        let cache = GraphCache::default();
        let oid = ObjectId::from_bytes_or_panic(&[1; 20]);
        let tree = ObjectId::from_bytes_or_panic(&[2; 20]);
        cache
            .save(
                &store,
                "alice",
                Segment {
                    records: vec![Record::new(oid, tree, None, None, Some(Bloom::new()))],
                },
            )
            .await;
        assert!(cache.load(&store, "alice", oid).await.is_some());
        assert!(cache.load(&store, "bob", oid).await.is_none());
        tokio::fs::write(
            dir.path()
                .join("alice/info/history-v1")
                .join(oid.to_string()),
            b"corrupt",
        )
        .await
        .unwrap();
        let cold = GraphCache::default();
        assert!(cold.load(&store, "alice", oid).await.is_none());
    }

    #[tokio::test]
    async fn stale_segment_writer_cannot_erase_completed_filters_or_tail() {
        let dir = tempfile::tempdir().unwrap();
        let a = crate::git::LocalObjectStore::new(dir.path());
        let b = crate::git::LocalObjectStore::new(dir.path());
        let oid = |n| ObjectId::from_bytes_or_panic(&[n; 20]);
        let tip = oid(1);
        let mut records = Vec::new();
        for n in 1..=5 {
            records.push(Record::new(
                oid(n),
                oid(n + 10),
                (n < 5).then(|| oid(n + 1)),
                (n < 5).then(|| oid(n + 11)),
                None,
            ));
        }
        let mut stale = Segment { records };
        a.put_history_segment("acct", &tip, stale.encode().unwrap())
            .await
            .unwrap();
        let mut complete = stale.clone();
        for record in &mut complete.records[..4] {
            record.bloom = Some(Bloom::new());
        }
        b.put_history_segment("acct", &tip, complete.encode().unwrap())
            .await
            .unwrap();
        // This reader began before B, then finishes its shorter upgrade later.
        stale.records[0].bloom = Some(Bloom::new());
        stale.records.truncate(2);
        a.put_history_segment("acct", &tip, stale.encode().unwrap())
            .await
            .unwrap();
        let bytes = a.get_history_segment("acct", &tip).await.unwrap().unwrap();
        let merged = Segment::decode(&bytes, tip).unwrap();
        assert_eq!(merged.records.len(), 5);
        assert!(merged.records[..4].iter().all(|r| r.bloom.is_some()));
    }

    #[test]
    fn bloom_includes_directories_and_rejects_unknown_version() {
        let mut bloom = Bloom::new();
        bloom.add("user/alice/file.md");
        for path in ["user", "user/alice", "user/alice/file.md"] {
            assert!(bloom.maybe_contains(path));
        }
        assert!(!bloom.maybe_contains("unrelated"));
        bloom.version += 1;
        assert!(bloom.maybe_contains("unrelated"));
    }

    #[test]
    fn segment_roundtrip_detects_corruption_and_wrong_tip() {
        let oid = ObjectId::from_bytes_or_panic(&[1; 20]);
        let tree = ObjectId::from_bytes_or_panic(&[2; 20]);
        let seg = Segment {
            records: vec![Record::new(oid, tree, None, None, Some(Bloom::new()))],
        };
        let bytes = seg.encode().unwrap();
        assert!(Segment::decode(&bytes, oid).is_some());
        assert!(Segment::decode(&bytes, tree).is_none());
        let mut bytes = bytes.to_vec();
        let i = bytes.len() / 2;
        bytes[i] ^= 1;
        assert!(Segment::decode(&bytes, oid).is_none());
    }
}
use crate::git::{object_store::ObjectStore, util};
use bytes::Bytes;
use gix_hash::ObjectId;
use gix_object::bstr::BString;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, Mutex};

pub const SEGMENT_RECORDS: usize = 128;
pub const MAX_SEGMENT_BYTES: usize = 512 * 1024;
const FORMAT_VERSION: u32 = 1;
const FILTER_BYTES: usize = 256;
const FILTER_VERSION: u32 = 1;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Bloom {
    version: u32,
    bits: Vec<u8>,
}
impl Bloom {
    pub fn new() -> Self {
        Self {
            version: FILTER_VERSION,
            bits: vec![0; FILTER_BYTES],
        }
    }
    pub fn saturated() -> Self {
        Self {
            version: FILTER_VERSION,
            bits: vec![255; FILTER_BYTES],
        }
    }
    pub fn add(&mut self, path: &str) {
        let mut end = path.len();
        loop {
            for bit in Self::positions(&path[..end]) {
                self.bits[bit / 8] |= 1 << (bit % 8);
            }
            match path[..end].rfind('/') {
                Some(i) => end = i,
                None => break,
            }
        }
    }
    fn positions(path: &str) -> [usize; 4] {
        let hash = xxhash_rust::xxh3::xxh3_64(path.as_bytes());
        let step = hash.rotate_left(32) | 1;
        std::array::from_fn(|i| {
            hash.wrapping_add((i as u64).wrapping_mul(step)) as usize % (FILTER_BYTES * 8)
        })
    }
    pub fn maybe_contains(&self, path: &str) -> bool {
        if self.version != FILTER_VERSION || self.bits.len() != FILTER_BYTES {
            return true;
        }
        Self::positions(path)
            .iter()
            .all(|&bit| self.bits[bit / 8] & (1 << (bit % 8)) != 0)
    }
}
impl Default for Bloom {
    fn default() -> Self {
        Self::new()
    }
}

/// OIDs use hex on disk; records are validated before entering the cache.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Record {
    pub oid: String,
    pub tree: String,
    pub parent: Option<String>,
    pub parent_tree: Option<String>,
    pub bloom: Option<Bloom>,
}
impl Record {
    pub fn new(
        oid: ObjectId,
        tree: ObjectId,
        parent: Option<ObjectId>,
        parent_tree: Option<ObjectId>,
        bloom: Option<Bloom>,
    ) -> Self {
        Self {
            oid: oid.to_string(),
            tree: tree.to_string(),
            parent: parent.map(|o| o.to_string()),
            parent_tree: parent_tree.map(|o| o.to_string()),
            bloom,
        }
    }
    pub fn oid(&self) -> ObjectId {
        ObjectId::from_hex(self.oid.as_bytes()).expect("validated graph oid")
    }
    pub fn tree(&self) -> ObjectId {
        ObjectId::from_hex(self.tree.as_bytes()).expect("validated graph tree")
    }
    pub fn parent(&self) -> Option<ObjectId> {
        self.parent
            .as_ref()
            .map(|s| ObjectId::from_hex(s.as_bytes()).expect("validated parent"))
    }
    pub fn parent_tree(&self) -> Option<ObjectId> {
        self.parent_tree
            .as_ref()
            .map(|s| ObjectId::from_hex(s.as_bytes()).expect("validated parent tree"))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Segment {
    pub records: Vec<Record>,
}
#[derive(Serialize, Deserialize)]
struct Envelope {
    version: u32,
    checksum: u64,
    payload: String,
}
impl Segment {
    /// Compatible records describe immutable objects. Keep the longest prefix
    /// and every usable filter so a stale writer cannot undo completed work.
    pub fn merge(&self, incoming: &Self) -> Option<Self> {
        let mut merged = if self.records.len() >= incoming.records.len() {
            self.clone()
        } else {
            incoming.clone()
        };
        for (i, (old, new)) in self.records.iter().zip(&incoming.records).enumerate() {
            if old.oid != new.oid
                || old.tree != new.tree
                || old.parent != new.parent
                || old.parent_tree != new.parent_tree
            {
                return None;
            }
            let useful = |b: &Bloom| {
                b.version == FILTER_VERSION
                    && b.bits.len() == FILTER_BYTES
                    && b.bits.iter().any(|&byte| byte != 255)
            };
            merged.records[i].bloom = if old.bloom.as_ref().is_some_and(useful) {
                old.bloom.clone()
            } else {
                new.bloom.clone().or_else(|| old.bloom.clone())
            };
        }
        Some(merged)
    }

    pub fn encode(&self) -> Option<Bytes> {
        if self.records.is_empty() || self.records.len() > SEGMENT_RECORDS {
            return None;
        }
        let payload = serde_json::to_string(self).ok()?;
        let checksum = xxhash_rust::xxh3::xxh3_64(payload.as_bytes());
        let bytes = serde_json::to_vec(&Envelope {
            version: FORMAT_VERSION,
            checksum,
            payload,
        })
        .ok()?;
        (bytes.len() <= MAX_SEGMENT_BYTES).then(|| Bytes::from(bytes))
    }
    pub fn decode(bytes: &[u8], tip: ObjectId) -> Option<Self> {
        if bytes.len() > MAX_SEGMENT_BYTES {
            return None;
        }
        let env: Envelope = serde_json::from_slice(bytes).ok()?;
        if env.version != FORMAT_VERSION
            || xxhash_rust::xxh3::xxh3_64(env.payload.as_bytes()) != env.checksum
        {
            return None;
        }
        let segment: Self = serde_json::from_str(&env.payload).ok()?;
        if segment.records.is_empty() || segment.records.len() > SEGMENT_RECORDS {
            return None;
        }
        let mut expected = Some(tip);
        let mut seen = HashSet::new();
        for record in &segment.records {
            let parse = |s: &str| {
                if s.len() == 40 {
                    ObjectId::from_hex(s.as_bytes()).ok()
                } else {
                    None
                }
            };
            let oid = parse(&record.oid)?;
            let _tree = parse(&record.tree)?;
            if expected != Some(oid) || !seen.insert(oid) {
                return None;
            }
            expected = match &record.parent {
                Some(s) => Some(parse(s)?),
                None => None,
            };
            match (&record.parent, &record.parent_tree) {
                (Some(_), Some(s)) => {
                    parse(s)?;
                }
                (None, None) => {}
                _ => return None,
            }
        }
        for pair in segment.records.windows(2) {
            if pair[0].parent_tree.as_ref() != Some(&pair[1].tree) {
                return None;
            }
        }
        if expected.is_some_and(|oid| seen.contains(&oid)) {
            return None;
        }
        Some(segment)
    }
}

/// Bounded per-service cache: at most 32 segments, each at most 512 KiB encoded.
/// No lock is held across I/O. Keys include the account, never only the OID.
#[derive(Default)]
pub struct GraphCache {
    segments: Mutex<HashMap<(String, ObjectId), Arc<Segment>>>,
}
impl GraphCache {
    pub async fn load(
        &self,
        store: &dyn ObjectStore,
        account: &str,
        tip: ObjectId,
    ) -> Option<Arc<Segment>> {
        let key = (account.to_string(), tip);
        if let Some(segment) = self.segments.lock().ok()?.get(&key) {
            return Some(segment.clone());
        }
        let bytes = tokio::time::timeout(
            std::time::Duration::from_millis(250),
            store.get_history_segment(account, &tip),
        )
        .await
        .ok()?
        .ok()??;
        let segment = Arc::new(Segment::decode(&bytes, tip)?);
        self.insert(key, segment.clone());
        Some(segment)
    }
    fn insert(&self, key: (String, ObjectId), segment: Arc<Segment>) {
        if let Ok(mut segments) = self.segments.lock() {
            if segments.len() >= 32 && !segments.contains_key(&key) {
                segments.clear();
            }
            let segment = segments
                .get(&key)
                .and_then(|old| old.merge(&segment))
                .map(Arc::new)
                .unwrap_or(segment);
            segments.insert(key, segment);
        }
    }
    pub async fn save(&self, store: &dyn ObjectStore, account: &str, segment: Segment) -> bool {
        let Some(bytes) = segment.encode() else {
            return false;
        };
        let tip = segment.records[0].oid();
        match tokio::time::timeout(
            std::time::Duration::from_millis(250),
            store.put_history_segment(account, &tip, bytes),
        )
        .await
        {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                tracing::warn!(%error, "snapshot history segment write failed");
                return false;
            }
            Err(_) => {
                tracing::warn!("snapshot history segment write timed out");
                return false;
            }
        }
        self.insert((account.to_owned(), tip), Arc::new(segment));
        true
    }

    pub async fn anchor(
        &self,
        store: &dyn ObjectStore,
        account: &str,
        branch: &str,
    ) -> Option<Arc<Segment>> {
        let tip = tokio::time::timeout(
            std::time::Duration::from_millis(250),
            store.get_history_anchor(account, branch),
        )
        .await
        .ok()?
        .ok()??;
        self.load(store, account, tip).await
    }

    pub async fn publish_anchor(
        &self,
        store: &dyn ObjectStore,
        account: &str,
        branch: &str,
        tip: ObjectId,
    ) {
        let _ = tokio::time::timeout(
            std::time::Duration::from_millis(250),
            store.put_history_anchor(account, branch, tip),
        )
        .await;
    }
}

/// Work allowance shared by all lazy filter construction in one log request.
/// Exhaustion returns an unknown filter, never a partial Bloom filter.
pub struct BuildBudget {
    pub trees: usize,
    entries: usize,
}
impl BuildBudget {
    pub fn new(trees: usize) -> Self {
        Self {
            trees,
            entries: 8192,
        }
    }
}

async fn bounded_tree(
    store: &dyn ObjectStore,
    account: &str,
    oid: Option<ObjectId>,
    budget: &mut BuildBudget,
) -> Option<BTreeMap<BString, gix_object::tree::Entry>> {
    let Some(oid) = oid else {
        return Some(BTreeMap::new());
    };
    if budget.trees == 0 {
        return None;
    }
    budget.trees -= 1;
    let raw = util::read_object_limited(store, account, &oid, 1024 * 1024)
        .await
        .ok()?;
    let (kind, _, offset) = util::parse_object_header(&raw).ok()?;
    if kind != gix_object::Kind::Tree {
        return None;
    }
    let tree = gix_object::TreeRef::from_bytes(&raw[offset..]).ok()?;
    if tree.entries.len() > budget.entries {
        budget.entries = 0;
        return None;
    }
    budget.entries -= tree.entries.len();
    Some(
        gix_object::Tree::from(tree)
            .entries
            .into_iter()
            .map(|e| (e.filename.clone(), e))
            .collect(),
    )
}

/// Diff actual trees, including mode-only changes and changed directory entries.
/// A read/decode error or exhausted budget produces None (all paths may match).
pub async fn build_bloom(
    store: &dyn ObjectStore,
    account: &str,
    base: Option<ObjectId>,
    head: ObjectId,
    budget: &mut BuildBudget,
) -> Option<Bloom> {
    let mut bloom = Bloom::new();
    let mut stack = vec![(String::new(), base, Some(head))];
    let mut changed = 0;
    while let Some((prefix, base, head)) = stack.pop() {
        if base == head {
            continue;
        }
        let old = bounded_tree(store, account, base, budget).await?;
        let new = bounded_tree(store, account, head, budget).await?;
        let names: std::collections::BTreeSet<_> = old.keys().chain(new.keys()).collect();
        for name in names {
            let left = old.get(name);
            let right = new.get(name);
            if left.map(|e| (e.oid, e.mode)) == right.map(|e| (e.oid, e.mode)) {
                continue;
            }
            changed += 1;
            if changed > 256 {
                return None;
            }
            // Non-UTF8 paths cannot be queried by this API; conservatively skip
            // acceleration for the entire commit rather than normalize bytes.
            let name = std::str::from_utf8(name.as_ref()).ok()?;
            let path = if prefix.is_empty() {
                name.to_owned()
            } else {
                format!("{prefix}/{name}")
            };
            if path.len() > 4096 {
                return None;
            }
            bloom.add(&path);
            let subtree =
                |e: Option<&gix_object::tree::Entry>| e.filter(|e| e.mode.is_tree()).map(|e| e.oid);
            let (base, head) = (subtree(left), subtree(right));
            if base.is_some() || head.is_some() {
                stack.push((path, base, head));
            }
        }
    }
    Some(bloom)
}

/// Branch names never become filesystem components. Hash collisions can only
/// choose a less useful hint: readers still traverse from the requested HEAD.
pub fn anchor_name(branch: &str) -> String {
    let branch = if branch.starts_with("refs/") {
        branch.to_owned()
    } else {
        format!("refs/heads/{branch}")
    };
    format!(
        "anchor-{:032x}",
        xxhash_rust::xxh3::xxh3_128(branch.as_bytes())
    )
}
pub fn encode_anchor(tip: ObjectId) -> Bytes {
    Bytes::from(format!("1 {tip}"))
}
pub fn decode_anchor(bytes: &[u8]) -> Option<ObjectId> {
    if bytes.len() != 42 || &bytes[..2] != b"1 " {
        return None;
    }
    ObjectId::from_hex(&bytes[2..]).ok()
}

/// Merge validated wire data. Bad existing cache data is replaceable; incoming
/// invalid/inconsistent data is rejected rather than published.
pub fn merge_bytes(old: Option<&[u8]>, incoming: &[u8], tip: ObjectId) -> Option<Bytes> {
    let incoming = Segment::decode(incoming, tip)?;
    match old.and_then(|bytes| Segment::decode(bytes, tip)) {
        Some(old) => old.merge(&incoming)?.encode(),
        None => incoming.encode(),
    }
}
