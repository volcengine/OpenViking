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
