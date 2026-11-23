//! Real database contracts, explicitly run against a disposable provisioned database:
//! OV_TEST_PG_DSN='...' cargo test -p ragfs --features postgres --test postgres_queuefs -- --ignored
#![cfg(feature = "postgres")]
use ragfs::core::{
    plugin::ServicePlugin,
    types::{ConfigValue, PluginConfig, WriteFlag},
};
use ragfs::plugins::queuefs::{Message, PostgresQueueStorage, QueueFSPlugin};
use std::{collections::HashSet, sync::Arc, time::Duration};

fn namespace() -> String {
    format!("queue-test-{}", uuid::Uuid::new_v4())
}
async fn storage(ns: &str) -> Arc<PostgresQueueStorage> {
    Arc::new(
        PostgresQueueStorage::from_env("OV_TEST_PG_DSN", ns, 10)
            .await
            .unwrap(),
    )
}
async fn admin<T: Send + 'static>(
    f: impl FnOnce(&mut postgres::Client) -> T + Send + 'static,
) -> T {
    tokio::task::spawn_blocking(move || {
        let mut client = postgres::Client::connect(
            &std::env::var("OV_TEST_PG_DSN").expect("use a disposable PostgreSQL database"),
            postgres::NoTls,
        )
        .unwrap();
        f(&mut client)
    })
    .await
    .unwrap()
}
async fn cleanup(ns: String) {
    admin(move |c| {
        c.execute(
            "DELETE FROM public.ov_queuefs_queues WHERE namespace=$1",
            &[&ns],
        )
        .unwrap();
        c.execute(
            "DELETE FROM public.ov_queuefs_workers WHERE namespace=$1",
            &[&ns],
        )
        .unwrap();
    })
    .await;
}

#[tokio::test]
#[ignore]
async fn concurrent_claims_and_ack_ownership() {
    let ns = namespace();
    let producer = storage(&ns).await;
    producer.create_queue("tasks").await.unwrap();
    let mut expected = HashSet::new();
    for n in 0..60 {
        let msg = Message::new(vec![n]);
        expected.insert(msg.id.clone());
        producer.enqueue("tasks", msg).await.unwrap();
    }
    let mut tasks = Vec::new();
    for _ in 0..6 {
        let consumer = storage(&ns).await;
        let other = producer.clone();
        tasks.push(tokio::spawn(async move {
            let mut ids = Vec::new();
            while let Some(m) = consumer.dequeue("tasks").await.unwrap() {
                assert!(!other.ack("tasks", &m.id).await.unwrap());
                assert!(consumer.ack("tasks", &m.id).await.unwrap());
                assert!(!consumer.ack("tasks", &m.id).await.unwrap());
                ids.push(m.id);
            }
            consumer.shutdown().await.unwrap();
            ids
        }));
    }
    let mut claimed = HashSet::new();
    for task in tasks {
        for id in task.await.unwrap() {
            assert!(claimed.insert(id));
        }
    }
    assert_eq!(claimed, expected);
    assert_eq!(producer.status("tasks").await.unwrap().pending, 0);
    assert_eq!(producer.status("tasks").await.unwrap().processing, 0);
    producer.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn lossless_fifo_namespace_and_lifecycle() {
    let ns = namespace();
    let a = storage(&ns).await;
    let b = storage(&namespace()).await;
    a.create_queue("nested/%_").await.unwrap();
    a.create_queue("nested/other").await.unwrap();
    assert!(a.create_queue("nested/%_").await.is_err());
    assert_eq!(a.list_queues("nested/%").await.unwrap(), vec!["nested/%_"]);
    assert!(!b.queue_exists("nested/%_").await.unwrap());
    assert!(a.dequeue("absent").await.is_err());
    let mut msg = Message::new(vec![0, 255, 128, 10, 13]);
    msg.timestamp = std::time::UNIX_EPOCH + Duration::new(1_700_000_000, 123_456_789);
    a.enqueue("nested/%_", msg.clone()).await.unwrap();
    a.enqueue("nested/%_", Message::new(b"second".to_vec()))
        .await
        .unwrap();
    let peek = a.peek("nested/%_").await.unwrap().unwrap();
    assert_eq!(peek.id, msg.id);
    assert_eq!(peek.data, msg.data);
    assert_eq!(peek.timestamp, msg.timestamp);
    assert_eq!(a.size("nested/%_").await.unwrap(), 2);
    let first = a.dequeue("nested/%_").await.unwrap().unwrap();
    assert_eq!(first.id, msg.id);
    let state = a.status("nested/%_").await.unwrap();
    assert_eq!((state.pending, state.processing), (1, 1));
    assert_eq!(a.list_unacked("nested/%_").await.unwrap().len(), 2);
    a.clear("nested/%_").await.unwrap();
    assert!(!a.ack("nested/%_", &msg.id).await.unwrap());
    assert!(a.dequeue("nested/%_").await.unwrap().is_none());
    a.remove_queue("nested/%_").await.unwrap();
    assert!(!a.queue_exists("nested/%_").await.unwrap());
    a.shutdown().await.unwrap();
    b.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn heartbeat_protects_long_tasks_and_expired_owner_is_fenced() {
    let ns = namespace();
    let a = storage(&ns).await;
    a.create_queue("tasks").await.unwrap();
    a.enqueue("tasks", Message::new(b"slow".to_vec()))
        .await
        .unwrap();
    let msg = a.dequeue("tasks").await.unwrap().unwrap();
    let b = storage(&ns).await; // Startup must not reset a live peer's processing rows.
    assert!(b.dequeue("tasks").await.unwrap().is_none());
    tokio::time::sleep(Duration::from_secs(12)).await;
    assert!(b.dequeue("tasks").await.unwrap().is_none());
    assert!(a.ack("tasks", &msg.id).await.unwrap());
    a.enqueue("tasks", Message::new(b"abandoned".to_vec()))
        .await
        .unwrap();
    let abandoned = a.dequeue("tasks").await.unwrap().unwrap();
    let ns_expire = ns.clone();
    let id = abandoned.id.clone();
    admin(move |c| {
        c.execute(
            "UPDATE public.ov_queuefs_workers SET lease_expires_at=clock_timestamp()-interval '1 second'
             WHERE namespace=$1 AND owner=(SELECT owner FROM public.ov_queuefs_messages
                 WHERE namespace=$1 AND message_id=$2)", &[&ns_expire, &id],
        ).unwrap();
    }).await;
    assert!(a.ack("tasks", &abandoned.id).await.is_err());
    let recovered = b.dequeue("tasks").await.unwrap().unwrap();
    assert_eq!(recovered.id, abandoned.id);
    assert!(a.ack("tasks", &abandoned.id).await.is_err());
    assert!(b.ack("tasks", &abandoned.id).await.unwrap());
    a.shutdown().await.unwrap();
    b.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn shutdown_and_drop_recover_without_resurrection() {
    let ns = namespace();
    let a = storage(&ns).await;
    a.create_queue("tasks").await.unwrap();
    a.enqueue("tasks", Message::new(b"shutdown".to_vec()))
        .await
        .unwrap();
    let msg = a.dequeue("tasks").await.unwrap().unwrap();
    a.shutdown().await.unwrap();
    assert!(a.enqueue("tasks", Message::new(vec![])).await.is_err());
    let b = storage(&ns).await;
    assert_eq!(b.dequeue("tasks").await.unwrap().unwrap().id, msg.id);
    drop(b); // Heartbeat stops; abandoned delivery is recovered after lease expiry.
    tokio::time::sleep(Duration::from_secs(11)).await;
    let c = storage(&ns).await;
    assert_eq!(c.dequeue("tasks").await.unwrap().unwrap().id, msg.id);
    assert!(c.ack("tasks", &msg.id).await.unwrap());
    c.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn decoding_failure_rolls_back_claim_and_errors_redact_payload() {
    let ns = namespace();
    let a = storage(&ns).await;
    a.create_queue("tasks").await.unwrap();
    let msg = Message::new(b"payload".to_vec());
    a.enqueue("tasks", msg.clone()).await.unwrap();
    let duplicate = a
        .enqueue("tasks", msg.clone())
        .await
        .unwrap_err()
        .to_string();
    assert!(duplicate.contains("23505"));
    assert!(!duplicate.contains(&msg.id));
    let ns_corrupt = ns.clone();
    admin(move |c| {
        c.execute(
            "UPDATE public.ov_queuefs_messages SET payload='private-secret-invalid-json'
            WHERE namespace=$1",
            &[&ns_corrupt],
        )
        .unwrap();
    })
    .await;
    let error = a.dequeue("tasks").await.unwrap_err().to_string();
    assert!(!error.contains("private-secret"));
    let state = a.status("tasks").await.unwrap();
    assert_eq!((state.pending, state.processing), (1, 0));
    a.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn filesystem_control_files_use_postgres() {
    let ns = namespace();
    let plugin = QueueFSPlugin::new();
    let config = PluginConfig::single_backend(
        "queuefs",
        "/queue",
        std::collections::HashMap::from([
            ("backend".into(), ConfigValue::String("postgres".into())),
            ("service_name".into(), ConfigValue::String(ns.clone())),
            (
                "postgres_dsn_env".into(),
                ConfigValue::String("OV_TEST_PG_DSN".into()),
            ),
            ("postgres_lease_seconds".into(), ConfigValue::Int(10)),
        ]),
    );
    plugin.validate(&config).await.unwrap();
    let fs = plugin.initialize(config).await.unwrap();
    fs.mkdir("/tasks", 0o755).await.unwrap();
    fs.write("/tasks/enqueue", b"hello", 0, WriteFlag::None)
        .await
        .unwrap();
    let bytes = fs.read("/tasks/dequeue", 0, 0).await.unwrap();
    let message: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(message["data"], "hello");
    fs.write(
        "/tasks/ack",
        message["id"].as_str().unwrap().as_bytes(),
        0,
        WriteFlag::None,
    )
    .await
    .unwrap();
    assert_eq!(fs.read("/tasks/size", 0, 0).await.unwrap(), b"0");
    drop(fs);
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn disconnected_client_reconnects_on_a_subsequent_operation() {
    let ns = namespace();
    let a = storage(&ns).await;
    a.create_queue("tasks").await.unwrap();
    a.enqueue("tasks", Message::new(b"existing".to_vec()))
        .await
        .unwrap();
    let ns_kill = ns.clone();
    admin(move |c| {
        let owner: String = c
            .query_one(
                "SELECT owner FROM public.ov_queuefs_workers WHERE namespace=$1",
                &[&ns_kill],
            )
            .unwrap()
            .get(0);
        let application = format!("ov-queuefs-{owner}");
        let killed = c
            .query(
                "SELECT pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity
             WHERE application_name=$1",
                &[&application],
            )
            .unwrap();
        assert_eq!(killed.len(), 2);
    })
    .await;
    // The first operation may discover the closed socket, or reconnect if its
    // driver has already observed EOF. Never retry that same operation internally.
    let first = a.size("tasks").await;
    if let Ok(size) = first {
        assert_eq!(size, 1);
    }
    assert_eq!(a.size("tasks").await.unwrap(), 1);
    let message = a.dequeue("tasks").await.unwrap().unwrap();
    assert!(a.ack("tasks", &message.id).await.unwrap());
    a.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn keyed_claims_and_retry_preserve_identity_and_affinity() {
    let ns = namespace();
    let a = storage(&ns).await;
    let b = storage(&ns).await;
    a.create_queue("tasks").await.unwrap();
    let mut first = Message::new(b"first".to_vec());
    first.serial_key = Some("serial".into());
    let mut second = Message::new(b"second".to_vec());
    second.serial_key = first.serial_key.clone();
    a.enqueue("tasks", first.clone()).await.unwrap();
    a.enqueue("tasks", second.clone()).await.unwrap();
    let (claim_a, claim_b) = tokio::join!(a.dequeue("tasks"), b.dequeue("tasks"));
    let (owner, other, claimed) = match (claim_a.unwrap(), claim_b.unwrap()) {
        (Some(m), None) => (&a, &b, m),
        (None, Some(m)) => (&b, &a, m),
        _ => panic!("only one consumer may claim a serial key"),
    };
    assert_eq!(claimed.id, first.id);
    assert!(owner.dequeue("tasks").await.unwrap().is_none());
    assert!(!other.retry("tasks", &first.id).await.unwrap());
    assert!(owner.retry("tasks", &first.id).await.unwrap());
    assert!(!owner.retry("tasks", &first.id).await.unwrap());
    let redelivered = other.dequeue("tasks").await.unwrap().unwrap();
    assert_eq!(redelivered.id, first.id);
    assert_eq!(redelivered.serial_key, first.serial_key);
    assert!(owner.dequeue("tasks").await.unwrap().is_none());
    assert!(other.ack("tasks", &first.id).await.unwrap());
    assert_eq!(owner.dequeue("tasks").await.unwrap().unwrap().id, second.id);
    assert!(owner.ack("tasks", &second.id).await.unwrap());

    let mut grouped = Message::new(b"group-1".to_vec());
    grouped.group_key = Some("group".into());
    let mut grouped_next = Message::new(b"group-2".to_vec());
    grouped_next.group_key = grouped.group_key.clone();
    a.enqueue("tasks", grouped.clone()).await.unwrap();
    a.enqueue("tasks", grouped_next.clone()).await.unwrap();
    let (claim_a, claim_b) = tokio::join!(a.dequeue("tasks"), b.dequeue("tasks"));
    let (owner, other) = match (claim_a.unwrap(), claim_b.unwrap()) {
        (Some(m), None) if m.id == grouped.id => (&a, &b),
        (None, Some(m)) if m.id == grouped.id => (&b, &a),
        _ => panic!("one group must have one consumer owner"),
    };
    let next = owner.dequeue("tasks").await.unwrap().unwrap();
    assert_eq!(next.id, grouped_next.id);
    assert_eq!(next.group_key, grouped.group_key);
    assert!(owner.ack("tasks", &grouped.id).await.unwrap());
    let mut third = Message::new(b"group-3".to_vec());
    third.group_key = grouped.group_key.clone();
    a.enqueue("tasks", third.clone()).await.unwrap();
    assert!(other.dequeue("tasks").await.unwrap().is_none());
    assert!(owner.ack("tasks", &grouped_next.id).await.unwrap());
    assert_eq!(other.dequeue("tasks").await.unwrap().unwrap().id, third.id);
    assert!(other.ack("tasks", &third.id).await.unwrap());
    a.shutdown().await.unwrap();
    b.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn plain_retry_goes_to_tail_and_invalid_keys_are_rejected() {
    let ns = namespace();
    let a = storage(&ns).await;
    a.create_queue("tasks").await.unwrap();
    let first = Message::new(b"first".to_vec());
    let second = Message::new(b"second".to_vec());
    a.enqueue("tasks", first.clone()).await.unwrap();
    a.enqueue("tasks", second.clone()).await.unwrap();
    assert_eq!(a.dequeue("tasks").await.unwrap().unwrap().id, first.id);
    assert!(a.retry("tasks", &first.id).await.unwrap());
    assert_eq!(a.dequeue("tasks").await.unwrap().unwrap().id, second.id);
    assert_eq!(a.dequeue("tasks").await.unwrap().unwrap().id, first.id);
    let mut invalid = Message::new(vec![]);
    invalid.group_key = Some("g".into());
    invalid.serial_key = Some("s".into());
    assert!(a.enqueue("tasks", invalid).await.is_err());
    a.clear("tasks").await.unwrap();
    a.shutdown().await.unwrap();
    cleanup(ns).await;
}

#[tokio::test]
#[ignore]
async fn runtime_role_needs_no_ddl_and_missing_schema_fails_closed() {
    // The disposable test server uses local trust authentication. Never run this
    // test against a business database: it creates a temporary role and database.
    let suffix = uuid::Uuid::new_v4().simple().to_string();
    let role = format!("queue_role_{suffix}");
    let database = format!("queue_db_{suffix}");
    let role_create = role.clone();
    let database_create = database.clone();
    admin(move |c| {
        c.batch_execute(&format!(
            "CREATE ROLE {role_create} LOGIN;
             GRANT USAGE ON SCHEMA public TO {role_create};
             GRANT SELECT ON public.ov_queuefs_schema TO {role_create};
             GRANT SELECT,INSERT,UPDATE,DELETE ON public.ov_queuefs_queues,
                 public.ov_queuefs_workers,public.ov_queuefs_messages TO {role_create};
             GRANT USAGE ON SEQUENCE public.ov_queuefs_messages_sequence_seq TO {role_create}"
        ))
        .unwrap();
        c.batch_execute(&format!("CREATE DATABASE {database_create}"))
            .unwrap();
    })
    .await;
    let base = std::env::var("OV_TEST_PG_DSN").unwrap();
    let role_env = format!("OV_QUEUE_ROLE_{suffix}");
    let empty_env = format!("OV_QUEUE_EMPTY_{suffix}");
    std::env::set_var(&role_env, format!("{base} user={role}"));
    std::env::set_var(&empty_env, format!("{base} dbname={database}"));
    let ns = namespace();
    let runtime = PostgresQueueStorage::from_env(&role_env, &ns, 10)
        .await
        .unwrap();
    runtime.create_queue("tasks").await.unwrap();
    let m = Message::new(vec![1, 2, 3]);
    runtime.enqueue("tasks", m.clone()).await.unwrap();
    assert_eq!(runtime.dequeue("tasks").await.unwrap().unwrap().id, m.id);
    assert!(runtime.ack("tasks", &m.id).await.unwrap());
    runtime.remove_queue("tasks").await.unwrap();
    runtime.shutdown().await.unwrap();
    drop(runtime);
    let empty_error = match PostgresQueueStorage::from_env(&empty_env, &ns, 10).await {
        Ok(_) => panic!("unprovisioned database must fail"),
        Err(error) => error.to_string(),
    };
    assert!(empty_error.contains("provision postgres_schema.sql"));
    std::env::remove_var(role_env);
    std::env::remove_var(empty_env);
    admin(move |c| {
        assert!(!c
            .query_one(
                "SELECT has_schema_privilege($1,'public','CREATE')",
                &[&role],
            )
            .unwrap()
            .get::<_, bool>(0));
        c.batch_execute(&format!("DROP DATABASE {database}"))
            .unwrap();
        c.batch_execute(&format!("DROP OWNED BY {role}; DROP ROLE {role}"))
            .unwrap();
    })
    .await;
}
