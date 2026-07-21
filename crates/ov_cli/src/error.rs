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
        if error.is_timeout() {
            Self::Timeout(format!("{context}: request timed out: {error}"))
        } else if error.is_decode() {
            Self::Parse(format!("{context}: {error}"))
        } else if error.is_builder() {
            Self::Client(format!("{context}: {error}"))
        } else {
            Self::Network(format!("{context}: {error}"))
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
