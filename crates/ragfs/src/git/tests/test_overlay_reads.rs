#[tokio::test]
async fn tombstoned_commit_is_hidden_by_all_read_paths() {
    let (_dir, vfs, _, _, svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 3).await;
    let deleted = ids[1];
    let hex = deleted.to_string();
    svc.delete_commits(deletion(&[deleted], false))
        .await
        .unwrap();

    let show = |target_ref: String| ShowRequest {
        account: "acct".into(),
        target_ref,
        path: None,
    };
    assert!(matches!(
        svc.show(show(hex[..16].into())).await,
        Err(GitError::OidPrefixNotFound { .. })
    ));
    assert!(
        matches!(svc.show(show(hex.clone())).await,Err(GitError::CommitNotFound(oid)) if oid==deleted)
    );
    assert!(matches!(
        svc.path_is_file("acct", &deleted, "resources/file").await,
        Err(GitError::CommitNotFound(_))
    ));
    assert!(matches!(
        svc.diff_paths("acct", Some(&hex), "main", &["resources".into()])
            .await,
        Err(GitError::CommitNotFound(_))
    ));
    assert!(matches!(
        svc.restore(RestoreRequest {
            account: "acct".into(),
            branch: "main".into(),
            project_dir: None,
            source_commit: hex,
            allow_missing_source: false,
            dry_run: true,
            message: None,
            author_name: "tester".into(),
            author_email: "tester@example.com".into()
        })
        .await,
        Err(GitError::CommitNotFound(_))
    ));
    assert!(
        matches!(svc.show(show(ids[2].to_string())).await.unwrap(),ShowResponse::Commit{oid,parents,..} if oid==ids[2] && parents==[ids[0]])
    );
    assert!(
        matches!(svc.show(ShowRequest{account:"acct".into(),target_ref:"main".into(),path:Some("resources/file".into())}).await.unwrap(),ShowResponse::Blob{bytes,..} if bytes.as_ref()==b"2")
    );
}
