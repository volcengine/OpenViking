use super::HttpClient;
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;

// A scripted HTTP peer checks the actual request boundary: one submission,
// then independent status GETs (including a GET that times out).
async fn resource_wait_server(
    responses: Vec<(u16, serde_json::Value, u64)>,
) -> (String, tokio::task::JoinHandle<Vec<String>>) {
    let (url, server, _) = resource_wait_server_with_signal(responses).await;
    (url, server)
}

async fn bounded<T>(future: impl std::future::Future<Output = T>) -> T {
    tokio::time::timeout(std::time::Duration::from_secs(20), future)
        .await
        .expect("resource wait test exceeded 20s")
}

async fn resource_wait_server_with_signal(
    responses: Vec<(u16, serde_json::Value, u64)>,
) -> (
    String,
    tokio::task::JoinHandle<Vec<String>>,
    oneshot::Receiver<()>,
) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let (request_tx, request_rx) = oneshot::channel();
    let server = tokio::spawn(bounded(async move {
        let mut request_tx = Some(request_tx);
        let mut requests = Vec::new();
        let mut writers = Vec::new();
        for (status, body, delay_ms) in responses {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            loop {
                let mut chunk = [0; 8192];
                let size = stream.read(&mut chunk).await.unwrap();
                assert!(size > 0, "connection ended before the request body");
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
                        break;
                    }
                }
            }
            requests.push(String::from_utf8_lossy(&request).into_owned());
            if let Some(tx) = request_tx.take() {
                let _ = tx.send(());
            }
            // Delayed replies must not prevent accepting the next retry.
            writers.push(tokio::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
                let body = body.to_string();
                let response = format!(
                    "HTTP/1.1 {status} Test\r\ncontent-type: application/json\r\n\
                     content-length: {}\r\nconnection: close\r\n\r\n{body}",
                    body.len()
                );
                // A timed-out status query legitimately closes its socket.
                let _ = stream.write_all(response.as_bytes()).await;
            }));
        }
        for writer in writers {
            writer.await.unwrap();
        }
        requests
    }));
    (url, server, request_rx)
}

fn resource_task_response(status: &str) -> serde_json::Value {
    json!({"status": "ok", "result": {
        "task_id": "task-import", "status": status, "stage": "embedding",
        "error": if matches!(status, "failed" | "cancelled") {
            Some("embedding rejected")
        } else { None },
        "result": {
            "status": "success", "root_uri": "viking://resources/demo/docs",
            "context_count": 10, "code": "MODEL_ERROR",
            "details": {"model": "test"}
        }
    }})
}

async fn submit_test_resource(
    client: &HttpClient,
    path: &str,
    add_type: Option<String>,
    wait: bool,
) -> crate::error::Result<serde_json::Value> {
    submit_test_resource_with_to(client, path, add_type, wait, None).await
}

async fn submit_test_resource_with_to(
    client: &HttpClient,
    path: &str,
    add_type: Option<String>,
    wait: bool,
    to: Option<String>,
) -> crate::error::Result<serde_json::Value> {
    let parent = to.is_none().then(|| "viking://resources/demo".into());
    bounded(client.add_resource(
        path,
        add_type,
        to,
        None,
        parent,
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
async fn add_resource_wait_polls_local_file_directory_url_and_connector() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("doc.md");
    std::fs::write(&file, "# Example").unwrap();
    for (path, add_type, local) in [
        (file.to_str().unwrap(), None, true),
        (dir.path().to_str().unwrap(), None, true),
        ("https://example.com/docs", None, false),
        ("tos://bucket/docs", Some("tos".to_string()), false),
    ] {
        let mut responses = Vec::new();
        if local {
            responses.push((
                200,
                json!({
                    "status":"ok", "result":{"temp_file_id":"upload-1"}
                }),
                0,
            ));
        }
        responses.extend([
            (
                200,
                json!({"status":"ok", "result":{"task_id":"task-import"}}),
                0,
            ),
            (200, resource_task_response("pending"), 0),
            (200, resource_task_response("running"), 0),
            (200, resource_task_response("completed"), 0),
        ]);
        let (url, server) = resource_wait_server(responses).await;
        // Two polling intervals exceed the one-second per-request timeout.
        let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
        let result = submit_test_resource(&client, path, add_type, true)
            .await
            .unwrap();
        assert_eq!(result["task_id"], "task-import");
        assert_eq!(result["context_count"], 10);
        let requests = server.await.unwrap();
        let imports: Vec<_> = requests
            .iter()
            .filter(|r| r.starts_with("POST /api/v1/resources HTTP"))
            .collect();
        assert_eq!(imports.len(), 1, "must never resubmit the import");
        let body: serde_json::Value =
            serde_json::from_str(imports[0].split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["wait"], false);
        assert_eq!(body["create_parent"], true);
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
async fn add_resource_wait_retries_status_timeout_without_resubmission() {
    let (url, server) = resource_wait_server(vec![
        (
            200,
            json!({"status":"ok", "result":{"task_id":"task-import"}}),
            0,
        ),
        (200, resource_task_response("running"), 3000),
        (200, resource_task_response("completed"), 0),
    ])
    .await;
    let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
    let result = submit_test_resource(&client, "https://example.com/docs", None, true)
        .await
        .unwrap();
    assert_eq!(result["task_id"], "task-import");
    let requests = server.await.unwrap();
    assert_eq!(requests.len(), 3);
    assert!(requests[0].starts_with("POST "));
    assert!(
        requests[1..]
            .iter()
            .all(|r| r.starts_with("GET /api/v1/tasks/"))
    );
}

#[tokio::test]
async fn add_resource_wait_reports_failed_and_cancelled_tasks() {
    for status in ["failed", "cancelled"] {
        let (url, server) = resource_wait_server(vec![
            (
                200,
                json!({"status":"ok", "result":{"task_id":"task-import"}}),
                0,
            ),
            (200, resource_task_response(status), 0),
        ])
        .await;
        let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
        let error = submit_test_resource(&client, "https://example.com/docs", None, true)
            .await
            .unwrap_err();
        match error {
            crate::error::Error::Api {
                message,
                code,
                details,
                ..
            } => {
                assert!(message.contains(status));
                assert_eq!(code.as_deref(), Some("MODEL_ERROR"));
                assert_eq!(details.unwrap()["task_id"], "task-import");
            }
            other => panic!("unexpected error: {other}"),
        }
        assert_eq!(server.await.unwrap().len(), 2);
    }
}

#[tokio::test]
async fn add_resource_wait_does_not_retry_auth_failure() {
    let (url, server) = resource_wait_server(vec![
        (
            200,
            json!({"status":"ok", "result":{"task_id":"task-import"}}),
            0,
        ),
        (
            403,
            json!({"status":"error", "error":{
                "code":"FORBIDDEN", "message":"Access denied"
            }}),
            0,
        ),
    ])
    .await;
    let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
    let error = submit_test_resource(&client, "https://example.com/docs", None, true)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("ov task status task-import"));
    match error {
        crate::error::Error::TaskWait { source, .. } => assert!(matches!(
            *source,
            crate::error::Error::Api {
                status: Some(403),
                ..
            }
        )),
        other => panic!("unexpected error: {other}"),
    }
    assert_eq!(server.await.unwrap().len(), 2);
}

#[tokio::test]
async fn add_resource_wait_interrupts_an_inflight_status_query() {
    let (url, server, request_rx) =
        resource_wait_server_with_signal(vec![(200, resource_task_response("running"), 3000)])
            .await;
    let client = HttpClient::new(url, None, None, None, None, 5.0, false, None);
    let error = bounded(client.wait_for_resource_task("task-import", async {
        request_rx.await.unwrap();
        Ok(())
    }))
    .await
    .unwrap_err();
    assert!(matches!(error, crate::error::Error::Interrupted { .. }));
    assert!(error.to_string().contains("ov task status task-import"));
    let requests = server.await.unwrap();
    assert_eq!(requests.len(), 1);
    assert!(
        requests[0].starts_with("GET "),
        "must not cancel the server task"
    );
}

#[tokio::test]
async fn add_resource_without_wait_returns_task_without_polling() {
    let (url, server) = resource_wait_server(vec![(
        200,
        json!({"status":"ok", "result":{"task_id":"task-import"}}),
        0,
    )])
    .await;
    let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
    let result = submit_test_resource(&client, "https://example.com/docs", None, false)
        .await
        .unwrap();
    assert_eq!(result["task_id"], "task-import");
    assert_eq!(server.await.unwrap().len(), 1);
}

#[tokio::test]
async fn add_resource_wait_retries_gateway_errors_and_rate_limits() {
    for status in [429, 502, 503, 504] {
        let (url, server) = resource_wait_server(vec![
            (
                200,
                json!({"status":"ok", "result":{"task_id":"task-import"}}),
                0,
            ),
            (status, json!({"error":{"message":"try again"}}), 0),
            (200, resource_task_response("completed"), 0),
        ])
        .await;
        let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
        let result = submit_test_resource_with_to(
            &client,
            "https://example.com/docs",
            None,
            true,
            Some("viking://resources/demo/docs".into()),
        )
        .await
        .unwrap();
        assert_eq!(result["task_id"], "task-import");
        let requests = server.await.unwrap();
        assert_eq!(requests.len(), 3);
        assert!(requests[0].starts_with("POST /api/v1/resources HTTP"));
        assert!(
            requests[1..]
                .iter()
                .all(|r| r.starts_with("GET /api/v1/tasks/"))
        );
        let body: serde_json::Value =
            serde_json::from_str(requests[0].split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["wait"], false);
        assert_eq!(body["to"], "viking://resources/demo/docs");
        assert!(body.get("parent").is_none());
    }
}

fn fast_resource_wait_policy() -> super::ResourceWaitPolicy {
    // Keep the production failure limit; shorten only scheduling delays.
    super::ResourceWaitPolicy {
        retry_delay: std::time::Duration::from_millis(1),
        max_retry_delay: std::time::Duration::from_millis(5),
        poll_interval: std::time::Duration::from_millis(1),
        ..Default::default()
    }
}

#[tokio::test]
async fn add_resource_wait_bounds_consecutive_errors_and_resets_after_success() {
    for recovers in [false, true] {
        let policy = fast_resource_wait_policy();
        let failure = (
            503,
            json!({"error":{"code":"UNAVAILABLE","message":"offline"}}),
            0,
        );
        let mut responses = vec![failure.clone(); policy.max_failures - 1];
        if recovers {
            responses.push((200, resource_task_response("running"), 0));
            responses.extend(vec![failure; policy.max_failures - 1]);
            responses.push((200, resource_task_response("completed"), 0));
        } else {
            responses.push(failure);
        }
        let expected_requests = responses.len();
        let (url, server) = resource_wait_server(responses).await;
        let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
        let result = bounded(client.wait_for_resource_task_with_policy(
            "task-import",
            std::future::pending(),
            policy,
        ))
        .await;
        if recovers {
            assert_eq!(result.unwrap()["task_id"], "task-import");
        } else {
            let error = result.unwrap_err();
            assert_eq!(error.code(), "UNAVAILABLE");
            assert!(error.to_string().contains("ov task status task-import"));
            match error {
                crate::error::Error::TaskWait { source, .. } => assert!(matches!(
                    *source,
                    crate::error::Error::Api {
                        status: Some(503),
                        ..
                    }
                )),
                other => panic!("unexpected error: {other}"),
            }
        }
        let requests = server.await.unwrap();
        assert_eq!(requests.len(), expected_requests);
        assert!(requests.iter().all(|r| r.starts_with("GET /api/v1/tasks/")));
    }
}

#[tokio::test]
async fn add_resource_wait_bounds_connection_failures() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
    let error = bounded(client.wait_for_resource_task_with_policy(
        "task-import",
        std::future::pending(),
        fast_resource_wait_policy(),
    ))
    .await
    .unwrap_err();
    assert_eq!(error.code(), "UNAVAILABLE");
    assert!(error.to_string().contains("ov task status task-import"));
}

#[tokio::test]
async fn add_resource_wait_malformed_status_has_task_recovery_guidance() {
    for task in [
        json!({"status":"ok","result":{"status":"unknown"}}),
        json!({"status":"ok","result":{"status":"completed"}}),
        json!({"status":"ok","result":{"status":"completed","result":[]}}),
    ] {
        let (url, server) = resource_wait_server(vec![
            (
                200,
                json!({"status":"ok", "result":{"task_id":"task-import"}}),
                0,
            ),
            (200, task, 0),
        ])
        .await;
        let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
        let error = submit_test_resource(&client, "https://example.com/docs", None, true)
            .await
            .unwrap_err();
        assert!(error.to_string().contains("ov task status task-import"));
        assert_eq!(server.await.unwrap().len(), 2);
    }
}

#[tokio::test]
async fn add_resource_submission_timeout_warns_before_resubmission() {
    for wait in [false, true] {
        let (url, server) = resource_wait_server(vec![(
            200,
            json!({"status":"ok", "result":{"task_id":"task-import"}}),
            3000,
        )])
        .await;
        let client = HttpClient::new(url, None, None, None, None, 1.0, false, None);
        let error = submit_test_resource(&client, "https://example.com/docs", None, wait)
            .await
            .unwrap_err();
        assert_eq!(error.code(), "DEADLINE_EXCEEDED");
        assert!(
            error
                .to_string()
                .contains("check ov task list before resubmitting")
        );
        let requests = server.await.unwrap();
        assert_eq!(requests.len(), 1);
        assert!(requests[0].starts_with("POST /api/v1/resources HTTP"));
    }
}
