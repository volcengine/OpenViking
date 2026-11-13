    // ── 10: show ───────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_commit_meta_by_oid() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let oid = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: oid.to_hex().to_string(),
                path: None,
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Commit {
                oid: returned,
                parents,
                message,
                author,
                committer,
                tree,
            } => {
                assert_eq!(returned, oid);
                assert!(parents.is_empty(), "root commit");
                assert_eq!(message, "first");
                assert_eq!(author.name, "tester");
                assert_eq!(author.email, "tester@example.com");
                assert_eq!(committer.name, "tester");
                assert_ne!(tree, ObjectId::empty_tree(gix_hash::Kind::Sha1));
            }
            other => panic!("expected Commit, got {other:?}"),
        }
    }

    // ── 10 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_resolves_branch_name_and_full_ref() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let oid = make_commit(&svc, "acct", "main", "first").await;

        for tref in ["main", "refs/heads/main"] {
            let resp = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: tref.into(),
                    path: None,
                })
                .await
                .unwrap();
            match resp {
                ShowResponse::Commit { oid: returned, .. } => assert_eq!(returned, oid),
                other => panic!("{tref}: expected Commit, got {other:?}"),
            }
        }
    }

    // ── 10b: abbreviated OID resolution ────────────────────────────────
    #[tokio::test]
    async fn test_show_resolves_short_oid_unique() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let oid = make_commit(&svc, "acct", "main", "first").await;
        let full = oid.to_hex().to_string();

        for len in [4usize, 7, 12, 39] {
            let short = &full[..len];
            let resp = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: short.into(),
                    path: None,
                })
                .await
                .unwrap_or_else(|e| {
                    panic!("short oid {short} (len {len}) should resolve, got {e}")
                });
            match resp {
                ShowResponse::Commit { oid: returned, .. } => assert_eq!(returned, oid),
                other => panic!("len {len}: expected Commit, got {other:?}"),
            }
        }
    }

    #[tokio::test]
    async fn test_show_short_oid_not_found_distinguished_from_branch() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        // A 4-hex string that almost-certainly does not match any commit.
        // (SHA-1 collision against a single commit is astronomically unlikely
        // for "ffff" — the first commit's hex is deterministic given the
        // test's actor/time-zero, so this is a stable miss.)
        let bogus = "ffff";
        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: bogus.into(),
                path: None,
            })
            .await
            .unwrap_err();
        assert!(
            matches!(err, GitError::OidPrefixNotFound { ref prefix } if prefix == bogus),
            "expected OidPrefixNotFound({bogus}), got {err:?}",
        );
    }

    #[tokio::test]
    async fn test_short_oid_three_chars_falls_through_to_ref_lookup() {
        // 3 hex chars is below the 4-char floor for abbreviated OID; it
        // should be treated as a branch name (which doesn't exist), giving
        // a RefStore::NotFound error — NOT OidPrefixNotFound.
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "abc".into(),
                path: None,
            })
            .await
            .unwrap_err();
        assert!(
            matches!(err, GitError::RefStore(RefStoreError::NotFound(_))),
            "expected RefStore::NotFound for 3-char input, got {err:?}",
        );
    }

    // ── 11 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_blob_round_trip() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        let body = b"hello world\n";
        vfs.put("resources/a.md", body);
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/a.md".into()),
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Blob {
                bytes,
                size,
                oid: _,
            } => {
                assert_eq!(bytes.as_ref(), body);
                assert_eq!(size, body.len() as u64);
            }
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_show_blob_limit_rejects_before_returning_payload() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"payload exceeds limit");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show_with_limit(
                ShowRequest {
                    account: "acct".into(),
                    target_ref: "main".into(),
                    path: Some("resources/a.md".into()),
                },
                Some(4),
            )
            .await
            .unwrap_err();

        assert!(matches!(err, GitError::BlobTooLarge { size: 21, limit: 4 }));
    }

    #[tokio::test]
    async fn test_show_blob_limit_allows_payload_at_limit() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"payload");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let response = svc
            .show_with_limit(
                ShowRequest {
                    account: "acct".into(),
                    target_ref: "main".into(),
                    path: Some("resources/a.md".into()),
                },
                Some(7),
            )
            .await
            .unwrap();

        assert!(matches!(
            response,
            ShowResponse::Blob {
                bytes,
                size: 7,
                ..
            } if bytes.as_ref() == b"payload"
        ));
    }

    // ── 12 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_blob_path_not_found() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/missing.md".into()),
            })
            .await
            .unwrap_err();

        match err {
            GitError::PathNotFound(p) => assert_eq!(p, "resources/missing.md"),
            other => panic!("expected PathNotFound, got {other:?}"),
        }
    }

    // ── 13 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_blob_rejects_directory_path() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources".into()),
            })
            .await
            .unwrap_err();

        match err {
            GitError::PathIsDirectory(p) => assert_eq!(p, "resources"),
            other => panic!("expected PathIsDirectory, got {other:?}"),
        }
    }

    // ── 14 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_unknown_ref() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "nonexistent".into(),
                path: None,
            })
            .await
            .unwrap_err();

        match err {
            GitError::RefStore(RefStoreError::NotFound(name)) => {
                assert_eq!(name, "refs/heads/nonexistent");
            }
            other => panic!("expected RefStore NotFound, got {other:?}"),
        }
    }

    // ── 15 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_malformed_oid_input() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "z".repeat(40),
                path: None,
            })
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::Other(_) | GitError::RefStore(_)));
    }

    // ── 16 ─────────────────────────────────────────────────────────────
    /// Blob bytes survive a round-trip even when they contain NUL bytes,
    /// non-UTF-8 sequences, and multiple newlines. Guards against any
    /// future "treat blobs as strings" regression.
    #[tokio::test]
    async fn test_show_blob_binary_and_multiline() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        // NUL, invalid UTF-8 (0xC3 0x28 is an invalid 2-byte sequence), CRLF, LF.
        let body: Vec<u8> = vec![
            b'h', b'i', 0x00, 0xC3, 0x28, b'\r', b'\n', b'l', b'i', b'n', b'e', b'2', b'\n', 0xFF,
            0xFE, 0xFD,
        ];
        vfs.put("resources/bin.dat", &body);
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/bin.dat".into()),
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Blob { bytes, size, .. } => {
                assert_eq!(bytes, body);
                assert_eq!(size as usize, body.len());
            }
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    // ── 17 ─────────────────────────────────────────────────────────────
    /// Construct a commit whose author and committer differ, write it
    /// directly via `util::write_object`, point a ref at it, and verify
    /// `show()` decodes the two signatures into the two Actor fields
    /// without crossing them. Bypasses `commit()` because the public
    /// `CommitRequest` API only accepts one author (used for both).
    #[tokio::test]
    async fn test_show_distinguishes_committer_from_author() {
        use gix_object::{bstr::BString, Commit, WriteTo};

        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        // First, create a normal commit just to get a real tree OID.
        let seed_oid = make_commit(&svc, "acct", "main", "seed").await;
        let seed_tree =
            load_commit_meta(object_store.as_ref() as &dyn ObjectStore, "acct", &seed_oid)
                .await
                .unwrap()
                .tree;

        // Build a commit with deliberately mismatched author/committer.
        let author = gix_actor::Signature {
            name: "Alice Author".into(),
            email: "alice@example.com".into(),
            time: gix_date::Time {
                seconds: 1_700_000_000,
                offset: 3600,
                sign: gix_date::time::Sign::Plus,
            },
        };
        let committer = gix_actor::Signature {
            name: "Carol Committer".into(),
            email: "carol@example.com".into(),
            time: gix_date::Time {
                seconds: 1_700_000_100,
                offset: -7200,
                sign: gix_date::time::Sign::Minus,
            },
        };
        let commit = Commit {
            tree: seed_tree,
            parents: Vec::new().into(),
            author,
            committer,
            encoding: None,
            message: BString::from("split-actors"),
            extra_headers: Vec::new(),
        };
        let mut buf = Vec::new();
        commit.write_to(&mut buf).unwrap();
        let oid = crate::git::util::write_object(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            gix_object::Kind::Commit,
            &buf,
        )
        .await
        .unwrap();

        // Point a fresh branch at it so show() can find it by name.
        ref_store
            .cas_update("acct", "refs/heads/split", None, oid)
            .await
            .unwrap();

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "split".into(),
                path: None,
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Commit {
                author, committer, ..
            } => {
                assert_eq!(author.name, "Alice Author");
                assert_eq!(author.email, "alice@example.com");
                assert_eq!(author.time_seconds, 1_700_000_000);
                assert_eq!(author.tz_offset_seconds, 3600);
                assert_eq!(committer.name, "Carol Committer");
                assert_eq!(committer.email, "carol@example.com");
                assert_eq!(committer.time_seconds, 1_700_000_100);
                assert_eq!(committer.tz_offset_seconds, -7200);
            }
            other => panic!("expected Commit, got {other:?}"),
        }
    }

    // ── 18 ─────────────────────────────────────────────────────────────
    /// When an intermediate path component is a blob (not a tree),
    /// `tree_builder::lookup` returns `Ok(None)`, which `show()` maps
    /// to `PathNotFound`. Pin this so a future change can't silently
    /// reinterpret it as `PathIsDirectory` or `CorruptedObject`.
    #[tokio::test]
    async fn test_show_intermediate_path_component_is_blob() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/a.md/oops".into()),
            })
            .await
            .unwrap_err();

        match err {
            GitError::PathNotFound(p) => assert_eq!(p, "resources/a.md/oops"),
            other => panic!("expected PathNotFound, got {other:?}"),
        }
    }

    // ── 19 ─────────────────────────────────────────────────────────────
    /// `show()` validates the `path` argument up front. Empty string, a
    /// leading or trailing `/`, and embedded `//` all fail
    /// `validate_relative_path` before any tree lookup runs — callers see
    /// `InvalidPath` rather than mixed `Other` / `PathNotFound` results.
    /// This pins the contract guarding the native binding boundary against
    /// traversal-style input (`..`, `/abs`, …) being silently accepted.
    #[tokio::test]
    async fn test_show_path_with_invalid_form() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        for bad in ["", "/x", "x/", "a//b"] {
            let err = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: "main".into(),
                    path: Some(bad.into()),
                })
                .await
                .unwrap_err();
            assert!(
                matches!(err, GitError::InvalidPath(_)),
                "path {bad:?}: expected InvalidPath, got {err:?}",
            );
        }
    }

    // ── 20 ─────────────────────────────────────────────────────────────
    /// If the commit's loose object file is removed from the store
    /// after the ref still points at it, `show()` must surface
    /// `ObjectStoreError::NotFound` (wrapped in `GitError::ObjectStore`).
    /// Guards against any future "swallow missing objects" regression
    /// inside `load_commit_meta`.
    #[tokio::test]
    async fn test_show_commit_object_missing_from_store() {
        let (dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let oid = make_commit(&svc, "acct", "main", "first").await;

        // LocalObjectStore layout: {base_dir}/{account}/objects/{aa}/{bb...}
        let hex = oid.to_hex().to_string();
        let path = dir
            .path()
            .join("acct")
            .join("objects")
            .join(&hex[..2])
            .join(&hex[2..]);
        std::fs::remove_file(&path).expect("loose commit object must exist before removal");

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: None,
            })
            .await
            .unwrap_err();

        match err {
            GitError::ObjectStore(ObjectStoreError::NotFound(missing)) => {
                assert_eq!(missing, oid);
            }
            other => panic!("expected ObjectStore(NotFound), got {other:?}"),
        }
    }
