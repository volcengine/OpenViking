//! Verify the actual S3 HTTP requests, including NotFound and range semantics.

use super::*;
use crate::core::ConfigValue;
use std::collections::HashMap;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::task::JoinHandle;
use tokio::time::{timeout, Duration};

struct Server {
    endpoint: String,
    requests: Arc<Mutex<Vec<String>>>,
    task: JoinHandle<()>,
}

impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn server(responses: Vec<(&'static str, &'static str)>) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let requests = Arc::new(Mutex::new(Vec::new()));
    let captured = requests.clone();
    let task = tokio::spawn(async move {
        for (status, body) in responses {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            let mut buffer = [0; 1024];
            while !request.ends_with(b"\r\n\r\n") {
                let count = socket.read(&mut buffer).await.unwrap();
                assert!(count > 0 && request.len() < 16_384);
                request.extend_from_slice(&buffer[..count]);
            }
            let request = String::from_utf8(request).unwrap();
            let head = request.starts_with("HEAD ");
            captured.lock().unwrap().push(request);
            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Length: {}\r\nContent-Type: application/xml\r\nConnection: close\r\n\r\n{}",
                body.len(),
                if head { "" } else { body },
            );
            socket.write_all(response.as_bytes()).await.unwrap();
        }
    });
    Server {
        endpoint,
        requests,
        task,
    }
}

async fn filesystem(server: &Server) -> S3FileSystem {
    let params = HashMap::from([
        ("bucket".into(), ConfigValue::String("test-bucket".into())),
        (
            "endpoint".into(),
            ConfigValue::String(server.endpoint.clone()),
        ),
        ("region".into(), ConfigValue::String("us-east-1".into())),
        (
            "access_key_id".into(),
            ConfigValue::String("test-access".into()),
        ),
        (
            "secret_access_key".into(),
            ConfigValue::String("test-secret".into()),
        ),
        ("prefix".into(), ConfigValue::String("tenant".into())),
        ("cache_enabled".into(), ConfigValue::Bool(false)),
    ]);
    let client = S3Client::new(&params).await.unwrap();
    S3FileSystem {
        client: Arc::new(client),
        dir_cache: S3ListDirCache::new(10, 30, false),
        stat_cache: S3StatCache::new(10, 30, false),
    }
}

const NOT_FOUND: &str = "<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>";
const EMPTY_LIST: &str =
    "<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><KeyCount>0</KeyCount></ListBucketResult>";

#[tokio::test]
async fn reserved_lock_reads_use_one_get_and_preserve_missing_tokens() {
    for name in [".path.ovlock", ".exact.ovlock.note.md.0123456789abcdef"] {
        let server = server(vec![("404 Not Found", NOT_FOUND)]).await;
        let fs = filesystem(&server).await;
        let error = timeout(
            Duration::from_secs(3),
            fs.read(&format!("/dir/{name}"), 0, 0),
        )
        .await
        .unwrap()
        .unwrap_err();
        assert!(matches!(error, Error::NotFound(_)));
        let requests = server.requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert!(requests[0].starts_with(&format!("GET /test-bucket/tenant/dir/{name}")));
    }
}

#[tokio::test]
async fn reserved_lock_full_and_range_reads_keep_get_semantics() {
    for (offset, size, body, range) in [
        (0, 0, "token", None),
        (2, 3, "ken", Some("range: bytes=2-4")),
        (2, 0, "ken", Some("range: bytes=2-")),
    ] {
        let server = server(vec![("200 OK", body)]).await;
        let fs = filesystem(&server).await;
        let bytes = timeout(
            Duration::from_secs(3),
            fs.read("/dir/.path.ovlock", offset, size),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(bytes, body.as_bytes());
        let requests = server.requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert!(requests[0].starts_with("GET "));
        if let Some(range) = range {
            assert!(requests[0].to_lowercase().contains(range));
        }
    }
}

#[tokio::test]
async fn reserved_lock_access_denied_remains_an_error_without_directory_probes() {
    let server = server(vec![(
        "403 Forbidden",
        "<Error><Code>AccessDenied</Code><Message>denied</Message></Error>",
    )])
    .await;
    let fs = filesystem(&server).await;
    let error = timeout(Duration::from_secs(3), fs.read("/dir/.path.ovlock", 0, 0))
        .await
        .unwrap()
        .unwrap_err();
    assert!(!matches!(error, Error::NotFound(_)));
    assert_eq!(server.requests.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn ordinary_file_names_keep_directory_disambiguation() {
    // The similarly named file is not reserved; retain HEAD(marker), LIST, GET.
    let server = server(vec![
        ("404 Not Found", NOT_FOUND),
        ("200 OK", EMPTY_LIST),
        ("200 OK", "content"),
    ])
    .await;
    let fs = filesystem(&server).await;
    let bytes = timeout(Duration::from_secs(3), fs.read("/dir/.exact.ovlock", 0, 0))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(bytes, b"content");
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 3);
    assert!(requests[0].starts_with("HEAD "));
    assert!(requests[1].contains("list-type=2"));
    assert!(requests[2].starts_with("GET /test-bucket/tenant/dir/.exact.ovlock"));
}

#[tokio::test]
async fn ordinary_directory_reads_still_return_is_a_directory() {
    let server = server(vec![("200 OK", ""), ("404 Not Found", NOT_FOUND)]).await;
    let fs = filesystem(&server).await;
    let error = timeout(Duration::from_secs(3), fs.read("/dir", 0, 0))
        .await
        .unwrap()
        .unwrap_err();
    assert!(matches!(error, Error::IsADirectory(_)));
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 2);
    assert!(requests.iter().all(|request| request.starts_with("HEAD ")));
}
