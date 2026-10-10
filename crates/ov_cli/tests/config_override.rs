//! Run each override in its own process: never mutate the test runner's environment.
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Output, Stdio},
};

fn run(dir: &std::path::Path, selected: &str, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_ov"))
        .current_dir(dir)
        .env("OPENVIKING_CLI_CONFIG_FILE", selected)
        .env("OPENVIKING_LANG", "en")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .args(args)
        .output()
        .unwrap()
}

fn seed(dir: &std::path::Path, name: &str, url: &str) {
    fs::write(
        dir.join(name),
        json!({"url": url, "output": "table"}).to_string(),
    )
    .unwrap();
}

#[test]
fn status_and_config_list_identify_the_selected_file() {
    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), "ovcli.conf", "http://default.invalid");
    seed(dir.path(), "ovcli.conf.aaa", "http://127.0.0.1:9");
    seed(dir.path(), "ovcli.conf.selected", "http://127.0.0.1:9");
    let selected = dir.path().join("ovcli.conf.selected");
    let result = run(dir.path(), selected.to_str().unwrap(), &["status"]);
    let text = String::from_utf8_lossy(&result.stdout);
    assert!(result.status.success(), "{result:?}");
    assert!(text.contains("selected"), "{text}");
    assert!(text.contains("127.0.0.1:9"), "{text}");
    assert!(text.contains(dir.path().to_str().unwrap()), "{text}");
    assert!(!text.contains("default.invalid"), "{text}");
    let result = run(
        dir.path(),
        "ovcli.conf.selected",
        &["config", "list", "-o", "json"],
    );
    assert!(result.status.success(), "{result:?}");
    let data: Value = serde_json::from_slice(&result.stdout).unwrap();
    let entries = data["result"].as_array().unwrap();
    assert_eq!(entries.iter().filter(|e| e["active"] == true).count(), 1);
    assert!(
        entries
            .iter()
            .any(|e| e["name"] == "selected" && e["active"] == true)
    );
}

#[test]
fn missing_override_never_loads_another_profile() {
    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), "ovcli.conf", "http://default.invalid");
    let result = run(dir.path(), "missing.json", &["config", "show"]);
    assert!(!result.status.success());
    assert!(!String::from_utf8_lossy(&result.stdout).contains("default.invalid"));
    let result = run(dir.path(), "missing.json", &["status"]);
    assert!(!result.status.success());
}

#[test]
fn empty_override_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let result = run(dir.path(), "", &["config", "list"]);
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("must not be empty"));
}

#[test]
fn connected_status_uses_the_same_profile_as_the_request() {
    use std::io::{Read, Write};
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        let mut request = [0; 4096];
        let len = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..len]).contains("GET /api/v1/observer/system"));
        let body = r#"{"status":"ok","result":{}}"#;
        write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
    });
    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), "ovcli.conf", "http://default.invalid");
    seed(dir.path(), "ovcli.conf.selected", &url);
    let result = run(dir.path(), "ovcli.conf.selected", &["status"]);
    server.join().unwrap();
    assert!(result.status.success(), "{result:?}");
    let text = String::from_utf8_lossy(&result.stdout);
    assert!(text.contains("selected") && text.contains(&url), "{text}");
    assert!(!text.contains("Unreachable"), "{text}");
}

#[test]
fn reindex_request_timeout_overrides_the_selected_config() {
    use std::io::{BufRead, BufReader, Read, Write};
    use std::time::{Duration, Instant};

    for (configured, extra, succeeds) in [
        (0.02, vec!["--timeout", "2"], true),
        (2.0, vec!["--timeout", "0.02"], false),
        (0.02, vec![], false),
    ] {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let server = std::thread::spawn(move || {
            let started = Instant::now();
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(started.elapsed() < Duration::from_secs(5));
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(e) => panic!("{e}"),
                }
            };
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut reader = BufReader::new(&mut stream);
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            assert_eq!(line, "POST /api/v1/content/reindex HTTP/1.1\r\n");
            let mut length = 0;
            loop {
                line.clear();
                assert!(reader.read_line(&mut line).unwrap() > 0);
                if line == "\r\n" {
                    break;
                }
                if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                    length = value.trim().parse::<usize>().unwrap();
                }
            }
            let mut body = vec![0; length];
            reader.read_exact(&mut body).unwrap();
            let request: Value = serde_json::from_slice(&body).unwrap();
            assert_eq!(
                request,
                json!({
                    "uri": "viking://resources/demo", "mode": "vectors_only", "wait": true
                })
            );
            std::thread::sleep(Duration::from_millis(200));
            let body = r#"{"status":"ok","result":{"completed":true}}"#;
            // The client intentionally disconnects in the two timeout cases.
            let _ = write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
        });
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("ovcli.conf.selected"),
            json!({
                "url": url, "timeout": configured
            })
            .to_string(),
        )
        .unwrap();
        let mut args = vec!["reindex", "viking://resources/demo", "-o", "json"];
        args.extend(extra);
        let result = run(dir.path(), "ovcli.conf.selected", &args);
        server.join().unwrap();
        assert_eq!(result.status.success(), succeeds, "{result:?}");
        if succeeds {
            let response: Value = serde_json::from_slice(&result.stdout).unwrap();
            assert_eq!(response["result"]["completed"], true);
        } else {
            let error: Value = serde_json::from_slice(&result.stderr).unwrap();
            assert_eq!(error["error"]["code"], "DEADLINE_EXCEEDED");
        }
    }
}
