use thiserror::Error;

#[derive(Error, Debug)]
pub enum Error {
    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Network error: {0}")]
    Network(String),

    #[error("API error: {0}")]
    Api(String),

    #[error("Client error: {0}")]
    Client(String),

    #[error("Parse error: {0}")]
    Parse(String),

    #[error("Output error: {0}")]
    Output(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

/// CLI-specific error type for command handlers
#[derive(Error, Debug)]
#[error("{message}")]
pub struct CliError {
    pub message: String,
    pub code: String,
    pub exit_code: i32,
}

impl CliError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            code: "CLI_ERROR".to_string(),
            exit_code: 1,
        }
    }

    pub fn config(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            code: "CLI_CONFIG".to_string(),
            exit_code: 2,
        }
    }

    pub fn network(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            code: "CONNECTION_ERROR".to_string(),
            exit_code: 3,
        }
    }
}

impl From<Error> for CliError {
    fn from(err: Error) -> Self {
        match err {
            Error::Config(msg) => CliError::config(msg),
            Error::Network(msg) => CliError::network(msg),
            Error::Api(msg) => CliError::new(format!("API error: {}", msg)),
            Error::Client(msg) => CliError::new(format!("Client error: {}", msg)),
            Error::Parse(msg) => CliError::new(format!("Parse error: {}", msg)),
            Error::Output(msg) => CliError::new(format!("Output error: {}", msg)),
            Error::Io(e) => CliError::new(format!("IO error: {}", e)),
            Error::Serialization(e) => CliError::new(format!("Serialization error: {}", e)),
        }
    }
}

impl From<reqwest::Error> for CliError {
    fn from(err: reqwest::Error) -> Self {
        if err.is_connect() || err.is_timeout() {
            CliError::network(format!(
                "Failed to connect to OpenViking server. \
                 Check the url in ovcli.conf and ensure the server is running. ({ })",
                err
            ))
        } else {
            CliError::new(format!("HTTP error: {}", err))
        }
    }
}

impl From<serde_json::Error> for CliError {
    fn from(err: serde_json::Error) -> Self {
        CliError::new(format!("JSON error: {}", err))
    }
}
