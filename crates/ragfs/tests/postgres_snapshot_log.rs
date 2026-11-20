//! Run against a disposable, pre-provisioned PostgreSQL database:
//! SNAPSHOT_LOG_TEST_PG_DSN='postgresql://...' cargo test --locked -p ragfs \
//!   --features postgres --test postgres_snapshot_log -- --ignored
#![cfg(feature = "postgres")]

use ragfs::git::{PostgresSnapshotLogStore, SnapshotLogError, SnapshotLogRow, SnapshotLogStore};
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use tokio::sync::Barrier;

fn dsn() -> String {
    std::env::var("SNAPSHOT_LOG_TEST_PG_DSN")
        .expect("set SNAPSHOT_LOG_TEST_PG_DSN to a disposable PostgreSQL database")
}

async fn store() -> PostgresSnapshotLogStore {
    PostgresSnapshotLogStore::connect(&dsn(), 4, 5_000)
        .await
        .expect("provision openviking/service/sql/snapshot_log_postgres_init.sql first")
}

fn oid(index: usize) -> String {
    format!("{index:040x}")
}

fn rows(index: usize, paths: &[&str]) -> Vec<SnapshotLogRow> {
    paths
        .iter()
        .map(|path| SnapshotLogRow {
            path: (*path).into(),
            commit_oid: oid(index),
        })
        .collect()
}

async fn cleanup(account: &str) {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn())
        .await
        .unwrap();
    sqlx::query("DELETE FROM snapshot_log WHERE account = $1")
        .bind(account)
        .execute(&pool)
        .await
        .unwrap();
    pool.close().await;
}

#[tokio::test]
#[ignore = "requires a real PostgreSQL database"]
async fn postgres_snapshot_log_history_scope_width_and_watermark() {
    let store = store().await;
    let account = uuid::Uuid::new_v4().simple().to_string() + &"a".repeat(223);
    let branch = "b".repeat(512);
    // Varied four-byte characters exercise the full 2048-byte path limit.
    let wide: String = (0..512)
        .map(|i| char::from_u32(0x10000 + i).unwrap())
        .collect();
    let paths = ["/", "docs", "docs/File'_%\\.md", wide.as_str()];
    store
        .append_rows_if_latest(&account, &branch, None, &rows(1, &paths))
        .await
        .unwrap();
    let first_id = store
        .load_latest_indexed_commit(&account, &branch)
        .await
        .unwrap()
        .unwrap()
        .0;
    store
        .append_rows_if_latest(&account, &branch, Some(&oid(1)), &rows(2, &paths))
        .await
        .unwrap();
    // Same account, another branch has an independent chain.
    store
        .append_rows_if_latest(&account, "other", None, &rows(3, &paths))
        .await
        .unwrap();
    let latest = store
        .load_latest_indexed_commit(&account, &branch)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(latest.1, oid(2));
    let query = vec![paths[2].to_string(), wide.clone(), paths[2].to_string()];
    let hits = store
        .query_path_commits(&account, &branch, &query, latest.0, 0, 10)
        .await
        .unwrap();
    assert_eq!(
        hits.iter()
            .map(|hit| hit.commit_oid.clone())
            .collect::<Vec<_>>(),
        vec![oid(2), oid(1)]
    );
    assert!(hits[0].last_id > hits[1].last_id);
    assert_eq!(
        store
            .query_path_commits(&account, &branch, &query, first_id, 0, 10)
            .await
            .unwrap()[0]
            .commit_oid,
        oid(1)
    );
    assert_eq!(
        store
            .query_path_commits(&account, &branch, &query, latest.0, 0, 1)
            .await
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        store
            .query_path_commits(&account, &branch, &query, latest.0, 1, 1)
            .await
            .unwrap(),
        hits[1..]
    );
    assert!(store
        .query_path_commits(&account, &branch, &query, latest.0, 2, 1)
        .await
        .unwrap()
        .is_empty());
    #[cfg(target_pointer_width = "64")]
    assert!(matches!(
        store
            .query_path_commits(&account, &branch, &query, latest.0, usize::MAX, 1)
            .await,
        Err(SnapshotLogError::Sql(sqlx::Error::Encode(_)))
    ));
    for (a, b, p) in [
        (account.to_uppercase(), branch.clone(), query.clone()),
        (account.clone(), "missing".to_string(), query.clone()),
        (
            account.clone(),
            branch.clone(),
            vec!["docs/file'_%\\.md".to_string()],
        ),
    ] {
        assert!(store
            .query_path_commits(&a, &b, &p, i64::MAX, 0, 10)
            .await
            .unwrap()
            .is_empty());
    }
    assert!(store
        .query_path_commits(&account, &branch, &[], i64::MAX, 0, 10)
        .await
        .unwrap()
        .is_empty());
    assert!(store
        .query_path_commits(&account, &branch, &query, i64::MAX, 0, 0)
        .await
        .unwrap()
        .is_empty());
    cleanup(&account).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires a real PostgreSQL database"]
async fn postgres_snapshot_log_serializes_first_and_subsequent_appends() {
    // Independent pools model separate OV processes.
    let stores = [store().await, store().await];
    let account = uuid::Uuid::new_v4().simple().to_string();
    for parent in [None, Some(oid(1))] {
        let barrier = Arc::new(Barrier::new(2));
        let append = |index: usize| {
            let barrier = barrier.clone();
            let store = &stores[index];
            let account = &account;
            let parent = parent.as_deref();
            async move {
                barrier.wait().await;
                store
                    .append_rows_if_latest(
                        account,
                        "main",
                        parent,
                        &rows(if parent.is_none() { 1 } else { index + 2 }, &["/", "a"]),
                    )
                    .await
            }
        };
        let (a, b) = tokio::join!(append(0), append(1));
        assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
        assert!(a
            .as_ref()
            .err()
            .or(b.as_ref().err())
            .unwrap()
            .is_stale_latest());
    }
    // Failed/stale transactions release their locks and leave the pool usable.
    let latest = stores[0]
        .load_latest_indexed_commit(&account, "main")
        .await
        .unwrap()
        .unwrap();
    stores[1]
        .append_rows_if_latest(&account, "main", Some(&latest.1), &rows(4, &["/"]))
        .await
        .unwrap();
    cleanup(&account).await;
}

#[tokio::test]
#[ignore = "requires a real PostgreSQL database"]
async fn postgres_snapshot_log_rolls_back_whole_batch_and_rejects_invalid_rows() {
    let store = store().await;
    let account = uuid::Uuid::new_v4().simple().to_string();
    let err = store
        .append_rows_if_latest(&account, "main", Some(&oid(1)), &rows(2, &["/"]))
        .await
        .unwrap_err();
    assert!(err.is_stale_latest());
    let mut batch: Vec<_> = (0..1001)
        .map(|i| SnapshotLogRow {
            path: format!("docs/{i}"),
            commit_oid: oid(1),
        })
        .collect();
    batch.push(batch[0].clone()); // Fail in the second INSERT, after the first 1000 rows.
    assert!(matches!(
        store
            .append_rows_if_latest(&account, "main", None, &batch)
            .await,
        Err(SnapshotLogError::Sql(_))
    ));
    assert!(store
        .load_latest_indexed_commit(&account, "main")
        .await
        .unwrap()
        .is_none());
    for invalid in [vec![], rows(1, &[""]), rows(1, &[&"x".repeat(2049)])] {
        assert!(store
            .append_rows_if_latest(&account, "main", None, &invalid)
            .await
            .is_err());
    }
    batch.pop();
    store
        .append_rows_if_latest(&account, "main", None, &batch)
        .await
        .unwrap();
    assert_eq!(
        store
            .load_latest_indexed_commit(&account, "main")
            .await
            .unwrap()
            .unwrap()
            .1,
        oid(1)
    );
    cleanup(&account).await;
}

#[tokio::test]
#[ignore = "requires a real PostgreSQL database"]
async fn postgres_snapshot_log_startup_does_not_provision_schema() {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn())
        .await
        .unwrap();
    let schema = format!("snapshot_empty_{}", uuid::Uuid::new_v4().simple());
    sqlx::query(&format!("CREATE SCHEMA {schema}"))
        .execute(&pool)
        .await
        .unwrap();
    let mut url = url::Url::parse(&dsn()).unwrap();
    url.query_pairs_mut()
        .append_pair("options", &format!("-c search_path={schema}"));
    assert!(matches!(
        PostgresSnapshotLogStore::connect(url.as_str(), 1, 5_000).await,
        Err(SnapshotLogError::Sql(_))
    ));
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM pg_tables WHERE schemaname = $1")
        .bind(&schema)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
    sqlx::query(&format!("DROP SCHEMA {schema}"))
        .execute(&pool)
        .await
        .unwrap();
    pool.close().await;
}
