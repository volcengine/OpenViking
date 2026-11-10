use serde_json::Value;
use thiserror::Error;

#[derive(Error, Debug)]
pub enum Error {
    #[error("No ovcli.conf detected. Run ov config to create one before using server commands.")]
    MissingConfig,

    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Language error: {0}")]
    Language(String),

    #[error("Network error: {0}")]
    Network(String),

    #[error("Request timeout: {0}")]
    Timeout(String),

    #[error("API error: {message}")]
    Api {
        code: Option<String>,
        message: String,
        details: Option<Value>,
        status: Option<u16>,
    },

    #[error("Client error: {0}")]
    Client(String),

    #[error("Parse error: {0}")]
    Parse(String),

    #[error("Output error: {0}")]
    Output(String),

    #[error("Invalid path: {0}")]
    InvalidPath(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Zip error: {0}")]
    Zip(#[from] zip::result::ZipError),

    #[error("already reported")]
    AlreadyReported,
}

impl Error {
    pub fn api(message: impl Into<String>) -> Self {
        Self::Api {
            code: None,
            message: message.into(),
            details: None,
            status: None,
        }
    }

    pub fn api_with_status(message: impl Into<String>, status: u16) -> Self {
        Self::Api {
            code: None,
            message: message.into(),
            details: None,
            status: Some(status),
        }
    }

    pub fn api_response(
        code: Option<String>,
        message: impl Into<String>,
        details: Option<Value>,
        status: u16,
    ) -> Self {
        Self::Api {
            code,
            message: message.into(),
            details,
            status: Some(status),
        }
    }

    pub(crate) fn from_reqwest(context: &str, error: reqwest::Error) -> Self {
        let detail = with_sources(&error);
        if error.is_timeout() {
            Self::Timeout(format!("{context}: request timed out: {detail}"))
        } else if error.is_decode() {
            Self::Parse(format!("{context}: {detail}"))
        } else if error.is_builder() {
            Self::Client(format!("{context}: {detail}"))
        } else {
            Self::Network(format!("{context}: {detail}"))
        }
    }

    pub(crate) fn code(&self) -> &str {
        match self {
            Self::MissingConfig | Self::Config(_) => "FAILED_PRECONDITION",
            Self::Language(_) | Self::Client(_) | Self::InvalidPath(_) => "INVALID_ARGUMENT",
            Self::Network(_) => "UNAVAILABLE",
            Self::Timeout(_) => "DEADLINE_EXCEEDED",
            Self::Api { code, status, .. } => code
                .as_deref()
                .unwrap_or_else(|| code_from_http_status(*status)),
            Self::Parse(_)
            | Self::Output(_)
            | Self::Io(_)
            | Self::Serialization(_)
            | Self::Zip(_)
            | Self::AlreadyReported => "INTERNAL",
        }
    }
}

/// reqwest 0.12 / hyper 1.x: Display omits the cause, which lives in source().
fn with_sources(error: &dyn std::error::Error) -> String {
    let mut message = error.to_string();
    let mut source = error.source();
    while let Some(cause) = source {
        let text = cause.to_string();
        if !message.ends_with(&text) {
            message.push_str(": ");
            message.push_str(&text);
        }
        source = cause.source();
    }
    message
}

fn code_from_http_status(status: Option<u16>) -> &'static str {
    match status {
        Some(400 | 422) => "INVALID_ARGUMENT",
        Some(401) => "UNAUTHENTICATED",
        Some(403) => "PERMISSION_DENIED",
        Some(404) => "NOT_FOUND",
        Some(409) => "CONFLICT",
        Some(412) => "FAILED_PRECONDITION",
        Some(429) => "RESOURCE_EXHAUSTED",
        Some(502 | 503) => "UNAVAILABLE",
        Some(504) => "DEADLINE_EXCEEDED",
        _ => "INTERNAL",
    }
}

pub type Result<T> = std::result::Result<T, Error>;

#[cfg(test)]
mod tests {
    use super::*;
    use std::error::Error as _;

    #[test]
    fn source_chain_format_avoids_duplicate_suffixes() {
        #[derive(Debug, thiserror::Error)]
        #[error("{message}")]
        struct ChainedError {
            message: &'static str,
            #[source]
            cause: std::io::Error,
        }
        for message in ["outer", "outer: inner"] {
            let error = ChainedError {
                message,
                cause: std::io::Error::other("inner"),
            };
            assert_eq!(with_sources(&error), "outer: inner");
        }
    }

    #[tokio::test]
    async fn from_reqwest_reports_the_underlying_cause() {
        let port = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        let error = reqwest::Client::builder()
            .no_proxy()
            .build()
            .unwrap()
            .get(format!("http://127.0.0.1:{port}/health"))
            .send()
            .await
            .unwrap_err();
        let mut root_cause = String::new();
        let mut source = error.source();
        while let Some(cause) = source {
            root_cause = cause.to_string();
            source = cause.source();
        }
        assert!(!root_cause.is_empty());

        let message = Error::from_reqwest("HTTP request failed", error).to_string();

        assert!(message.contains(&root_cause), "{message}");
        assert_eq!(message.matches(root_cause.as_str()).count(), 1);
    }
}
