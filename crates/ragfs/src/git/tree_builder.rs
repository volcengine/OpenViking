//! Async-native Git tree editor for building and modifying tree objects.

use std::collections::{BTreeMap, HashMap};

use gix_hash::ObjectId;
use gix_object::bstr::{BString, ByteSlice};
use gix_object::tree::{self, EntryKind};
use gix_object::{Tree, TreeRef, WriteTo};

use crate::git::error::GitError;
use crate::git::object_store::ObjectStore;
use crate::git::util::{parse_object_header, read_object, write_object};

/// Type alias for tree entries mapping path components to tree entries
pub type TreeEntries = BTreeMap<BString, tree::Entry>;

/// Editor for constructing and modifying Git tree objects
pub struct TreeEditor {
    pub(crate) root: TreeEntries,
    pub(crate) subtrees: HashMap<BString, TreeEntries>,
}

impl TreeEditor {
    /// Create a new empty TreeEditor
    pub fn empty() -> Self {
        Self {
            root: BTreeMap::new(),
            subtrees: HashMap::new(),
        }
    }

    /// Split a path into components, validating each component.
    fn split_path(path: &str) -> Result<Vec<&str>, GitError> {
        if path.is_empty() {
            return Err(GitError::Other("empty path".into()));
        }

        let components: Vec<&str> = path.split('/').collect();
        for comp in &components {
            if comp.is_empty() {
                return Err(GitError::Other("empty path component".into()));
            }
        }
        Ok(components)
    }

    /// Join path components into a `dir1/dir2/...` BString key.
    fn join_prefix(parts: &[&str]) -> BString {
        let mut out = BString::default();
        for (i, p) in parts.iter().enumerate() {
            if i > 0 {
                out.push(b'/');
            }
            out.extend_from_slice(p.as_bytes());
        }
        out
    }

    /// Ensure every subtree along `parent_dirs` is loaded into `self.subtrees`.
    ///
    /// Walks top-down. For each level not yet present in `self.subtrees`, it
    /// reads the directory entry from the parent level and lazily loads the
    /// referenced tree object (Fast Path 2: untouched subtrees are never
    /// loaded). Returns `Ok(false)` if a component is missing and
    /// `create_missing` is false (used by `remove` for its no-op semantics);
    /// `Ok(true)` once all levels are present.
    async fn ensure_path_loaded(
        &mut self,
        store: &dyn ObjectStore,
        account: &str,
        parent_dirs: &[&str],
        create_missing: bool,
    ) -> Result<bool, GitError> {
        for depth in 1..=parent_dirs.len() {
            let dir_name = parent_dirs[depth - 1];
            let child_key = Self::join_prefix(&parent_dirs[..depth]);

            if self.subtrees.contains_key(&child_key) {
                continue;
            }

            // Inspect the directory entry in the parent level. Copy out the oid
            // (or the decision) before any await to avoid holding a borrow of
            // `self.subtrees`/`self.root` across the load.
            enum Action {
                Load(ObjectId),
                Empty,
                Missing,
            }
            let action = {
                let parent: &TreeEntries = if depth == 1 {
                    &self.root
                } else {
                    let parent_key = Self::join_prefix(&parent_dirs[..depth - 1]);
                    self.subtrees.get(&parent_key).ok_or_else(|| {
                        GitError::Other(format!("subtree not loaded: {parent_key}"))
                    })?
                };
                match parent.get(dir_name.as_bytes().as_bstr()) {
                    // Existing directory entry: either an empty subtree or one
                    // to load. Null and the well-known empty-tree oid are both
                    // treated as empty — the empty tree object is conventionally
                    // never physically stored, so we must not try to load it.
                    Some(entry) if entry.mode == EntryKind::Tree.into() => {
                        if entry.oid.is_null()
                            || entry.oid == ObjectId::empty_tree(gix_hash::Kind::Sha1)
                        {
                            Action::Empty
                        } else {
                            Action::Load(entry.oid)
                        }
                    }
                    // Either no entry, or one that exists but is a file/symlink
                    // (a file→dir transition). Treat both as missing: the create
                    // path overwrites the stale opposite-kind entry with a fresh
                    // directory; the no-create path (remove) no-ops.
                    _ => Action::Missing,
                }
            };

            match action {
                Action::Load(oid) => {
                    let entries = load_tree_entries(store, account, &oid).await?;
                    self.subtrees.insert(child_key, entries);
                }
                Action::Empty => {
                    self.subtrees.insert(child_key, BTreeMap::new());
                }
                Action::Missing => {
                    if !create_missing {
                        return Ok(false);
                    }
                    // Create the directory entry in the parent level, then an
                    // empty subtree for it.
                    let parent: &mut TreeEntries = if depth == 1 {
                        &mut self.root
                    } else {
                        let parent_key = Self::join_prefix(&parent_dirs[..depth - 1]);
                        self.subtrees.entry(parent_key).or_insert_with(BTreeMap::new)
                    };
                    parent.insert(
                        dir_name.into(),
                        tree::Entry {
                            mode: EntryKind::Tree.into(),
                            filename: dir_name.into(),
                            oid: ObjectId::null(gix_hash::Kind::Sha1),
                        },
                    );
                    self.subtrees.insert(child_key, BTreeMap::new());
                }
            }
        }
        Ok(true)
    }

    /// Upsert a blob object at the given path.
    pub async fn upsert(
        &mut self,
        store: &dyn ObjectStore,
        account: &str,
        path: &str,
        oid: ObjectId,
    ) -> Result<(), GitError> {
        let components = Self::split_path(path)?;
        let (filename, parent_dirs) = components
            .split_last()
            .ok_or_else(|| GitError::Other("empty path".into()))?;

        let leaf = tree::Entry {
            mode: EntryKind::Blob.into(),
            filename: (*filename).into(),
            oid,
        };

        if parent_dirs.is_empty() {
            self.root.insert((*filename).into(), leaf);
            return Ok(());
        }

        self.ensure_path_loaded(store, account, parent_dirs, true)
            .await?;

        let leaf_key = Self::join_prefix(parent_dirs);
        let subtree = self.subtrees.entry(leaf_key).or_insert_with(BTreeMap::new);
        subtree.insert((*filename).into(), leaf);

        Ok(())
    }

    /// Remove a path from the tree. No-op if the path does not exist.
    pub async fn remove(
        &mut self,
        store: &dyn ObjectStore,
        account: &str,
        path: &str,
    ) -> Result<(), GitError> {
        let components = Self::split_path(path)?;
        let (filename, parent_dirs) = components
            .split_last()
            .ok_or_else(|| GitError::Other("empty path".into()))?;

        if parent_dirs.is_empty() {
            self.root.remove(filename.as_bytes().as_bstr());
            return Ok(());
        }

        // Missing ancestor → nothing to remove (keep no-op semantics, do not
        // create directories).
        if !self
            .ensure_path_loaded(store, account, parent_dirs, false)
            .await?
        {
            return Ok(());
        }

        let prefix = Self::join_prefix(parent_dirs);
        if let Some(subtree) = self.subtrees.get_mut(&prefix) {
            subtree.remove(filename.as_bytes().as_bstr());
        }
        Ok(())
    }

    /// Splice an existing subtree (referenced by its OID) into the editor at the
    /// given path. The path's intermediate ancestors are created as needed.
    ///
    /// Any in-memory editor state under `path` is discarded — subsequent
    /// `write()` calls will reference `subtree_oid` directly without rebuilding
    /// the subtree. This is the API `restore` uses to swap a whole project
    /// directory to a historical version without enumerating every file.
    ///
    /// Note: if you later call `upsert`/`remove` *inside* the spliced subtree
    /// (e.g. `upsert_subtree("a/b", oid); upsert("a/b/x.txt", ...)`), the
    /// in-memory state for "a/b" is rebuilt from those edits alone — the
    /// contents of `subtree_oid` are not merged in. Splice, then edit, is a
    /// destructive pattern.
    pub async fn upsert_subtree(
        &mut self,
        store: &dyn ObjectStore,
        account: &str,
        path: &str,
        subtree_oid: ObjectId,
    ) -> Result<(), GitError> {
        let components = Self::split_path(path)?;
        let (dirname, parent_dirs) = components
            .split_last()
            .ok_or_else(|| GitError::Other("empty path".into()))?;

        // Ensure each ancestor directory's subtree is loaded (so sibling
        // entries are preserved), creating missing ancestors as needed.
        self.ensure_path_loaded(store, account, parent_dirs, true)
            .await?;

        // Insert the leaf Tree entry pointing at the precomputed subtree.
        let leaf_entry = tree::Entry {
            mode: EntryKind::Tree.into(),
            filename: (*dirname).into(),
            oid: subtree_oid,
        };
        let leaf_parent: &mut TreeEntries = if parent_dirs.is_empty() {
            &mut self.root
        } else {
            let key = Self::join_prefix(parent_dirs);
            self.subtrees.entry(key).or_insert_with(BTreeMap::new)
        };
        leaf_parent.insert((*dirname).into(), leaf_entry);

        // Drop any stale in-memory state at or beneath `path` so write_subtree
        // doesn't recurse — it will reuse `subtree_oid` directly.
        let prefix = Self::join_prefix(&components);
        let prefix_slash: Vec<u8> = {
            let mut v = Vec::with_capacity(prefix.len() + 1);
            v.extend_from_slice(prefix.as_slice());
            v.push(b'/');
            v
        };
        let to_remove: Vec<BString> = self
            .subtrees
            .keys()
            .filter(|k| {
                k.as_slice() == prefix.as_slice()
                    || k.as_slice().starts_with(&prefix_slash)
            })
            .cloned()
            .collect();
        for k in to_remove {
            self.subtrees.remove(&k);
        }

        Ok(())
    }

    /// Load an existing tree from ObjectStore as the editing base.
    ///
    /// Only the root tree is loaded eagerly; subtrees are loaded lazily on
    /// first `upsert`/`remove`/`upsert_subtree` that touches them. Untouched
    /// subtrees are never read into memory and are reused as-is during
    /// `write` (Fast Path 2).
    pub async fn from_tree(
        store: &dyn ObjectStore,
        account: &str,
        tree_oid: ObjectId,
    ) -> Result<Self, GitError> {
        let mut editor = Self::empty();
        editor.root = load_tree_entries(store, account, &tree_oid).await?;
        Ok(editor)
    }

    /// Write all in-memory trees to ObjectStore, returning the root tree oid.
    /// Writes bottom-up: leaf subtrees first, then their parents.
    /// Empty subtrees are pruned.
    pub async fn write(
        &mut self,
        store: &dyn ObjectStore,
        account: &str,
    ) -> Result<ObjectId, GitError> {
        self.write_subtree(store, account, &BString::default()).await
    }

    fn write_subtree<'a>(
        &'a mut self,
        store: &'a dyn ObjectStore,
        account: &'a str,
        prefix: &'a BString,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<ObjectId, GitError>> + Send + 'a>>
    {
        Box::pin(async move {
            // Snapshot entry keys so we can mutate self.subtrees during recursion.
            let entry_specs: Vec<(BString, tree::Entry)> = {
                let entries = if prefix.is_empty() {
                    &self.root
                } else {
                    self.subtrees.get(prefix).ok_or_else(|| {
                        GitError::Other(format!("subtree not found: {prefix}"))
                    })?
                };
                entries.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
            };

            let mut result_entries: Vec<tree::Entry> = Vec::with_capacity(entry_specs.len());
            for (name, entry) in entry_specs {
                if entry.mode.is_tree() {
                    let child_prefix = if prefix.is_empty() {
                        name.clone()
                    } else {
                        let mut p = prefix.clone();
                        p.push(b'/');
                        p.extend_from_slice(&name);
                        p
                    };

                    match self.subtrees.get(&child_prefix) {
                        Some(child_entries) if child_entries.is_empty() => {
                            // Prune empty subtree.
                            continue;
                        }
                        Some(_) => {
                            // Subtree has in-memory edits — recurse to write them.
                            let child_oid = self.write_subtree(store, account, &child_prefix).await?;
                            result_entries.push(tree::Entry {
                                mode: EntryKind::Tree.into(),
                                filename: name,
                                oid: child_oid,
                            });
                        }
                        None => {
                            // No in-memory state: use the entry's existing OID as-is
                            // (e.g. placed by upsert_subtree or from_tree for untouched
                            //  subtrees). This is the Fast Path 2 optimisation.
                            result_entries.push(entry);
                        }
                    }
                } else {
                    result_entries.push(entry);
                }
            }

            result_entries.sort();
            let tree = Tree { entries: result_entries };
            let mut buf = Vec::new();
            tree.write_to(&mut buf)
                .map_err(|e| GitError::Other(format!("tree serialization: {e}")))?;

            let oid = write_object(store, account, gix_object::Kind::Tree, &buf).await?;
            Ok(oid)
        })
    }
}

/// Convert BTreeMap entries to a `gix_object::Tree` with Git sort order.
#[cfg(test)]
fn entries_to_tree(entries: &TreeEntries) -> Tree {
    let mut sorted: Vec<tree::Entry> = entries.values().cloned().collect();
    sorted.sort();
    Tree { entries: sorted }
}

/// Read and parse a tree object from ObjectStore.
async fn load_tree(
    store: &dyn ObjectStore,
    account: &str,
    oid: &ObjectId,
) -> Result<Tree, GitError> {
    let raw = read_object(store, account, oid).await?;
    let (_, _, header_len) = parse_object_header(&raw)?;
    let tree_ref = TreeRef::from_bytes(&raw[header_len..])
        .map_err(|e| GitError::CorruptedObject(format!("invalid tree: {e}")))?;
    Ok(Tree::from(tree_ref))
}

/// Load a tree object and convert it into a `TreeEntries` map keyed by filename.
async fn load_tree_entries(
    store: &dyn ObjectStore,
    account: &str,
    oid: &ObjectId,
) -> Result<TreeEntries, GitError> {
    let tree = load_tree(store, account, oid).await?;
    let mut entries = BTreeMap::new();
    for entry in tree.entries {
        entries.insert(entry.filename.clone(), entry);
    }
    Ok(entries)
}

/// Recursively flatten a tree into (path, blob_oid) pairs.
///
/// If `path_filter` is Some, only include blob paths whose prefix matches
/// any of the filter prefixes. Subtrees are descended into only when relevant.
pub async fn flatten(
    store: &dyn ObjectStore,
    account: &str,
    tree_oid: ObjectId,
    path_filter: &Option<Vec<String>>,
) -> Result<Vec<(String, ObjectId)>, GitError> {
    let mut result = Vec::new();
    let mut stack: Vec<(String, ObjectId)> = vec![(String::new(), tree_oid)];

    while let Some((prefix, oid)) = stack.pop() {
        let tree = load_tree(store, account, &oid).await?;
        for entry in tree.entries {
            let path = if prefix.is_empty() {
                entry.filename.to_string()
            } else {
                format!("{}/{}", prefix, entry.filename)
            };

            if entry.mode.is_tree() {
                let should_descend = match path_filter {
                    None => true,
                    Some(filters) => filters
                        .iter()
                        .any(|f| path.starts_with(f) || f.starts_with(&path)),
                };
                if should_descend {
                    stack.push((path, entry.oid));
                }
            } else {
                let include = match path_filter {
                    None => true,
                    Some(filters) => filters.iter().any(|f| path.starts_with(f)),
                };
                if include {
                    result.push((path, entry.oid));
                }
            }
        }
    }

    result.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(result)
}

/// Look up a single path in a tree, returning the entry's oid and mode.
/// Returns `Ok(None)` if the path doesn't exist.
pub async fn lookup(
    store: &dyn ObjectStore,
    account: &str,
    tree_oid: ObjectId,
    path: &str,
) -> Result<Option<(ObjectId, tree::EntryMode)>, GitError> {
    let mut cache = TreeLookupCache::new();
    lookup_cached(store, account, tree_oid, path, &mut cache).await
}

/// In-memory cache of decoded tree objects keyed by their OID. Intended for use
/// across many `lookup_cached` calls that share the same root (e.g. the commit
/// hot loop, where K candidate paths each walk depth-D ancestor trees that
/// otherwise get re-fetched + re-zlib-decoded K×D times).
///
/// Entries are `Arc`-shared so a clone is cheap; the cache is single-writer
/// (the caller's `&mut`) so no internal locking is needed.
pub struct TreeLookupCache {
    by_oid: HashMap<ObjectId, std::sync::Arc<TreeEntries>>,
}

impl TreeLookupCache {
    /// Create an empty cache.
    pub fn new() -> Self {
        Self {
            by_oid: HashMap::new(),
        }
    }

    /// Pre-seed the cache with an already-decoded tree's entries. Useful when
    /// the caller has the root entries on hand (e.g. from `TreeEditor::from_tree`)
    /// and wants the very first `lookup_cached` to skip the redundant fetch.
    pub fn seed(&mut self, oid: ObjectId, entries: TreeEntries) {
        self.by_oid.insert(oid, std::sync::Arc::new(entries));
    }
}

impl Default for TreeLookupCache {
    fn default() -> Self {
        Self::new()
    }
}

/// Same as [`lookup`], but reuses an external [`TreeLookupCache`] across calls
/// so each tree object is fetched + decoded at most once. The cache is keyed
/// on the *content-addressed* tree OID, so it stays correct across calls with
/// different starting roots.
pub async fn lookup_cached(
    store: &dyn ObjectStore,
    account: &str,
    tree_oid: ObjectId,
    path: &str,
    cache: &mut TreeLookupCache,
) -> Result<Option<(ObjectId, tree::EntryMode)>, GitError> {
    if path.is_empty() {
        return Err(GitError::Other("empty path".into()));
    }
    let components: Vec<&str> = path.split('/').collect();
    let mut current_oid = tree_oid;

    for (i, component) in components.iter().enumerate() {
        if component.is_empty() {
            return Err(GitError::Other("empty path component".into()));
        }
        let entries = match cache.by_oid.get(&current_oid) {
            Some(e) => e.clone(),
            None => {
                let loaded = std::sync::Arc::new(
                    load_tree_entries(store, account, &current_oid).await?,
                );
                cache.by_oid.insert(current_oid, loaded.clone());
                loaded
            }
        };
        let filename = component.as_bytes();
        let is_last = i == components.len() - 1;

        match entries.get(filename.as_bstr()) {
            Some(entry) => {
                if is_last {
                    return Ok(Some((entry.oid, entry.mode)));
                } else if entry.mode.is_tree() {
                    current_oid = entry.oid;
                } else {
                    return Ok(None);
                }
            }
            None => return Ok(None),
        }
    }

    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dummy_oid() -> ObjectId {
        ObjectId::null(gix_hash::Kind::Sha1)
    }

    #[test]
    fn test_empty_editor() {
        let editor = TreeEditor::empty();
        assert!(editor.root.is_empty());
        assert!(editor.subtrees.is_empty());
    }

    #[tokio::test]
    async fn test_upsert_single_file() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = dummy_oid();

        editor.upsert(&store, "acc", "file.txt", oid).await.unwrap();

        assert_eq!(editor.root.len(), 1);
        let entry = editor.root.get("file.txt".as_bytes().as_bstr()).unwrap();
        assert_eq!(entry.mode, EntryKind::Blob.into());
        assert_eq!(entry.oid, oid);
        assert_eq!(entry.filename, "file.txt");
    }

    #[tokio::test]
    async fn test_upsert_nested_path() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = dummy_oid();

        editor
            .upsert(&store, "acc", "dir/subdir/file.txt", oid)
            .await
            .unwrap();

        // Root has dir
        assert_eq!(editor.root.len(), 1);
        let dir_entry = editor.root.get("dir".as_bytes().as_bstr()).unwrap();
        assert_eq!(dir_entry.mode, EntryKind::Tree.into());

        // Subtrees has dir
        let dir_subtree = editor.subtrees.get("dir".as_bytes().as_bstr()).unwrap();
        assert_eq!(dir_subtree.len(), 1);
        let subdir_entry = dir_subtree.get("subdir".as_bytes().as_bstr()).unwrap();
        assert_eq!(subdir_entry.mode, EntryKind::Tree.into());

        // Subdir subtree
        let subdir_subtree = editor.subtrees.get("dir/subdir".as_bytes().as_bstr()).unwrap();
        assert_eq!(subdir_subtree.len(), 1);
        let file_entry = subdir_subtree.get("file.txt".as_bytes().as_bstr()).unwrap();
        assert_eq!(file_entry.mode, EntryKind::Blob.into());
        assert_eq!(file_entry.oid, oid);
    }

    #[tokio::test]
    async fn test_upsert_overwrite() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid1 = dummy_oid();
        let oid2 = ObjectId::from_hex(b"abcdef1234567890abcdef1234567890abcdef12").unwrap();

        editor.upsert(&store, "acc", "file.txt", oid1).await.unwrap();
        editor.upsert(&store, "acc", "file.txt", oid2).await.unwrap();

        let entry = editor.root.get("file.txt".as_bytes().as_bstr()).unwrap();
        assert_eq!(entry.oid, oid2);
    }

    #[tokio::test]
    async fn test_upsert_empty_component_rejected() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = dummy_oid();

        assert!(editor.upsert(&store, "acc", "", oid).await.is_err());
        assert!(editor.upsert(&store, "acc", "file//txt", oid).await.is_err());
        assert!(editor.upsert(&store, "acc", "/file.txt", oid).await.is_err());
        assert!(editor.upsert(&store, "acc", "file.txt/", oid).await.is_err());
    }

    #[tokio::test]
    async fn test_remove_existing() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = dummy_oid();

        editor.upsert(&store, "acc", "dir/file.txt", oid).await.unwrap();
        assert_eq!(editor.root.len(), 1);

        editor.remove(&store, "acc", "dir/file.txt").await.unwrap();

        let dir_subtree = editor.subtrees.get("dir".as_bytes().as_bstr()).unwrap();
        assert!(dir_subtree.is_empty());
    }

    #[tokio::test]
    async fn test_remove_nonexistent_is_noop() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        editor.remove(&store, "acc", "nonexistent.txt").await.unwrap();
        editor.remove(&store, "acc", "dir/nonexistent.txt").await.unwrap();
    }

    #[tokio::test]
    async fn test_upsert_top_level_file() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = dummy_oid();

        editor.upsert(&store, "acc", "top-level.txt", oid).await.unwrap();

        assert_eq!(editor.root.len(), 1);
        let entry = editor.root.get("top-level.txt".as_bytes().as_bstr()).unwrap();
        assert_eq!(entry.mode, EntryKind::Blob.into());
        assert_eq!(entry.filename, "top-level.txt");
        assert_eq!(entry.oid, oid);
    }

    #[tokio::test]
    async fn test_remove_top_level_file() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = dummy_oid();

        editor.upsert(&store, "acc", "single.txt", oid).await.unwrap();
        assert_eq!(editor.root.len(), 1);

        editor.remove(&store, "acc", "single.txt").await.unwrap();
        assert_eq!(editor.root.len(), 0);
    }

    // --- Test helpers ---

    fn make_store() -> (tempfile::TempDir, crate::git::backends::local::LocalObjectStore) {
        let dir = tempfile::tempdir().unwrap();
        let store = crate::git::backends::local::LocalObjectStore::new(dir.path());
        (dir, store)
    }

    fn serialize_tree(tree: &Tree) -> Vec<u8> {
        let mut buf = Vec::new();
        tree.write_to(&mut buf).unwrap();
        buf
    }

    fn oid_hex(hex: &[u8; 40]) -> ObjectId {
        ObjectId::from_hex(hex).unwrap()
    }

    // --- from_tree ---

    #[tokio::test]
    async fn test_from_tree_empty() {
        let (_d, store) = make_store();
        let empty_tree = Tree { entries: Vec::new() };
        let oid = write_object(&store, "acc", gix_object::Kind::Tree, &serialize_tree(&empty_tree))
            .await
            .unwrap();

        let editor = TreeEditor::from_tree(&store, "acc", oid).await.unwrap();
        assert!(editor.root.is_empty());
        assert!(editor.subtrees.is_empty());
    }

    #[tokio::test]
    async fn test_from_tree_with_entries() {
        let (_d, store) = make_store();

        let blob_a = oid_hex(b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        let blob_b = oid_hex(b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");

        let resources_tree = Tree {
            entries: vec![tree::Entry {
                mode: EntryKind::Blob.into(),
                filename: "b.md".into(),
                oid: blob_b,
            }],
        };
        let resources_oid = write_object(&store, "acc", gix_object::Kind::Tree, &serialize_tree(&resources_tree))
            .await
            .unwrap();

        let root_tree = Tree {
            entries: vec![
                tree::Entry {
                    mode: EntryKind::Blob.into(),
                    filename: "a.md".into(),
                    oid: blob_a,
                },
                tree::Entry {
                    mode: EntryKind::Tree.into(),
                    filename: "resources".into(),
                    oid: resources_oid,
                },
            ],
        };
        let root_oid = write_object(&store, "acc", gix_object::Kind::Tree, &serialize_tree(&root_tree))
            .await
            .unwrap();

        let editor = TreeEditor::from_tree(&store, "acc", root_oid).await.unwrap();
        assert_eq!(editor.root.len(), 2);
        assert!(editor.root.contains_key("a.md".as_bytes().as_bstr()));
        assert!(editor.root.contains_key("resources".as_bytes().as_bstr()));

        // Lazy loading: from_tree only loads root; subtrees are not read yet.
        assert!(editor.subtrees.is_empty());
    }

    // --- write ---

    #[tokio::test]
    async fn test_write_empty_tree() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let oid = editor.write(&store, "acc").await.unwrap();
        assert_eq!(oid, ObjectId::empty_tree(gix_hash::Kind::Sha1));
    }

    #[tokio::test]
    async fn test_write_single_blob() {
        let (_d, store) = make_store();
        let blob_oid = oid_hex(b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "README.md", blob_oid).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let tree = load_tree(&store, "acc", &root_oid).await.unwrap();
        assert_eq!(tree.entries.len(), 1);
        assert_eq!(tree.entries[0].filename, "README.md");
        assert_eq!(tree.entries[0].oid, blob_oid);
    }

    #[tokio::test]
    async fn test_write_nested_structure() {
        let (_d, store) = make_store();
        let oid1 = oid_hex(b"1111111111111111111111111111111111111111");
        let oid2 = oid_hex(b"2222222222222222222222222222222222222222");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "README.md", oid1).await.unwrap();
        editor.upsert(&store, "acc", "resources/docs/a.md", oid2).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let root_tree = load_tree(&store, "acc", &root_oid).await.unwrap();
        assert_eq!(root_tree.entries.len(), 2);

        let resources_entry = root_tree
            .entries
            .iter()
            .find(|e| e.filename == "resources")
            .unwrap();
        assert!(resources_entry.mode.is_tree());

        let res_tree = load_tree(&store, "acc", &resources_entry.oid).await.unwrap();
        assert_eq!(res_tree.entries.len(), 1);

        let docs_entry = &res_tree.entries[0];
        assert!(docs_entry.mode.is_tree());
        let docs_tree = load_tree(&store, "acc", &docs_entry.oid).await.unwrap();
        assert_eq!(docs_tree.entries.len(), 1);
        assert_eq!(docs_tree.entries[0].oid, oid2);
    }

    #[tokio::test]
    async fn test_round_trip_from_tree_upsert_write() {
        let (_d, store) = make_store();
        let oid1 = oid_hex(b"1111111111111111111111111111111111111111");
        let oid2 = oid_hex(b"2222222222222222222222222222222222222222");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "a.md", oid1).await.unwrap();
        let first_oid = editor.write(&store, "acc").await.unwrap();

        let mut editor2 = TreeEditor::from_tree(&store, "acc", first_oid).await.unwrap();
        editor2.upsert(&store, "acc", "b.md", oid2).await.unwrap();
        let second_oid = editor2.write(&store, "acc").await.unwrap();

        let tree = load_tree(&store, "acc", &second_oid).await.unwrap();
        assert_eq!(tree.entries.len(), 2);
    }

    #[tokio::test]
    async fn test_file_to_dir_transition_overwrites_blob() {
        // Prev tree: `foo` is a file (Blob). Then a path appears *under* it
        // (`foo/bar.md`), i.e. `foo` is now a directory. Upsert must replace
        // the stale Blob entry with a Tree and place the child inside it.
        let (_d, store) = make_store();
        let blob_foo = oid_hex(b"1111111111111111111111111111111111111111");
        let blob_bar = oid_hex(b"2222222222222222222222222222222222222222");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "foo", blob_foo).await.unwrap();
        let first_oid = editor.write(&store, "acc").await.unwrap();

        let mut editor2 = TreeEditor::from_tree(&store, "acc", first_oid).await.unwrap();
        editor2
            .upsert(&store, "acc", "foo/bar.md", blob_bar)
            .await
            .unwrap();
        let second_oid = editor2.write(&store, "acc").await.unwrap();

        let root = load_tree(&store, "acc", &second_oid).await.unwrap();
        assert_eq!(root.entries.len(), 1);
        let foo_entry = &root.entries[0];
        assert_eq!(foo_entry.filename, "foo");
        assert!(foo_entry.mode.is_tree(), "foo must now be a directory");

        let foo_tree = load_tree(&store, "acc", &foo_entry.oid).await.unwrap();
        assert_eq!(foo_tree.entries.len(), 1);
        assert_eq!(foo_tree.entries[0].filename, "bar.md");
        assert_eq!(foo_tree.entries[0].oid, blob_bar);
    }

    #[tokio::test]
    async fn test_remove_under_file_is_noop() {
        // Prev tree: `foo` is a file. Removing `foo/bar.md` (treating `foo` as
        // a dir) must be a silent no-op — not an error — and leave `foo` intact.
        let (_d, store) = make_store();
        let blob_foo = oid_hex(b"1111111111111111111111111111111111111111");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "foo", blob_foo).await.unwrap();
        let first_oid = editor.write(&store, "acc").await.unwrap();

        let mut editor2 = TreeEditor::from_tree(&store, "acc", first_oid).await.unwrap();
        editor2.remove(&store, "acc", "foo/bar.md").await.unwrap();
        let second_oid = editor2.write(&store, "acc").await.unwrap();

        let root = load_tree(&store, "acc", &second_oid).await.unwrap();
        assert_eq!(root.entries.len(), 1);
        assert_eq!(root.entries[0].filename, "foo");
        assert!(root.entries[0].mode == EntryKind::Blob.into());
        assert_eq!(root.entries[0].oid, blob_foo);
    }

    // --- flatten ---

    #[tokio::test]
    async fn test_flatten_empty_tree() {
        let (_d, store) = make_store();
        let empty_tree = Tree { entries: Vec::new() };
        let oid = write_object(&store, "acc", gix_object::Kind::Tree, &serialize_tree(&empty_tree))
            .await
            .unwrap();
        let result = flatten(&store, "acc", oid, &None).await.unwrap();
        assert!(result.is_empty());
    }

    #[tokio::test]
    async fn test_flatten_nested_tree() {
        let (_d, store) = make_store();
        let oid1 = oid_hex(b"1111111111111111111111111111111111111111");
        let oid2 = oid_hex(b"2222222222222222222222222222222222222222");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "README.md", oid1).await.unwrap();
        editor.upsert(&store, "acc", "resources/docs/a.md", oid2).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let result = flatten(&store, "acc", root_oid, &None).await.unwrap();
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].0, "README.md");
        assert_eq!(result[0].1, oid1);
        assert_eq!(result[1].0, "resources/docs/a.md");
        assert_eq!(result[1].1, oid2);
    }

    #[tokio::test]
    async fn test_flatten_with_path_filter() {
        let (_d, store) = make_store();
        let oid1 = oid_hex(b"1111111111111111111111111111111111111111");
        let oid2 = oid_hex(b"2222222222222222222222222222222222222222");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "a.md", oid1).await.unwrap();
        editor.upsert(&store, "acc", "resources/b.md", oid2).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let filter = Some(vec!["resources".to_string()]);
        let result = flatten(&store, "acc", root_oid, &filter).await.unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "resources/b.md");
        assert_eq!(result[0].1, oid2);
    }

    // --- lookup ---

    #[tokio::test]
    async fn test_lookup_blob_in_root() {
        let (_d, store) = make_store();
        let blob_oid = oid_hex(b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "README.md", blob_oid).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let (found, mode) = lookup(&store, "acc", root_oid, "README.md")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(found, blob_oid);
        assert!(mode.is_blob());
    }

    #[tokio::test]
    async fn test_lookup_nested_blob() {
        let (_d, store) = make_store();
        let blob_oid = oid_hex(b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "resources/a.md", blob_oid).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let (found, _) = lookup(&store, "acc", root_oid, "resources/a.md")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(found, blob_oid);
    }

    #[tokio::test]
    async fn test_lookup_not_found() {
        let (_d, store) = make_store();
        let empty_tree = Tree { entries: Vec::new() };
        let root_oid = write_object(&store, "acc", gix_object::Kind::Tree, &serialize_tree(&empty_tree))
            .await
            .unwrap();
        let result = lookup(&store, "acc", root_oid, "nonexistent.md").await.unwrap();
        assert!(result.is_none());
    }

    #[tokio::test]
    async fn test_lookup_tree_entry() {
        let (_d, store) = make_store();
        let blob_oid = oid_hex(b"cccccccccccccccccccccccccccccccccccccccc");
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "resources/a.md", blob_oid).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        let (found, mode) = lookup(&store, "acc", root_oid, "resources")
            .await
            .unwrap()
            .unwrap();
        assert!(mode.is_tree());
        // Verify by loading and confirming it has 1 entry
        let subtree = load_tree(&store, "acc", &found).await.unwrap();
        assert_eq!(subtree.entries.len(), 1);
        assert_eq!(subtree.entries[0].filename, "a.md");
    }

    // --- Sort order ---

    #[test]
    fn test_git_sort_order_preserved() {
        // Git sorts trees as if their name had a trailing '/'.
        // So blob "foo.c" comes before tree "foo" (which sorts as "foo/").
        let oid = dummy_oid();
        let mut entries = BTreeMap::new();
        entries.insert(
            "foo.c".into(),
            tree::Entry {
                mode: EntryKind::Blob.into(),
                filename: "foo.c".into(),
                oid,
            },
        );
        entries.insert(
            "foo".into(),
            tree::Entry {
                mode: EntryKind::Tree.into(),
                filename: "foo".into(),
                oid,
            },
        );

        let git_tree = entries_to_tree(&entries);
        assert_eq!(git_tree.entries[0].filename, "foo.c");
        assert_eq!(git_tree.entries[1].filename, "foo");
        assert!(git_tree.entries[0].mode.is_blob());
        assert!(git_tree.entries[1].mode.is_tree());
    }

    // --- Upsert subtree ---

    #[tokio::test]
    async fn test_upsert_subtree_root_level() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let tree_oid = ObjectId::empty_tree(gix_hash::Kind::Sha1);

        editor.upsert_subtree(&store, "acc", "subdir", tree_oid).await.unwrap();

        assert_eq!(editor.root.len(), 1);
        let entry = editor.root.get("subdir".as_bytes().as_bstr()).unwrap();
        assert!(entry.mode.is_tree());
        assert_eq!(entry.oid, tree_oid);

        // write() should reuse the OID directly (no recursion into self.subtrees)
        let root_oid = editor.write(&store, "acc").await.unwrap();
        let root = load_tree(&store, "acc", &root_oid).await.unwrap();
        assert_eq!(root.entries.len(), 1);
        assert_eq!(root.entries[0].filename, "subdir");
        assert_eq!(root.entries[0].oid, tree_oid);
    }

    #[tokio::test]
    async fn test_upsert_subtree_nested() {
        let (_d, store) = make_store();
        let mut editor = TreeEditor::empty();
        let tree_oid = ObjectId::empty_tree(gix_hash::Kind::Sha1);

        editor.upsert_subtree(&store, "acc", "a/b/c", tree_oid).await.unwrap();

        assert_eq!(editor.root.len(), 1);
        assert!(editor.root.get("a".as_bytes().as_bstr()).unwrap().mode.is_tree());
        assert!(editor.root.get("a".as_bytes().as_bstr()).unwrap().oid.is_null());

        let a_sub = editor.subtrees.get("a".as_bytes().as_bstr()).unwrap();
        assert_eq!(a_sub.len(), 1);
        assert!(a_sub.get("b".as_bytes().as_bstr()).unwrap().mode.is_tree());

        let ab_sub = editor.subtrees.get("a/b".as_bytes().as_bstr()).unwrap();
        assert_eq!(ab_sub.len(), 1);
        assert!(ab_sub.get("c".as_bytes().as_bstr()).unwrap().mode.is_tree());
        assert_eq!(ab_sub.get("c".as_bytes().as_bstr()).unwrap().oid, tree_oid);

        // No in-memory state for "a/b/c" — written directly.
        assert!(editor.subtrees.get("a/b/c".as_bytes().as_bstr()).is_none());

        let root_oid = editor.write(&store, "acc").await.unwrap();
        let root = load_tree(&store, "acc", &root_oid).await.unwrap();
        assert_eq!(root.entries.len(), 1);
        let a_oid = root.entries[0].oid;
        let a_tree = load_tree(&store, "acc", &a_oid).await.unwrap();
        assert_eq!(a_tree.entries.len(), 1);
        assert_eq!(a_tree.entries[0].filename, "b");
    }

    #[tokio::test]
    async fn test_upsert_subtree_clears_existing_state() {
        let (_d, store) = make_store();
        let oid1 = oid_hex(b"1111111111111111111111111111111111111111");
        let oid2 = oid_hex(b"2222222222222222222222222222222222222222");

        // Build editor with a/b/x.txt and a/b/y.txt
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "a/b/x.txt", oid1).await.unwrap();
        editor.upsert(&store, "acc", "a/b/y.txt", oid2).await.unwrap();
        assert!(editor.subtrees.contains_key("a/b".as_bytes().as_bstr()));

        // Replace a/b with an empty subtree
        let empty_tree = ObjectId::empty_tree(gix_hash::Kind::Sha1);
        editor.upsert_subtree(&store, "acc", "a/b", empty_tree).await.unwrap();

        // Stale "a/b" subtree should be gone
        assert!(editor.subtrees.get("a/b".as_bytes().as_bstr()).is_none());

        let root_oid = editor.write(&store, "acc").await.unwrap();
        let root = load_tree(&store, "acc", &root_oid).await.unwrap();
        let b_entry = root.entries.iter().find(|e| e.filename == "a").unwrap();
        let a_tree = load_tree(&store, "acc", &b_entry.oid).await.unwrap();
        assert_eq!(a_tree.entries.len(), 1);
        assert_eq!(a_tree.entries[0].filename, "b");
        assert_eq!(a_tree.entries[0].oid, empty_tree);
    }

    #[tokio::test]
    async fn test_upsert_subtree_then_upsert_inside() {
        let (_d, store) = make_store();
        let oid = oid_hex(b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");

        let mut editor = TreeEditor::empty();
        editor.upsert_subtree(&store, "acc", "a/b", ObjectId::empty_tree(gix_hash::Kind::Sha1)).await.unwrap();

        // Upsert inside the spliced subtree creates new in-memory state from scratch.
        editor.upsert(&store, "acc", "a/b/c.txt", oid).await.unwrap();

        let root_oid = editor.write(&store, "acc").await.unwrap();
        let root = load_tree(&store, "acc", &root_oid).await.unwrap();
        let a_entry = root.entries.iter().find(|e| e.filename == "a").unwrap();
        let a_tree = load_tree(&store, "acc", &a_entry.oid).await.unwrap();
        let b_entry = a_tree.entries.iter().find(|e| e.filename == "b").unwrap();
        let b_tree = load_tree(&store, "acc", &b_entry.oid).await.unwrap();
        assert_eq!(b_tree.entries.len(), 1);
        assert_eq!(b_tree.entries[0].filename, "c.txt");
        assert_eq!(b_tree.entries[0].oid, oid);
    }

    // --- Integration ---

    #[tokio::test]
    async fn test_commit_flow_round_trip() {
        let (_d, store) = make_store();
        let oid_readme = oid_hex(b"1111111111111111111111111111111111111111");
        let oid_doc = oid_hex(b"2222222222222222222222222222222222222222");
        let oid_skill = oid_hex(b"3333333333333333333333333333333333333333");

        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "README.md", oid_readme).await.unwrap();
        editor.upsert(&store, "acc", "resources/docs/a.md", oid_doc).await.unwrap();
        editor.upsert(&store, "acc", "agent/skills/b.py", oid_skill).await.unwrap();
        let commit1_oid = editor.write(&store, "acc").await.unwrap();

        let all_files = flatten(&store, "acc", commit1_oid, &None).await.unwrap();
        assert_eq!(all_files.len(), 3);

        assert_eq!(
            lookup(&store, "acc", commit1_oid, "README.md").await.unwrap().unwrap().0,
            oid_readme
        );
        assert_eq!(
            lookup(&store, "acc", commit1_oid, "resources/docs/a.md").await.unwrap().unwrap().0,
            oid_doc
        );
        assert_eq!(
            lookup(&store, "acc", commit1_oid, "agent/skills/b.py").await.unwrap().unwrap().0,
            oid_skill
        );

        let resources_only = flatten(
            &store,
            "acc",
            commit1_oid,
            &Some(vec!["resources".to_string()]),
        )
        .await
        .unwrap();
        assert_eq!(resources_only.len(), 1);
        assert_eq!(resources_only[0].0, "resources/docs/a.md");

        let mut editor2 = TreeEditor::from_tree(&store, "acc", commit1_oid).await.unwrap();
        let oid_new = oid_hex(b"4444444444444444444444444444444444444444");
        editor2.upsert(&store, "acc", "resources/docs/a.md", oid_new).await.unwrap();
        editor2.remove(&store, "acc", "agent/skills/b.py").await.unwrap();
        editor2.upsert(&store, "acc", "agent/skills/c.py", oid_new).await.unwrap();
        let commit2_oid = editor2.write(&store, "acc").await.unwrap();

        let all_files2 = flatten(&store, "acc", commit2_oid, &None).await.unwrap();
        assert_eq!(all_files2.len(), 3);

        assert_eq!(
            lookup(&store, "acc", commit2_oid, "resources/docs/a.md").await.unwrap().unwrap().0,
            oid_new
        );
        assert!(lookup(&store, "acc", commit2_oid, "agent/skills/b.py").await.unwrap().is_none());
        assert_eq!(
            lookup(&store, "acc", commit2_oid, "agent/skills/c.py").await.unwrap().unwrap().0,
            oid_new
        );

        // Original tree unchanged
        assert_eq!(
            lookup(&store, "acc", commit1_oid, "resources/docs/a.md").await.unwrap().unwrap().0,
            oid_doc
        );
        assert_eq!(
            lookup(&store, "acc", commit1_oid, "agent/skills/b.py").await.unwrap().unwrap().0,
            oid_skill
        );
    }

    // --- Fast Path 2 ---

    /// ObjectStore wrapper recording every `get`/`put` oid, used to prove the
    /// lazy-loading commit path never touches untouched subtrees.
    struct SpyObjectStore {
        inner: crate::git::backends::local::LocalObjectStore,
        gets: std::sync::Mutex<Vec<ObjectId>>,
        puts: std::sync::Mutex<Vec<ObjectId>>,
    }

    impl SpyObjectStore {
        fn new(inner: crate::git::backends::local::LocalObjectStore) -> Self {
            Self {
                inner,
                gets: std::sync::Mutex::new(Vec::new()),
                puts: std::sync::Mutex::new(Vec::new()),
            }
        }
        fn reset(&self) {
            self.gets.lock().unwrap().clear();
            self.puts.lock().unwrap().clear();
        }
        fn was_read(&self, oid: &ObjectId) -> bool {
            self.gets.lock().unwrap().iter().any(|o| o == oid)
        }
    }

    #[async_trait::async_trait]
    impl ObjectStore for SpyObjectStore {
        async fn put(
            &self,
            account: &str,
            oid: &ObjectId,
            zlib_body: bytes::Bytes,
        ) -> Result<(), crate::git::error::ObjectStoreError> {
            self.puts.lock().unwrap().push(*oid);
            self.inner.put(account, oid, zlib_body).await
        }
        async fn get(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> Result<bytes::Bytes, crate::git::error::ObjectStoreError> {
            self.gets.lock().unwrap().push(*oid);
            self.inner.get(account, oid).await
        }
        async fn exists(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> Result<bool, crate::git::error::ObjectStoreError> {
            self.inner.exists(account, oid).await
        }
    }

    #[tokio::test]
    async fn test_fast_path_2_untouched_subtree_not_read() {
        let dir = tempfile::tempdir().unwrap();
        let store = SpyObjectStore::new(
            crate::git::backends::local::LocalObjectStore::new(dir.path()),
        );
        let oid_a = oid_hex(b"1111111111111111111111111111111111111111");
        let oid_b = oid_hex(b"2222222222222222222222222222222222222222");

        // Build a root with two subtrees: resources/ and agent/.
        let mut editor = TreeEditor::empty();
        editor.upsert(&store, "acc", "resources/a.md", oid_a).await.unwrap();
        editor.upsert(&store, "acc", "agent/b.py", oid_b).await.unwrap();
        let root_oid = editor.write(&store, "acc").await.unwrap();

        // Record the untouched subtree's oid (agent/).
        let root = load_tree(&store, "acc", &root_oid).await.unwrap();
        let agent_oid = root.entries.iter().find(|e| e.filename == "agent").unwrap().oid;

        // Reset spy, then edit only inside resources/.
        store.reset();
        let mut editor2 = TreeEditor::from_tree(&store, "acc", root_oid).await.unwrap();
        let oid_c = oid_hex(b"3333333333333333333333333333333333333333");
        editor2.upsert(&store, "acc", "resources/c.md", oid_c).await.unwrap();
        let new_root_oid = editor2.write(&store, "acc").await.unwrap();

        // Fast Path 2: the untouched agent/ subtree was never read...
        assert!(!store.was_read(&agent_oid), "untouched subtree should not be read");

        // ...and the new root reuses its OID as-is (not rewritten).
        let new_root = load_tree(&store, "acc", &new_root_oid).await.unwrap();
        let new_agent_oid = new_root.entries.iter().find(|e| e.filename == "agent").unwrap().oid;
        assert_eq!(new_agent_oid, agent_oid, "untouched subtree OID should be reused");
    }

    #[tokio::test]
    async fn test_upsert_subtree_preserves_siblings() {
        let (_d, store) = make_store();
        let oid_keep = oid_hex(b"1111111111111111111111111111111111111111");
        let oid_old = oid_hex(b"2222222222222222222222222222222222222222");
        let oid_new = oid_hex(b"3333333333333333333333333333333333333333");

        // Base tree: proj/keep.txt and proj/sub/old.txt.
        let mut base = TreeEditor::empty();
        base.upsert(&store, "acc", "proj/keep.txt", oid_keep).await.unwrap();
        base.upsert(&store, "acc", "proj/sub/old.txt", oid_old).await.unwrap();
        let root_oid = base.write(&store, "acc").await.unwrap();

        // Replacement subtree containing new.txt.
        let mut repl = TreeEditor::empty();
        repl.upsert(&store, "acc", "new.txt", oid_new).await.unwrap();
        let repl_oid = repl.write(&store, "acc").await.unwrap();

        // Splice proj/sub with the replacement subtree.
        let mut editor = TreeEditor::from_tree(&store, "acc", root_oid).await.unwrap();
        editor.upsert_subtree(&store, "acc", "proj/sub", repl_oid).await.unwrap();
        let new_root_oid = editor.write(&store, "acc").await.unwrap();

        // Sibling proj/keep.txt is preserved.
        let keep = lookup(&store, "acc", new_root_oid, "proj/keep.txt").await.unwrap();
        assert_eq!(keep.unwrap().0, oid_keep);

        // proj/sub now points at the replacement subtree (containing new.txt).
        let (sub_oid, mode) = lookup(&store, "acc", new_root_oid, "proj/sub").await.unwrap().unwrap();
        assert!(mode.is_tree());
        assert_eq!(sub_oid, repl_oid);
        let new_file = lookup(&store, "acc", new_root_oid, "proj/sub/new.txt").await.unwrap();
        assert_eq!(new_file.unwrap().0, oid_new);
        // Old file is gone.
        assert!(lookup(&store, "acc", new_root_oid, "proj/sub/old.txt").await.unwrap().is_none());
    }
}
