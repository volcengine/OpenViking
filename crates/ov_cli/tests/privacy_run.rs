#![cfg(unix)]

use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::Command;

const REFERENCE: &str = "{{ov_privacy:alice:viking://~/privacy/credential/demo#/values/pat}}";
const VALUE: &str = "test-secret-123";

fn mock_resolve(status: &str, result: &str) -> (String, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let status = status.to_string();
    let response = result.to_string();
    let handle = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0; 8192];
        let size = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..size]).contains("/privacy-configs/resolve"));
        write!(
            stream,
            "HTTP/1.1 {status}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{response}",
            response.len()
        )
        .unwrap();
    });
    (url, handle)
}

fn cli_config(dir: &std::path::Path, url: &str) -> std::path::PathBuf {
    let path = dir.join("ovcli.conf");
    std::fs::write(
        &path,
        serde_json::json!({"url": url, "api_key": "test-key", "user": "alice"}).to_string(),
    )
    .unwrap();
    path
}

#[test]
fn run_masks_both_streams_for_environment_and_inline_references() {
    for inline in [false, true] {
        let (url, server) = mock_resolve(
            "200 OK",
            &serde_json::json!({"status": "ok", "result": {"value": VALUE}}).to_string(),
        );
        let dir = tempfile::tempdir().unwrap();
        let config = cli_config(dir.path(), &url);
        let mut child = Command::new(env!("CARGO_BIN_EXE_ov"));
        child
            .env("OPENVIKING_CLI_CONFIG_FILE", config)
            .env("OPENVIKING_LANG", "en")
            .args(["privacy", "run"]);
        if inline {
            child.args([
                "--",
                "/bin/sh",
                "-c",
                "printf '%s' \"$1\"; printf '%s' \"$1\" >&2",
                "sh",
                REFERENCE,
            ]);
        } else {
            child.args([
                "--env",
                &format!("TOKEN={REFERENCE}"),
                "--",
                "/bin/sh",
                "-c",
                "printf '%s' \"$TOKEN\"; printf '%s' \"$TOKEN\" >&2",
            ]);
        }
        let output = child.output().unwrap();
        server.join().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.stdout, REFERENCE.as_bytes());
        assert_eq!(output.stderr, REFERENCE.as_bytes());
    }
}

#[test]
fn run_loads_env_file_and_applies_explicit_override() {
    let (url, server) = mock_resolve(
        "200 OK",
        &serde_json::json!({"status": "ok", "result": {"value": VALUE}}).to_string(),
    );
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), &url);
    let file = dir.path().join("privacy.env");
    let old_reference = REFERENCE.replace("/pat}}", "/old}}");
    std::fs::write(
        &file,
        format!("\u{feff}# references\r\nFILE=\"{REFERENCE}\"\r\nTOKEN='{old_reference}'\r\n"),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .arg("privacy")
        .arg("run")
        .arg("--env-file")
        .arg(&file)
        .arg("--env")
        .arg(format!("TOKEN={REFERENCE}"))
        .args([
            "--",
            "/bin/sh",
            "-c",
            "printf '%s|%s|%s' \"$FILE\" \"$TOKEN\" \"$1\"",
            "sh",
            REFERENCE,
        ])
        .output()
        .unwrap();
    server.join().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        output.stdout,
        format!("{REFERENCE}|{REFERENCE}|{REFERENCE}").as_bytes()
    );
}

#[test]
fn run_interpolates_repeated_argument_and_environment_references_without_shell_parsing() {
    let value = "a b'\"\\$() ; `echo injected`\nline2";
    let (url, server) = mock_resolve(
        "200 OK",
        &serde_json::json!({"status": "ok", "result": {"value": value}}).to_string(),
    );
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), &url);
    let output = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .args(["privacy", "run", "--env", &format!("TOKEN={REFERENCE}")])
        .args([
            "--",
            "/bin/sh",
            "-c",
            "printf '%s|%s|%s' \"$1\" \"$2\" \"$TOKEN\"",
            "sh",
            &format!("Authorization: Bearer {REFERENCE}"),
            REFERENCE,
        ])
        .output()
        .unwrap();
    server.join().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        output.stdout,
        format!("Authorization: Bearer {REFERENCE}|{REFERENCE}|{REFERENCE}").as_bytes()
    );
}

#[test]
fn run_uses_later_env_file_for_the_same_name() {
    let (url, server) = mock_resolve(
        "200 OK",
        &serde_json::json!({"status": "ok", "result": {"value": VALUE}}).to_string(),
    );
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), &url);
    let earlier = dir.path().join("earlier.env");
    let later = dir.path().join("later.env");
    std::fs::write(
        &earlier,
        format!("TOKEN={}\n", REFERENCE.replace("/pat}}", "/old}}")),
    )
    .unwrap();
    std::fs::write(&later, format!("TOKEN={REFERENCE}\n")).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .args(["privacy", "run", "--env-file"])
        .arg(earlier)
        .arg("--env-file")
        .arg(later)
        .args(["--", "/bin/sh", "-c", "printf '%s' \"$TOKEN\""])
        .output()
        .unwrap();
    server.join().unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout, REFERENCE.as_bytes());
}

#[test]
fn run_rejects_invalid_env_file_before_starting_child() {
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), "http://127.0.0.1:1");
    let file = dir.path().join("privacy.env");
    let marker = dir.path().join("executed");
    for line in ["TOKEN=accidental-plaintext", "BAD-NAME=anything"] {
        std::fs::write(&file, format!("# comment\n{line}\n")).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_ov"))
            .env("OPENVIKING_CLI_CONFIG_FILE", &config)
            .env("OPENVIKING_LANG", "en")
            .args(["privacy", "run", "--env-file"])
            .arg(&file)
            .args(["--", "/bin/sh", "-c"])
            .arg(format!("touch '{}'", marker.display()))
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(!marker.exists());
        assert!(!String::from_utf8_lossy(&output.stderr).contains("accidental-plaintext"));
    }
}

#[test]
fn run_does_not_execute_after_resolution_failure() {
    let (url, server) = mock_resolve(
        "404 Not Found",
        r#"{"status":"error","error":{"code":"NOT_FOUND","message":"missing"}}"#,
    );
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), &url);
    let marker = dir.path().join("executed");
    let output = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .args([
            "privacy",
            "run",
            "--env",
            &format!("TOKEN={REFERENCE}"),
            "--",
            "/bin/sh",
            "-c",
            &format!("touch '{}'", marker.display()),
        ])
        .output()
        .unwrap();
    server.join().unwrap();
    assert!(!output.status.success());
    assert!(!marker.exists());
}

#[test]
fn run_rejects_incomplete_argument_reference_before_starting_child() {
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), "http://127.0.0.1:1");
    let marker = dir.path().join("executed");
    let output = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .args(["privacy", "run", "--", "/bin/sh", "-c"])
        .arg(format!("touch '{}'", marker.display()))
        .arg("{{ov_privacy:incomplete")
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!marker.exists());
}

#[test]
fn run_errors_do_not_echo_command_arguments() {
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), "http://127.0.0.1:1");
    for args in [
        vec!["--env", "TOKEN=accidental-plaintext", "--", "/bin/true"],
        vec![
            "--",
            "/nonexistent-privacy-run-program",
            "accidental-plaintext",
        ],
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_ov"))
            .env("OPENVIKING_CLI_CONFIG_FILE", &config)
            .env("OPENVIKING_LANG", "en")
            .args(["privacy", "run"])
            .args(args)
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(!String::from_utf8_lossy(&output.stderr).contains("accidental-plaintext"));
    }
}

#[test]
fn run_forwards_stdin_and_masks_echoed_value() {
    let (url, server) = mock_resolve(
        "200 OK",
        &serde_json::json!({"status": "ok", "result": {"value": VALUE}}).to_string(),
    );
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), &url);
    let mut child = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .args([
            "privacy",
            "run",
            "--env",
            &format!("TOKEN={REFERENCE}"),
            "--",
            "/bin/cat",
        ])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(VALUE.as_bytes())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    server.join().unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout, REFERENCE.as_bytes());
}

#[test]
fn run_forwards_output_without_references() {
    let dir = tempfile::tempdir().unwrap();
    let config = cli_config(dir.path(), "http://127.0.0.1:1");
    let output = Command::new(env!("CARGO_BIN_EXE_ov"))
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .args([
            "privacy",
            "run",
            "--",
            "/bin/sh",
            "-c",
            "printf out; printf err >&2; exit 7",
        ])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(7));
    assert_eq!(output.stdout, b"out");
    assert_eq!(output.stderr, b"err");
}
