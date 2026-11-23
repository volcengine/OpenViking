use super::HttpClient;
use serde_json::{Value, json};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

async fn bounded<T>(future: impl std::future::Future<Output = T>) -> T {
    tokio::time::timeout(Duration::from_secs(20), future)
        .await
        .expect("resource wait test timed out")
}

fn task(status: &str) -> Value {
    json!({"status":"ok","result":{
        "status":status, "error":null,
        "result":{"context_count":10,"code":"MODEL_ERROR","details":{"model":"test"}}
    }})
}

// Record real HTTP requests; delayed replies do not block accepting retries.
async fn server(
    responses: Vec<(u16, Value, u64)>,
) -> (HttpClient, tokio::task::JoinHandle<Vec<String>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let client = HttpClient::new(
        format!("http://{}", listener.local_addr().unwrap()),
        None,
        None,
        None,
        None,
        1.0,
        false,
        None,
    );
    let peer = tokio::spawn(bounded(async move {
        let mut requests = Vec::new();
        let mut writers = Vec::new();
        for (status, body, delay) in responses {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            loop {
                let mut bytes = [0; 4096];
                let count = socket.read(&mut bytes).await.unwrap();
                assert!(count > 0);
                request.extend_from_slice(&bytes[..count]);
                if let Some(end) = request.windows(4).position(|w| w == b"\r\n\r\n") {
                    let headers = String::from_utf8_lossy(&request[..end]);
                    let length = headers
                        .lines()
                        .filter_map(|l| l.split_once(':'))
                        .find(|(k, _)| k.eq_ignore_ascii_case("content-length"))
                        .map(|(_, v)| v.trim().parse::<usize>().unwrap())
                        .unwrap_or(0);
                    if request.len() >= end + 4 + length {
                        break;
                    }
                }
            }
            requests.push(String::from_utf8_lossy(&request).into_owned());
            writers.push(tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(delay)).await;
                let body = body.to_string();
                let response = format!(
                    "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = socket.write_all(response.as_bytes()).await;
            }));
        }
        for writer in writers {
            writer.await.unwrap();
        }
        requests
    }));
    (client, peer)
}

async fn submit(
    client: &HttpClient,
    path: &str,
    add_type: Option<String>,
    wait: bool,
) -> crate::error::Result<Value> {
    bounded(client.add_resource(
        path,
        add_type,
        Some("viking://resources/demo/docs".into()),
        None,
        None,
        "",
        "",
        wait,
        false,
        None,
        None,
        None,
        true,
        0.0,
        "semantic_and_vectors".into(),
        None,
        Vec::new(),
        "replace".into(),
        None,
        false,
        false,
    ))
    .await
}

#[tokio::test]
async fn submits_once_and_polls_file_directory_url_and_connector() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("doc.md");
    std::fs::write(&file, "# Example").unwrap();
    for (path, add_type, local) in [
        (file.to_str().unwrap(), None, true),
        (dir.path().to_str().unwrap(), None, true),
        ("https://example.com/docs", None, false),
        ("tos://bucket/docs", Some("tos".into()), false),
    ] {
        let mut replies = Vec::new();
        if local {
            replies.push((
                200,
                json!({"status":"ok","result":{"temp_file_id":"upload"}}),
                0,
            ));
        }
        replies.extend([
            (
                200,
                json!({"status":"ok","result":{"task_id":"task-import"}}),
                0,
            ),
            (200, task("pending"), 0),
            (200, task("running"), 0),
            (200, task("completed"), 0),
        ]);
        let (client, peer) = server(replies).await;
        let result = submit(&client, path, add_type, true).await.unwrap();
        assert_eq!(result["task_id"], "task-import");
        assert_eq!(result["context_count"], 10);
        let requests = bounded(peer).await.unwrap();
        let imports: Vec<_> = requests
            .iter()
            .filter(|r| r.starts_with("POST /api/v1/resources HTTP"))
            .collect();
        assert_eq!(imports.len(), 1);
        let body: Value =
            serde_json::from_str(imports[0].split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["wait"], false);
        assert_eq!(body["to"], "viking://resources/demo/docs");
        assert_eq!(
            requests
                .iter()
                .filter(|r| r.starts_with("GET /api/v1/tasks/task-import"))
                .count(),
            3
        );
    }
}

#[tokio::test]
async fn no_wait_returns_without_polling() {
    let (client, peer) = server(vec![(
        200,
        json!({"status":"ok","result":{"task_id":"task-import"}}),
        0,
    )])
    .await;
    assert_eq!(
        submit(&client, "https://example.com/docs", None, false)
            .await
            .unwrap()["task_id"],
        "task-import"
    );
    assert_eq!(bounded(peer).await.unwrap().len(), 1);
}

#[tokio::test]
async fn retries_timeouts_rate_limits_and_gateway_errors_without_resubmitting() {
    for (status, delay) in [(200, 3000), (429, 0), (502, 0), (503, 0), (504, 0)] {
        let (client, peer) = server(vec![
            (
                200,
                json!({"status":"ok","result":{"task_id":"task-import"}}),
                0,
            ),
            (status, task("running"), delay),
            (200, task("completed"), 0),
        ])
        .await;
        assert_eq!(
            submit(&client, "https://example.com/docs", None, true)
                .await
                .unwrap()["task_id"],
            "task-import"
        );
        let requests = bounded(peer).await.unwrap();
        assert_eq!(requests.len(), 3);
        assert!(requests[0].starts_with("POST "));
        assert!(
            requests[1..]
                .iter()
                .all(|r| r.starts_with("GET /api/v1/tasks/"))
        );
    }
}

#[tokio::test]
async fn stops_after_ten_failures_and_resets_counter_after_success() {
    for recovers in [false, true] {
        let failure = (
            503,
            json!({"error":{"code":"UNAVAILABLE","message":"offline"}}),
            0,
        );
        let mut replies = vec![failure.clone(); 9];
        if recovers {
            replies.push((200, task("running"), 0));
            replies.extend(vec![failure; 9]);
            replies.push((200, task("completed"), 0));
        } else {
            replies.push(failure);
        }
        let count = replies.len();
        let (client, peer) = server(replies).await;
        let result = bounded(client.wait_for_resource_task(
            "task-import",
            std::future::pending(),
            Duration::from_millis(1),
        ))
        .await;
        if recovers {
            assert_eq!(result.unwrap()["task_id"], "task-import");
        } else {
            let error = result.unwrap_err();
            assert_eq!(error.code(), "UNAVAILABLE");
            assert!(error.to_string().contains("ov task status task-import"));
        }
        let requests = bounded(peer).await.unwrap();
        assert_eq!(requests.len(), count);
        assert!(requests.iter().all(|r| r.starts_with("GET /api/v1/tasks/")));
    }
}

#[tokio::test]
async fn authentication_and_malformed_status_errors_include_recovery_command() {
    for (status, response) in [
        (
            403,
            json!({"error":{"code":"FORBIDDEN","message":"Access denied","details":{"reason":"revoked"}}}),
        ),
        (200, task("unknown")),
        (
            200,
            json!({"status":"ok","result":{"status":"completed","result":[]}}),
        ),
        (200, json!({"status":"ok","result":{"status":"completed"}})),
    ] {
        let (client, peer) = server(vec![(status, response, 0)]).await;
        let error = bounded(client.wait_for_resource_task(
            "task-import",
            std::future::pending(),
            Duration::from_millis(1),
        ))
        .await
        .unwrap_err();
        assert!(error.to_string().contains("ov task status task-import"));
        if status == 403 {
            assert_eq!(error.code(), "FORBIDDEN");
            match error {
                crate::error::Error::Api {
                    status, details, ..
                } => {
                    assert_eq!(status, Some(403));
                    assert_eq!(details.unwrap()["details"]["reason"], "revoked");
                }
                other => panic!("{other}"),
            }
        }
        assert_eq!(bounded(peer).await.unwrap().len(), 1);
    }
}

#[tokio::test]
async fn failed_tasks_are_terminal() {
    let (client, peer) = server(vec![(200, task("failed"), 0)]).await;
    let error = bounded(client.wait_for_resource_task(
        "task-import",
        std::future::pending(),
        Duration::from_millis(1),
    ))
    .await
    .unwrap_err();
    assert_eq!(error.code(), "MODEL_ERROR");
    assert!(error.to_string().contains("failed: resource processing failed"));
    assert!(!error.to_string().contains("continues"));
    assert_eq!(bounded(peer).await.unwrap().len(), 1);
}

#[tokio::test]
async fn cancelled_tasks_surface_a_cancelled_code() {
    // 使用 mark_cancelled 的真实响应形状：result 和 error 均为空。
    let (client, peer) = server(vec![(
        200,
        json!({"status":"ok","result":{"status":"cancelled","error":null,"result":null}}),
        0,
    )])
    .await;
    let error = bounded(client.wait_for_resource_task(
        "task-import",
        std::future::pending(),
        Duration::from_millis(1),
    ))
    .await
    .unwrap_err();
    assert_eq!(error.code(), "CANCELLED");
    assert!(error.to_string().contains("ov task status task-import"));
    assert!(!error.to_string().contains("cancelled: cancelled"));
    match error {
        crate::error::Error::Api { message, details, status, .. } => {
            assert_eq!(message, "Task task-import cancelled; check with: ov task status task-import");
            assert_eq!(details.unwrap(), json!({"task_id": "task-import", "status": "cancelled"}));
            assert_eq!(status, None);
        }
        other => panic!("{other}"),
    }
    assert_eq!(bounded(peer).await.unwrap().len(), 1);
}

#[tokio::test]
async fn connection_failures_are_bounded() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let client = HttpClient::new(
        format!("http://{}", listener.local_addr().unwrap()),
        None,
        None,
        None,
        None,
        1.0,
        false,
        None,
    );
    drop(listener);
    let error = bounded(client.wait_for_resource_task(
        "task-import",
        std::future::pending(),
        Duration::from_millis(1),
    ))
    .await
    .unwrap_err();
    assert_eq!(error.code(), "UNAVAILABLE");
    assert!(error.to_string().contains("ov task status task-import"));
}
