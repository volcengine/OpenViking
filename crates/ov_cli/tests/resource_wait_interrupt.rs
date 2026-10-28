//! Exercise the real process exit path without cancelling the server task.
#![cfg(unix)]

use serde_json::json;
use std::process::Stdio;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    process::Command,
    sync::oneshot,
    time::{Duration, timeout},
};

async fn read_request(stream: &mut tokio::net::TcpStream) -> String {
    let mut request = Vec::new();
    loop {
        let mut chunk = [0; 4096];
        let size = stream.read(&mut chunk).await.unwrap();
        assert!(size > 0);
        request.extend_from_slice(&chunk[..size]);
        if let Some(end) = request.windows(4).position(|w| w == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&request[..end]);
            let length = headers
                .lines()
                .filter_map(|line| line.split_once(':'))
                .find(|(key, _)| key.eq_ignore_ascii_case("content-length"))
                .map(|(_, value)| value.trim().parse::<usize>().unwrap())
                .unwrap_or(0);
            if request.len() >= end + 4 + length {
                return String::from_utf8(request).unwrap();
            }
        }
    }
}

#[tokio::test]
async fn ctrl_c_exits_130_without_cancelling_server_task() {
    timeout(Duration::from_secs(20), async {
        for (config_output, cli_output) in [("table", None), ("table", Some("json")), ("json", None)] {
            let json_output = cli_output.unwrap_or(config_output) == "json";
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let url = format!("http://{}", listener.local_addr().unwrap());
            let (status_tx, status_rx) = oneshot::channel();
            let (finish_tx, finish_rx) = oneshot::channel();
            let server = tokio::spawn(async move {
                let (mut submit, _) = listener.accept().await.unwrap();
                let request = read_request(&mut submit).await;
                assert!(request.starts_with("POST /api/v1/resources HTTP"));
                let body: serde_json::Value =
                    serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
                assert_eq!(body["wait"], false);
                let response = json!({"status":"ok","result":{"task_id":"task-import"}}).to_string();
                submit.write_all(format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}",
                    response.len()
                ).as_bytes()).await.unwrap();
                let (mut status, _) = listener.accept().await.unwrap();
                assert!(read_request(&mut status).await.starts_with("GET /api/v1/tasks/task-import"));
                status_tx.send(()).unwrap();
                // Keep this status GET in flight until the interrupted CLI exits.
                finish_rx.await.unwrap();
                let listener = listener.into_std().unwrap();
                assert!(matches!(listener.accept(), Err(e) if e.kind() == std::io::ErrorKind::WouldBlock),
                    "CLI must neither resubmit nor cancel the task");
            });
            let dir = tempfile::tempdir().unwrap();
            std::fs::write(dir.path().join("ovcli.conf"), json!({"url":url,"output":config_output}).to_string()).unwrap();
            std::fs::create_dir(dir.path().join(".openviking")).unwrap();
            std::fs::write(dir.path().join(".openviking/ovcli.settings.conf"),
                json!({"language":"en"}).to_string()).unwrap();
            let mut args = vec!["add-resource", "https://example.com/docs",
                "--to", "viking://resources/demo/docs", "--wait"];
            if let Some(format) = cli_output {
                args.extend(["--output", format]);
            }
            let child = Command::new(env!("CARGO_BIN_EXE_ov"))
                // Isolate saved language settings from the user's real home.
                .env("HOME", dir.path())
                .env("OPENVIKING_CLI_CONFIG_FILE", dir.path().join("ovcli.conf"))
                .env("OPENVIKING_LANG", "en")
                .env("NO_COLOR", "1")
                .args(&args)
                .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
                .kill_on_drop(true).spawn().unwrap();
            status_rx.await.unwrap();
            let signal = Command::new("kill").args(["-INT", &child.id().unwrap().to_string()])
                .status().await.unwrap();
            assert!(signal.success());
            let output = child.wait_with_output().await.unwrap();
            assert_eq!(output.status.code(), Some(130), "{output:?}");
            assert!(output.stdout.is_empty());
            let stderr = String::from_utf8(output.stderr).unwrap();
            if json_output {
                // 显式参数或配置选择 JSON 时，stderr 必须是完整错误 envelope。
                let envelope: serde_json::Value = serde_json::from_str(&stderr)
                    .unwrap_or_else(|e| panic!("stderr must be a JSON envelope ({e}): {stderr}"));
                assert_eq!(envelope["ok"], false, "{stderr}");
                assert_eq!(envelope["error"]["code"], "INTERRUPTED", "{stderr}");
                assert_eq!(envelope["error"]["details"]["task_id"], "task-import", "{stderr}");
                assert!(envelope["error"]["message"]
                    .as_str().unwrap().contains("ov task status task-import"), "{stderr}");
            } else {
                assert!(stderr.contains("ov task status task-import"), "{stderr}");
                assert_eq!(stderr.lines().count(), 1, "{stderr}");
                assert!(!stderr.contains("INVALID_ARGUMENT") && !stderr.contains("Client error"));
            }
            finish_tx.send(()).unwrap();
            server.await.unwrap();
        }
    }).await.expect("SIGINT integration test exceeded 20s");
}
