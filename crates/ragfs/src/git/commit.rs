//! Git commit object construction.

use gix_hash::ObjectId;
use gix_object::{bstr::BString, Commit, WriteTo};

use crate::git::error::ObjectStoreError;
use crate::git::object_store::ObjectStore;
use crate::git::util::write_object;

/// Build a `gix_object::Commit` and write it via the existing `write_object`
/// helper. Uses `gix_date::Time::now_local_or_utc()` for both author and
/// committer timestamps. Returns the new commit's ObjectId.
pub async fn write_commit(
    store: &dyn ObjectStore,
    account: &str,
    tree: ObjectId,
    parents: Vec<ObjectId>,
    author_name: &str,
    author_email: &str,
    message: &str,
) -> Result<ObjectId, ObjectStoreError> {
    let now = gix_date::Time::now_local_or_utc();
    let actor = gix_actor::Signature {
        name: author_name.into(),
        email: author_email.into(),
        time: now,
    };
    let commit = Commit {
        tree,
        parents: parents.into(),
        author: actor.clone(),
        committer: actor,
        encoding: None,
        message: BString::from(message),
        extra_headers: Vec::new(),
    };
    let mut buf = Vec::with_capacity(256);
    commit
        .write_to(&mut buf)
        .map_err(|e| ObjectStoreError::Backend(format!("commit encode: {e}")))?;
    write_object(store, account, gix_object::Kind::Commit, &buf).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_write_commit_round_trip_no_parent() {
        use tempfile::tempdir;
        use crate::git::backends::local::LocalObjectStore;
        use crate::git::util::{read_object, parse_object_header};
        use gix_object::bstr::ByteSlice;

        let dir = tempdir().unwrap();
        let store = LocalObjectStore::new(dir.path());

        let tree = gix_hash::ObjectId::empty_tree(gix_hash::Kind::Sha1);
        let oid = write_commit(
            &store, "acct",
            tree,
            vec![],                          // root commit, no parents
            "alice", "alice@example.com",
            "init",
        ).await.unwrap();

        let raw = read_object(&store, "acct", &oid).await.unwrap();
        let (kind, _, hdr) = parse_object_header(&raw).unwrap();
        assert_eq!(kind, gix_object::Kind::Commit);
        let parsed = gix_object::CommitRef::from_bytes(&raw[hdr..]).unwrap();
        assert_eq!(parsed.tree(), tree);
        assert_eq!(parsed.parents().count(), 0);
        assert_eq!(parsed.message, b"init".as_bstr());
        assert_eq!(parsed.author.name, b"alice".as_bstr());
        assert_eq!(parsed.author.email, b"alice@example.com".as_bstr());
    }

    #[tokio::test]
    async fn test_write_commit_with_parent() {
        use tempfile::tempdir;
        use crate::git::backends::local::LocalObjectStore;
        use crate::git::util::{read_object, parse_object_header};

        let dir = tempdir().unwrap();
        let store = LocalObjectStore::new(dir.path());

        let tree = gix_hash::ObjectId::empty_tree(gix_hash::Kind::Sha1);
        let parent = gix_hash::ObjectId::from_hex(
            b"1234567890abcdef1234567890abcdef12345678"
        ).unwrap();

        let oid = write_commit(
            &store, "acct",
            tree,
            vec![parent],
            "bob", "bob@example.com",
            "child commit",
        ).await.unwrap();

        let raw = read_object(&store, "acct", &oid).await.unwrap();
        let (_, _, hdr) = parse_object_header(&raw).unwrap();
        let parsed = gix_object::CommitRef::from_bytes(&raw[hdr..]).unwrap();
        let parents: Vec<_> = parsed.parents().collect();
        assert_eq!(parents.len(), 1);
        assert_eq!(parents[0], parent);
    }
}
