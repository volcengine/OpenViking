use crate::client::HttpClient;
use crate::error::{Error, Result};
use crate::output::{OutputFormat, output_success};
use serde::Serialize;
use serde_json::Value;
use std::future::Future;
use std::time::Instant;
use uuid::Uuid;

const STAGE_NAMES: [&str; 5] = ["write", "stat", "find", "cleanup", "absence"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct StageResult {
    name: &'static str,
    ok: bool,
    detail: String,
}

impl StageResult {
    fn pending(name: &'static str) -> Self {
        Self {
            name,
            ok: false,
            detail: "not run".to_string(),
        }
    }

    fn pass(name: &'static str, detail: impl Into<String>) -> Self {
        Self {
            name,
            ok: true,
            detail: detail.into(),
        }
    }

    fn fail(name: &'static str, detail: impl Into<String>) -> Self {
        Self {
            name,
            ok: false,
            detail: detail.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct VerifyRetrievalReport {
    ok: bool,
    uri: String,
    elapsed_ms: u64,
    stages: Vec<StageResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    retained_uri: Option<String>,
}

trait RetrievalProbeOperations {
    fn create<'a>(
        &'a self,
        uri: &'a str,
        content: &'a str,
        timeout: f64,
    ) -> impl Future<Output = Result<Value>> + 'a;

    fn stat<'a>(&'a self, uri: &'a str) -> impl Future<Output = Result<Value>> + 'a;

    fn find<'a>(
        &'a self,
        marker: &'a str,
        uri: &'a str,
    ) -> impl Future<Output = Result<Value>> + 'a;

    fn cleanup<'a>(
        &'a self,
        uri: &'a str,
        timeout: f64,
    ) -> impl Future<Output = Result<Value>> + 'a;
}

impl RetrievalProbeOperations for HttpClient {
    async fn create(&self, uri: &str, content: &str, timeout: f64) -> Result<Value> {
        self.write(
            uri,
            content,
            "create",
            true,
            Some(timeout),
            "semantic_and_vectors",
            Vec::new(),
            "replace",
        )
        .await
    }

    async fn stat(&self, uri: &str) -> Result<Value> {
        self.stat(uri).await
    }

    async fn find(&self, marker: &str, uri: &str) -> Result<Value> {
        self.find(
            marker.to_string(),
            uri.to_string(),
            None,
            10,
            None,
            None,
            None,
            None,
            None,
            Some(vec!["resource".to_string()]),
            None,
            false,
        )
        .await
    }

    async fn cleanup(&self, uri: &str, timeout: f64) -> Result<Value> {
        self.rm(uri, true, true, Some(timeout)).await
    }
}

pub async fn run(
    client: &HttpClient,
    timeout: f64,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let id = Uuid::new_v4().simple().to_string();
    let uri = format!("viking://~/resources/openviking-retrieval-check-{id}.md");
    let marker = format!("openviking-retrieval-check-{id}");
    let content = format!("# OpenViking retrieval verification\n\nMarker: {marker}\n");
    let started_at = Instant::now();
    let mut report = execute(client, &uri, &marker, &content, timeout).await;
    report.elapsed_ms = started_at
        .elapsed()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX);

    output_report(&report, output_format, compact);
    if report.ok {
        Ok(())
    } else {
        Err(Error::AlreadyReported)
    }
}

async fn execute<T: RetrievalProbeOperations>(
    operations: &T,
    requested_uri: &str,
    marker: &str,
    content: &str,
    timeout: f64,
) -> VerifyRetrievalReport {
    let mut stages: Vec<StageResult> = STAGE_NAMES.into_iter().map(StageResult::pending).collect();
    let mut canonical_uri = requested_uri.to_string();

    let write_succeeded = match operations.create(requested_uri, content, timeout).await {
        Ok(value) => {
            if let Some(detail) = processing_failure_detail(&value) {
                stages[0] = StageResult::fail("write", detail);
                stages[2].detail = "not run because resource processing failed".to_string();
                false
            } else {
                stages[0] = StageResult::pass("write", "resource processing completed");
                true
            }
        }
        Err(error) => {
            stages[0] = StageResult::fail("write", error_detail(&error));
            stages[2].detail = "not run because the write result was uncertain".to_string();
            false
        }
    };

    match operations.stat(requested_uri).await {
        Ok(value) => match value
            .get("uri")
            .and_then(Value::as_str)
            .filter(|uri| is_expected_canonical_uri(uri, requested_uri))
        {
            Some(uri) => {
                canonical_uri = uri.to_string();
                stages[1] = StageResult::pass(
                    "stat",
                    if write_succeeded {
                        "canonical URI resolved"
                    } else {
                        "resource exists after the write stage did not pass"
                    },
                );
            }
            None => {
                stages[1] = StageResult::fail(
                    "stat",
                    "response did not contain the expected canonical URI",
                );
            }
        },
        Err(error) if !write_succeeded && error.code() == "NOT_FOUND" => {
            stages[1] = StageResult::fail("stat", "resource is absent after the write failure");
            stages[3].detail = "not run because the resource is absent".to_string();
            stages[4] = StageResult::pass("absence", "filesystem resource is absent");
            return report(requested_uri.to_string(), stages, None);
        }
        Err(error) => stages[1] = StageResult::fail("stat", error_detail(&error)),
    }

    if write_succeeded && stages[1].ok {
        match operations.find(marker, &canonical_uri).await {
            Ok(value) if contains_resource_uri(&value, &canonical_uri) => {
                stages[2] = StageResult::pass("find", "exact resource URI was returned");
            }
            Ok(_) => {
                stages[2] = StageResult::fail("find", "exact resource URI was not returned");
            }
            Err(error) => stages[2] = StageResult::fail("find", error_detail(&error)),
        }
    } else if write_succeeded {
        stages[2].detail = "not run because the canonical URI was not verified".to_string();
    }

    stages[3] = match operations.cleanup(requested_uri, timeout).await {
        Ok(_) => StageResult::pass("cleanup", "remove request completed"),
        Err(error) => StageResult::fail("cleanup", error_detail(&error)),
    };

    match operations.stat(requested_uri).await {
        Err(error) if error.code() == "NOT_FOUND" => {
            stages[4] = StageResult::pass("absence", "filesystem resource is absent");
        }
        Err(error) => stages[4] = StageResult::fail("absence", error_detail(&error)),
        Ok(_) => {
            stages[4] = StageResult::fail("absence", "filesystem resource still exists");
        }
    }

    let retained_uri = (!stages[4].ok).then(|| canonical_uri.clone());
    report(canonical_uri, stages, retained_uri)
}

fn processing_failure_detail(value: &Value) -> Option<String> {
    let failed_stages: Vec<&str> = ["semantic_status", "vector_status"]
        .into_iter()
        .filter(|name| value.get(name).and_then(Value::as_str) == Some("failed"))
        .collect();

    (!failed_stages.is_empty())
        .then(|| format!("resource processing failed: {}", failed_stages.join(", ")))
}

fn contains_resource_uri(value: &Value, expected_uri: &str) -> bool {
    value
        .get("resources")
        .and_then(Value::as_array)
        .is_some_and(|resources| {
            resources
                .iter()
                .any(|resource| resource.get("uri").and_then(Value::as_str) == Some(expected_uri))
        })
}

fn is_expected_canonical_uri(canonical_uri: &str, requested_uri: &str) -> bool {
    let Some(filename) = requested_uri.rsplit('/').next() else {
        return false;
    };
    canonical_uri.starts_with("viking://user/")
        && canonical_uri.ends_with(&format!("/resources/{filename}"))
}

fn error_detail(error: &Error) -> String {
    format!("{}: {error}", error.code())
}

fn report(
    uri: String,
    stages: Vec<StageResult>,
    retained_uri: Option<String>,
) -> VerifyRetrievalReport {
    VerifyRetrievalReport {
        ok: stages.iter().all(|stage| stage.ok),
        uri,
        elapsed_ms: 0,
        stages,
        retained_uri,
    }
}

fn output_report(report: &VerifyRetrievalReport, output_format: OutputFormat, compact: bool) {
    if matches!(output_format, OutputFormat::Json) {
        if compact {
            println!("{}", serde_json::to_string(report).unwrap_or_default());
        } else {
            println!(
                "{}",
                serde_json::to_string_pretty(report).unwrap_or_default()
            );
        }
        return;
    }

    output_success(&report.stages, OutputFormat::Table, compact);
    println!("overall  {}", if report.ok { "passed" } else { "failed" });
    println!("uri      {}", report.uri);
    println!("elapsed  {} ms", report.elapsed_ms);
    if let Some(uri) = &report.retained_uri {
        println!("retained {}", uri);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::Mutex;

    #[derive(Default)]
    struct MockOperations {
        create: Mutex<VecDeque<Result<Value>>>,
        stat: Mutex<VecDeque<Result<Value>>>,
        find: Mutex<VecDeque<Result<Value>>>,
        cleanup: Mutex<VecDeque<Result<Value>>>,
        calls: Mutex<Vec<String>>,
    }

    impl MockOperations {
        const REQUESTED_URI: &'static str = "viking://~/resources/check.md";
        const CANONICAL_URI: &'static str = "viking://user/alice/resources/check.md";

        fn success(uri: &str) -> Self {
            Self {
                create: Mutex::new(VecDeque::from([Ok(serde_json::json!({"uri": uri}))])),
                stat: Mutex::new(VecDeque::from([
                    Ok(serde_json::json!({"uri": uri})),
                    Err(not_found()),
                ])),
                find: Mutex::new(VecDeque::from([Ok(serde_json::json!({
                    "resources": [{"uri": uri}]
                }))])),
                cleanup: Mutex::new(VecDeque::from([Ok(serde_json::json!({}))])),
                calls: Mutex::new(Vec::new()),
            }
        }

        fn take(queue: &Mutex<VecDeque<Result<Value>>>) -> Result<Value> {
            queue.lock().unwrap().pop_front().expect("mock result")
        }

        async fn run(&self) -> VerifyRetrievalReport {
            execute(self, Self::REQUESTED_URI, "marker", "body", 60.0).await
        }
    }

    impl RetrievalProbeOperations for MockOperations {
        async fn create(&self, uri: &str, _content: &str, _timeout: f64) -> Result<Value> {
            self.calls.lock().unwrap().push(format!("create:{uri}"));
            Self::take(&self.create)
        }

        async fn stat(&self, uri: &str) -> Result<Value> {
            self.calls.lock().unwrap().push(format!("stat:{uri}"));
            Self::take(&self.stat)
        }

        async fn find(&self, marker: &str, uri: &str) -> Result<Value> {
            self.calls
                .lock()
                .unwrap()
                .push(format!("find:{marker}:{uri}"));
            Self::take(&self.find)
        }

        async fn cleanup(&self, uri: &str, _timeout: f64) -> Result<Value> {
            self.calls.lock().unwrap().push(format!("cleanup:{uri}"));
            Self::take(&self.cleanup)
        }
    }

    fn not_found() -> Error {
        Error::api_response(Some("NOT_FOUND".to_string()), "missing", None, 404)
    }

    fn server_error(message: &str) -> Error {
        Error::api_response(Some("INTERNAL".to_string()), message, None, 500)
    }

    #[tokio::test]
    async fn exact_uri_result_passes_and_not_found_confirms_absence() {
        let operations = MockOperations::success(MockOperations::CANONICAL_URI);
        let result = operations.run().await;

        assert!(result.ok);
        assert_eq!(result.uri, MockOperations::CANONICAL_URI);
        assert!(result.stages.iter().all(|stage| stage.ok));
        assert_eq!(result.retained_uri, None);
        assert_eq!(
            *operations.calls.lock().unwrap(),
            vec![
                "create:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
                "find:marker:viking://user/alice/resources/check.md",
                "cleanup:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
            ]
        );
    }

    #[tokio::test]
    async fn failed_processing_statuses_fail_write_and_still_clean_up() {
        for failed_field in ["semantic_status", "vector_status"] {
            let operations = MockOperations::success(MockOperations::CANONICAL_URI);
            *operations.create.lock().unwrap() = VecDeque::from([Ok(serde_json::json!({
                "uri": MockOperations::CANONICAL_URI,
                "semantic_status": if failed_field == "semantic_status" {
                    "failed"
                } else {
                    "complete"
                },
                "vector_status": if failed_field == "vector_status" {
                    "failed"
                } else {
                    "complete"
                }
            }))]);

            let result = operations.run().await;

            assert!(!result.ok, "{failed_field} failure must fail the probe");
            assert!(!result.stages[0].ok);
            assert!(result.stages[0].detail.contains(failed_field));
            assert_eq!(
                result.stages[2].detail,
                "not run because resource processing failed"
            );
            assert!(result.stages[3].ok);
            assert!(result.stages[4].ok);
            assert_eq!(result.retained_uri, None);
            assert_eq!(
                *operations.calls.lock().unwrap(),
                vec![
                    "create:viking://~/resources/check.md",
                    "stat:viking://~/resources/check.md",
                    "cleanup:viking://~/resources/check.md",
                    "stat:viking://~/resources/check.md",
                ],
                "unexpected calls for {failed_field}"
            );
        }
    }

    #[tokio::test]
    async fn create_failure_confirms_that_the_resource_is_absent() {
        let operations = MockOperations {
            create: Mutex::new(VecDeque::from([Err(server_error("write failed"))])),
            stat: Mutex::new(VecDeque::from([Err(not_found())])),
            ..Default::default()
        };

        let result = operations.run().await;

        assert!(!result.ok);
        assert!(!result.stages[0].ok);
        assert_eq!(
            result.stages[1].detail,
            "resource is absent after the write failure"
        );
        assert_eq!(
            result.stages[2].detail,
            "not run because the write result was uncertain"
        );
        assert_eq!(
            result.stages[3].detail,
            "not run because the resource is absent"
        );
        assert!(result.stages[4].ok);
        assert_eq!(result.retained_uri, None);
        assert_eq!(
            *operations.calls.lock().unwrap(),
            vec![
                "create:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
            ]
        );
    }

    #[tokio::test]
    async fn ambiguous_create_failure_cleans_up_resource_that_exists() {
        let operations = MockOperations {
            create: Mutex::new(VecDeque::from([Err(server_error("response lost"))])),
            stat: Mutex::new(VecDeque::from([
                Ok(serde_json::json!({"uri": MockOperations::CANONICAL_URI})),
                Err(not_found()),
            ])),
            cleanup: Mutex::new(VecDeque::from([Ok(serde_json::json!({}))])),
            ..Default::default()
        };

        let result = operations.run().await;

        assert!(!result.ok);
        assert!(!result.stages[0].ok);
        assert!(result.stages[1].ok);
        assert_eq!(
            result.stages[2].detail,
            "not run because the write result was uncertain"
        );
        assert!(result.stages[3].ok);
        assert!(result.stages[4].ok);
        assert_eq!(result.retained_uri, None);
        assert_eq!(
            *operations.calls.lock().unwrap(),
            vec![
                "create:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
                "cleanup:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
            ]
        );
    }

    #[tokio::test]
    async fn invalid_canonical_uri_skips_find_and_still_cleans_up() {
        let operations = MockOperations::success(MockOperations::CANONICAL_URI);
        *operations.stat.lock().unwrap() = VecDeque::from([
            Ok(serde_json::json!({
                "uri": "viking://user/alice/resources/other.md"
            })),
            Err(not_found()),
        ]);

        let result = operations.run().await;

        assert!(!result.ok);
        assert!(!result.stages[1].ok);
        assert_eq!(
            result.stages[2].detail,
            "not run because the canonical URI was not verified"
        );
        assert!(result.stages[3].ok);
        assert!(result.stages[4].ok);
        assert_eq!(result.retained_uri, None);
        assert_eq!(
            *operations.calls.lock().unwrap(),
            vec![
                "create:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
                "cleanup:viking://~/resources/check.md",
                "stat:viking://~/resources/check.md",
            ]
        );
    }

    #[tokio::test]
    async fn missing_exact_retrieval_result_still_runs_cleanup() {
        let operations = MockOperations::success(MockOperations::CANONICAL_URI);
        *operations.find.lock().unwrap() = VecDeque::from([Ok(serde_json::json!({
            "resources": [{"uri": "viking://user/alice/resources/other.md"}]
        }))]);

        let result = operations.run().await;

        assert!(!result.ok);
        assert!(!result.stages[2].ok);
        assert!(result.stages[3].ok);
        assert!(result.stages[4].ok);
        assert!(
            operations
                .calls
                .lock()
                .unwrap()
                .iter()
                .any(|call| call.starts_with("cleanup:"))
        );
    }

    #[tokio::test]
    async fn cleanup_failure_reports_retained_uri() {
        let operations = MockOperations::success(MockOperations::CANONICAL_URI);
        *operations.cleanup.lock().unwrap() = VecDeque::from([Err(server_error("remove failed"))]);
        *operations.stat.lock().unwrap() = VecDeque::from([
            Ok(serde_json::json!({"uri": MockOperations::CANONICAL_URI})),
            Ok(serde_json::json!({"uri": MockOperations::CANONICAL_URI})),
        ]);

        let result = operations.run().await;

        assert!(!result.ok);
        assert!(!result.stages[3].ok);
        assert!(!result.stages[4].ok);
        assert_eq!(
            result.retained_uri.as_deref(),
            Some(MockOperations::CANONICAL_URI)
        );
    }
}
