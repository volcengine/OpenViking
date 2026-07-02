//! S3FS tree construction from flat object listing.
//!
//! S3 has no native directory tree; `tree_directory` performs a single (or a
//! few paginated) flat `list_objects` call and reconstructs the full directory
//! tree in memory here. This module owns the reconstruction algorithm so that
//! `mod.rs` only keeps routing and the `FileSystem` trait implementation.

use std::collections::HashSet;
use std::time::SystemTime;

use super::client;
use crate::core::filesystem::{relative_depth, relative_match_file};
use crate::core::{FileInfo, Result, TreeEntry};

/// Extract the last path component (file/dir name) from a path.
fn s3_file_name(path: &str) -> String {
    path.rsplit('/').next().unwrap_or("").to_string()
}

/// Build an ordered list of `TreeEntry` from a flat S3 object listing.
///
/// Reconstructs synthetic directories (from directory markers and from file
/// parent prefixes), filters hidden files and entries beyond `level_limit`,
/// and orders entries by path-component lexicographic order so the output is
/// stable and consistent with the default DFS implementation.
///
/// `query_root` is the normalized query root path; `key_to_path` maps an S3
/// object key to a filesystem path (allowing prefix stripping to be injected).
pub(super) fn build_tree_entries_from_flat_listing<F>(
    query_root: &str,
    objects: &[client::ObjectMeta],
    show_hidden: bool,
    level_limit: Option<usize>,
    key_to_path: F,
) -> Result<Vec<TreeEntry>>
where
    F: Fn(&str) -> String,
{
    let mut seen_dirs: HashSet<String> = HashSet::new();
    let mut entries: Vec<(String, bool, u64, SystemTime)> = Vec::new();

    for obj in objects {
        let raw_path = key_to_path(&obj.key);
        let raw_path = if raw_path.starts_with('/') {
            raw_path
        } else {
            format!("/{}", raw_path)
        };

        if obj.is_dir_marker {
            let dir_path = raw_path.trim_end_matches('/').to_string();
            if dir_path.is_empty() || dir_path == query_root {
                continue;
            }
            if seen_dirs.insert(dir_path.clone()) {
                entries.push((dir_path, true, 0, SystemTime::now()));
            }
        } else {
            let file_path = raw_path;
            entries.push((file_path.clone(), false, obj.size as u64, obj.last_modified));

            // Recover intermediate directories *below the query root* from the
            // file's query-root-relative path. Using the relative path (instead
            // of the absolute filesystem path) guarantees that ancestors of the
            // query root are never emitted as tree entries.
            let rel = relative_match_file(query_root, &file_path);
            let rel_parts: Vec<&str> = rel.split('/').filter(|p| !p.is_empty()).collect();
            let base = if query_root == "/" { "" } else { query_root };
            for i in 1..rel_parts.len() {
                let dir_path = format!("{}/{}", base, rel_parts[..i].join("/"));
                if seen_dirs.insert(dir_path.clone()) {
                    entries.push((dir_path, true, 0, SystemTime::now()));
                }
            }
        }
    }

    let mut filtered: Vec<&(String, bool, u64, SystemTime)> = Vec::new();
    for entry in &entries {
        let (path, is_dir, _size, _mod_time) = entry;
        let name = s3_file_name(path);

        if !*is_dir && name.starts_with('.') && !show_hidden {
            continue;
        }

        if let Some(limit) = level_limit {
            let rel = relative_match_file(query_root, path);
            let depth = relative_depth(&rel);
            // S3FS uses `depth > limit` (post-generation filter) vs default
            // impl's `current_depth >= limit` (pre-expansion guard).  Both are
            // semantically equivalent: level_limit=N means entries at depth
            // <= N are included, entries at depth > N are excluded.
            if depth > limit {
                continue;
            }
        }

        filtered.push(entry);
    }

    filtered.sort_by(|(a_path, ..), (b_path, ..)| {
        let a_parts: Vec<&str> = a_path
            .trim_start_matches('/')
            .split('/')
            .filter(|p| !p.is_empty())
            .collect();
        let b_parts: Vec<&str> = b_path
            .trim_start_matches('/')
            .split('/')
            .filter(|p| !p.is_empty())
            .collect();
        a_parts.cmp(&b_parts)
    });

    let mut result = Vec::new();
    for (path, is_dir, size, mod_time) in filtered {
        let rel_path = relative_match_file(query_root, path);
        let name = s3_file_name(path);

        result.push(TreeEntry {
            path: path.clone(),
            rel_path,
            info: FileInfo {
                name,
                size: *size,
                mode: if *is_dir { 0o755 } else { 0o644 },
                mod_time: *mod_time,
                is_dir: *is_dir,
            },
            extra: std::collections::HashMap::new(),
        });
    }

    Ok(result)
}

/// Build one ordered page of `TreeEntry` values from a single S3 listing page.
///
/// Compared with `build_tree_entries_from_flat_listing`, this variant drops the
/// leading ancestor entries that were already emitted by a previous listing
/// page, so callers can stitch paginated S3 scans into one stable DFS stream.
pub(super) fn build_tree_entries_from_listing_page<F>(
    query_root: &str,
    objects: &[client::ObjectMeta],
    show_hidden: bool,
    level_limit: Option<usize>,
    prev_rel_parts: &[String],
    key_to_path: F,
) -> Result<Vec<TreeEntry>>
where
    F: Fn(&str) -> String,
{
    let mut entries =
        build_tree_entries_from_flat_listing(query_root, objects, show_hidden, level_limit, key_to_path)?;
    if prev_rel_parts.is_empty() || entries.is_empty() {
        return Ok(entries);
    }

    let mut skip = 0usize;
    while skip < entries.len() {
        let parts = rel_parts(&entries[skip].rel_path);
        if parts.len() <= prev_rel_parts.len() && prev_rel_parts[..parts.len()] == parts[..] {
            skip += 1;
            continue;
        }
        break;
    }

    if skip > 0 {
        entries.drain(..skip);
    }
    Ok(entries)
}

pub(super) fn rel_parts(rel_path: &str) -> Vec<String> {
    rel_path
        .split('/')
        .filter(|segment| !segment.is_empty() && *segment != ".")
        .map(ToString::to_string)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_meta(key: &str, size: i64, is_dir_marker: bool) -> client::ObjectMeta {
        client::ObjectMeta {
            key: key.to_string(),
            size,
            last_modified: std::time::SystemTime::now(),
            is_dir_marker,
        }
    }

    fn identity_key_to_path(key: &str) -> String {
        format!("/{}", key)
    }

    #[test]
    fn test_build_tree_entries_empty_listing() {
        let objects: Vec<client::ObjectMeta> = vec![];
        let result = build_tree_entries_from_flat_listing(
            "/root",
            &objects,
            false,
            None,
            identity_key_to_path,
        )
        .unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_build_tree_entries_dir_from_marker() {
        let objects = vec![make_meta("mydir/", 0, true)];
        let result = build_tree_entries_from_flat_listing(
            "/root",
            &objects,
            false,
            None,
            identity_key_to_path,
        )
        .unwrap();
        assert_eq!(result.len(), 1);
        let dir = &result[0];
        assert!(dir.info.is_dir);
        assert_eq!(dir.path, "/mydir");
        assert_eq!(dir.rel_path, "mydir");
        assert_eq!(dir.info.size, 0);
        assert_eq!(dir.info.mode, 0o755);
    }

    #[test]
    fn test_build_tree_entries_dir_dedup() {
        let objects = vec![
            make_meta("mydir/", 0, true),
            make_meta("mydir/file.txt", 100, false),
        ];
        let result =
            build_tree_entries_from_flat_listing("/", &objects, false, None, identity_key_to_path)
                .unwrap();
        assert_eq!(result.len(), 2);
        assert!(result[0].info.is_dir);
        assert!(result[1].info.is_dir == false);
    }

    #[test]
    fn test_build_tree_entries_dfs_order() {
        let objects = vec![
            make_meta("a/a.txt", 100, false),
            make_meta("a/b/c.txt", 200, false),
            make_meta("a/b.txt", 150, false),
        ];
        let result =
            build_tree_entries_from_flat_listing("/", &objects, false, None, identity_key_to_path)
                .unwrap();
        let paths: Vec<&str> = result.iter().map(|e| e.path.as_str()).collect();
        assert_eq!(
            paths,
            vec!["/a", "/a/a.txt", "/a/b", "/a/b/c.txt", "/a/b.txt"]
        );
    }

    #[test]
    fn test_build_tree_entries_level_limit() {
        let objects = vec![
            make_meta("a.txt", 100, false),
            make_meta("sub/b.txt", 200, false),
            make_meta("sub/deep/c.txt", 300, false),
        ];
        let result = build_tree_entries_from_flat_listing(
            "/",
            &objects,
            false,
            Some(1),
            identity_key_to_path,
        )
        .unwrap();
        let paths: Vec<&str> = result.iter().map(|e| e.path.as_str()).collect();
        assert_eq!(paths, vec!["/a.txt", "/sub"]);
    }

    #[test]
    fn test_build_tree_entries_show_hidden_false() {
        let objects = vec![
            make_meta("a.txt", 100, false),
            make_meta(".hidden", 50, false),
            make_meta("sub/b.txt", 200, false),
        ];
        let result = build_tree_entries_from_flat_listing(
            "/root",
            &objects,
            false,
            None,
            identity_key_to_path,
        )
        .unwrap();
        let names: Vec<&str> = result.iter().map(|e| e.info.name.as_str()).collect();
        assert!(names.contains(&"a.txt"));
        assert!(names.contains(&"sub"));
        assert!(names.contains(&"b.txt"));
        assert!(!names.contains(&".hidden"));
    }

    #[test]
    fn test_build_tree_entries_show_hidden_true() {
        let objects = vec![
            make_meta("a.txt", 100, false),
            make_meta(".hidden", 50, false),
        ];
        let result = build_tree_entries_from_flat_listing(
            "/root",
            &objects,
            true,
            None,
            identity_key_to_path,
        )
        .unwrap();
        let names: Vec<&str> = result.iter().map(|e| e.info.name.as_str()).collect();
        assert!(names.contains(&"a.txt"));
        assert!(names.contains(&".hidden"));
    }

    #[test]
    fn test_build_tree_entries_query_root_skipped() {
        let objects = vec![make_meta("root/file.txt", 100, false)];
        let result = build_tree_entries_from_flat_listing("/root", &objects, false, None, |key| {
            format!("/{}", key)
        })
        .unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].path, "/root/file.txt");
    }

    #[test]
    fn test_build_tree_entries_nested_dir_recovery() {
        let objects = vec![make_meta("a/b/c/d/file.txt", 100, false)];
        let result =
            build_tree_entries_from_flat_listing("/", &objects, false, None, identity_key_to_path)
                .unwrap();
        let paths: Vec<&str> = result.iter().map(|e| e.path.as_str()).collect();
        assert_eq!(
            paths,
            vec!["/a", "/a/b", "/a/b/c", "/a/b/c/d", "/a/b/c/d/file.txt"]
        );
    }

    /// Regression test: when the query root has ancestor directories, those
    /// ancestors must NOT leak into the tree result. Reproduces the bug where
    /// `ov tree viking://resources/resource_xxx` showed parent entries such as
    /// `tenant_xxx` and `tenant_xxx/resources`.
    #[test]
    fn test_build_tree_entries_no_ancestor_leak() {
        // query_root is several levels deep; listed objects live under it.
        let objects = vec![make_meta("tenant_x/resources/res_1/res_1.md", 171, false)];
        let result = build_tree_entries_from_flat_listing(
            "/tenant_x/resources/res_1",
            &objects,
            false,
            None,
            identity_key_to_path,
        )
        .unwrap();
        let paths: Vec<&str> = result.iter().map(|e| e.path.as_str()).collect();
        // Only the single file under the query root should appear; no ancestors
        // (`/tenant_x`, `/tenant_x/resources`) and not the query root itself.
        assert_eq!(paths, vec!["/tenant_x/resources/res_1/res_1.md"]);
    }

    #[test]
    fn test_build_tree_entries_page_drops_cross_page_duplicate_ancestors() {
        let first_page = vec![make_meta("a/b/first.txt", 100, false)];
        let first = build_tree_entries_from_listing_page(
            "/",
            &first_page,
            false,
            None,
            &[],
            identity_key_to_path,
        )
        .unwrap();
        let last_rel_parts = first
            .last()
            .unwrap()
            .rel_path
            .split('/')
            .map(str::to_string)
            .collect::<Vec<_>>();

        let second_page = vec![make_meta("a/b/second.txt", 100, false)];
        let second = build_tree_entries_from_listing_page(
            "/",
            &second_page,
            false,
            None,
            &last_rel_parts,
            identity_key_to_path,
        )
        .unwrap();

        let paths: Vec<&str> = second.iter().map(|e| e.path.as_str()).collect();
        assert_eq!(paths, vec!["/a/b/second.txt"]);
    }

    #[test]
    fn test_build_tree_entries_page_keeps_diverging_suffix_after_common_prefix() {
        let prev_rel_parts = vec!["a".to_string(), "b".to_string(), "first.txt".to_string()];
        let page = vec![make_meta("a/c/second.txt", 100, false)];
        let result = build_tree_entries_from_listing_page(
            "/",
            &page,
            false,
            None,
            &prev_rel_parts,
            identity_key_to_path,
        )
        .unwrap();

        let paths: Vec<&str> = result.iter().map(|e| e.path.as_str()).collect();
        assert_eq!(paths, vec!["/a/c", "/a/c/second.txt"]);
    }
}
