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

    #[error("API error: {message}")]
    Api {
        message: String,
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
            message: message.into(),
            status: None,
        }
    }

    pub fn api_with_status(message: impl Into<String>, status: u16) -> Self {
        Self::Api {
            message: message.into(),
            status: Some(status),
        }
    }
}

pub type Result<T> = std::result::Result<T, Error>;
