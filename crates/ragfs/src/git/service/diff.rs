use super::*;

impl GitService {
    /// List files that changed under `paths` between two commits.
    ///
    /// `base = None` compares against an empty tree, so every file is added.
    /// Each entry is `(account-relative path, "added" | "modified" | "deleted")`.
    /// A directory missing from one side counts as an empty tree there.
    pub async fn diff_paths(
        &self,
        account: &str,
        base: Option<&str>,
        head: &str,
        paths: &[String],
    ) -> Result<Vec<(String, &'static str)>, GitError> {
        validate_account_id(account)?;
        for path in paths {
            validate_relative_path(path)?;
        }
        let store = self.object_store.as_ref();
        let mut roots = Vec::new();
        for target in base.into_iter().chain([head]) {
            let oid = resolve_ref(self.ref_store.as_ref(), store, account, target).await?;
            roots.push(load_commit_meta(store, account, &oid).await?.tree);
        }
        let head_tree = roots.pop();
        let base_tree = roots.pop();
        let mut out = Vec::new();
        for dir in paths {
            let dir = dir.trim_matches('/');
            let mut sides = [None, None];
            for (side, tree) in sides.iter_mut().zip([base_tree, head_tree]) {
                if let Some(tree) = tree {
                    *side = crate::git::tree_builder::lookup(store, account, tree, dir)
                        .await?
                        .filter(|(_, mode)| mode.is_tree())
                        .map(|(oid, _)| oid);
                }
            }
            for (path, change) in
                crate::git::tree_builder::diff_trees(store, account, sides[0], sides[1]).await?
            {
                out.push((format!("{dir}/{path}"), change));
            }
        }
        Ok(out)
    }
}

/// Pure-function diff between two flattened subtrees.
///
/// Both inputs are `(path, oid)` slices as returned by `tree_builder::flatten`
/// on a subtree OID — meaning the paths are already relative to the subtree
/// root (no `project_dir` prefix). Results are sorted by path.
pub(super) fn compute_subtree_diff(
    source: &[(String, gix_hash::ObjectId)],
    head: &[(String, gix_hash::ObjectId)],
) -> crate::git::types::RestoreDiff {
    use std::collections::HashMap;
    let head_map: HashMap<&str, &gix_hash::ObjectId> =
        head.iter().map(|(p, o)| (p.as_str(), o)).collect();
    let source_map: HashMap<&str, &gix_hash::ObjectId> =
        source.iter().map(|(p, o)| (p.as_str(), o)).collect();

    let mut to_write = Vec::new();
    let mut unchanged = Vec::new();
    for (path, oid) in source {
        match head_map.get(path.as_str()) {
            Some(head_oid) if *head_oid == oid => unchanged.push(path.clone()),
            _ => to_write.push((path.clone(), *oid)),
        }
    }
    let mut to_delete: Vec<String> = head
        .iter()
        .filter(|(p, _)| !source_map.contains_key(p.as_str()))
        .map(|(p, _)| p.clone())
        .collect();

    to_write.sort_by(|a, b| a.0.cmp(&b.0));
    to_delete.sort();
    unchanged.sort();
    crate::git::types::RestoreDiff {
        to_write,
        to_delete,
        unchanged,
    }
}
