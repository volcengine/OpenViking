use super::*;

#[test]
fn logging_timestamp_uses_utc_milliseconds() {
    assert_eq!(
        log_timestamp(UNIX_EPOCH + std::time::Duration::from_millis(12)),
        "1970-01-01T00:00:00.012Z"
    );
}

#[test]
fn logging_output_is_single_line_and_omits_unknown_fields() {
    let path = std::env::temp_dir().join(format!("ragfs-log-{}.txt", uuid::Uuid::new_v4()));
    let writer = SharedFileWriter {
        file: Arc::new(Mutex::new(
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .unwrap(),
        )),
    };
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(Level::TRACE)
        .event_format(CommonLogFormat)
        .with_writer(move || writer.clone())
        .finish();
    tracing::subscriber::with_default(subscriber, || {
        tracing::warn!(target: "ragfs.logging.test", total = 2u64, peer_id = "private-peer", "line1\nline2");
        tracing::debug!(target: "ragfs.logging.test", "debug event");
        tracing::info!(target: "ragfs.logging.test", "info event");
        tracing::error!(target: "ragfs.logging.test", "error event");
        tracing::trace!(target: "ragfs.logging.test", "trace event");
        tracing::warn!(target: "ragfs.logging.test", "{}", "x".repeat(3000));
    });
    let content = std::fs::read_to_string(&path).unwrap();
    std::fs::remove_file(path).unwrap();
    assert_eq!(content.lines().count(), 6);
    assert!(content.contains(" WARNING ragfs.logging.test pid="));
    assert!(content.contains("request_id=- trace_id=-"));
    assert!(content.contains("message=\"line1\\nline2\" total=2"));
    assert!(!content.contains("private-peer"));
    assert!(!content.contains('\u{1b}'));
    assert_eq!(content.matches(" DEBUG ragfs.logging.test").count(), 2);
    assert_eq!(content.matches(" INFO ragfs.logging.test").count(), 1);
    assert_eq!(content.matches(" ERROR ragfs.logging.test").count(), 1);
    assert!(content.contains("original_length=3000 truncated=true"));
}

#[test]
fn parse_tracing_level_accepts_critical_as_error() {
    assert_eq!(parse_tracing_level("CRITICAL").unwrap(), Level::ERROR);
}

#[test]
fn replace_tracing_log_file_switches_shared_writer_to_new_active_file() {
    let active = std::env::temp_dir().join(format!(
        "openviking-rust-tracing-active-{}.log",
        std::process::id()
    ));
    let rotated = std::env::temp_dir().join(format!(
        "openviking-rust-tracing-rotated-{}.log",
        std::process::id()
    ));
    let _ = fs::remove_file(&active);
    let _ = fs::remove_file(&rotated);

    let shared = Arc::new(Mutex::new(open_tracing_log_file(&active).unwrap()));
    let mut writer = SharedFileWriter {
        file: shared.clone(),
    };
    writer.write_all(b"before-rotate\n").unwrap();
    writer.flush().unwrap();

    fs::rename(&active, &rotated).unwrap();
    replace_tracing_log_file(&shared, &active).unwrap();

    writer.write_all(b"after-rotate\n").unwrap();
    writer.flush().unwrap();

    assert!(fs::read_to_string(&rotated)
        .unwrap()
        .contains("before-rotate"));
    assert!(fs::read_to_string(&active)
        .unwrap()
        .contains("after-rotate"));

    let _ = fs::remove_file(&active);
    let _ = fs::remove_file(&rotated);
}
