//! Chat command for interacting with Vikingbot via OpenAPI

use std::io::Write;
use std::time::Duration;

use clap::Parser;
use reqwest::Client;
use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};

const DEFAULT_ENDPOINT: &str = "http://localhost:18790/api/v1/openapi";

/// Chat with Vikingbot via OpenAPI
#[derive(Debug, Parser)]
pub struct ChatCommand {
    /// API endpoint URL
    #[arg(short, long, default_value = DEFAULT_ENDPOINT)]
    pub endpoint: String,

    /// API key for authentication
    #[arg(short, long, env = "VIKINGBOT_API_KEY")]
    pub api_key: Option<String>,

    /// Session ID to use (creates new if not provided)
    #[arg(short, long)]
    pub session: Option<String>,

    /// User ID
    #[arg(short, long, default_value = "cli_user")]
    pub user: String,

    /// Non-interactive mode (single message)
    #[arg(short = 'M', long)]
    pub message: Option<String>,

    /// Stream the response
    #[arg(long)]
    pub stream: bool,

    /// Disable rich formatting
    #[arg(long)]
    pub no_format: bool,
}

/// Chat message for API
#[derive(Debug, Serialize, Deserialize)]
struct ChatMessage {
    role: String,
    content: String,
}

/// Chat request body
#[derive(Debug, Serialize)]
struct ChatRequest {
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    user_id: Option<String>,
    stream: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    context: Option<Vec<ChatMessage>>,
}

/// Chat response
#[derive(Debug, Deserialize)]
struct ChatResponse {
    session_id: String,
    message: String,
    #[serde(default)]
    events: Option<Vec<serde_json::Value>>,
}

/// Stream event
#[derive(Debug, Deserialize)]
struct StreamEvent {
    event: String,
    data: serde_json::Value,
}

impl ChatCommand {
    /// Execute the chat command
    pub async fn execute(&self) -> Result<()> {
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .build()
            .map_err(|e| Error::Network(format!("Failed to create HTTP client: {}", e)))?;

        if let Some(message) = &self.message {
            // Single message mode - ignore stream flag for now
            self.send_message(&client, message).await
        } else {
            // Interactive mode
            self.run_interactive(&client).await
        }
    }

    /// Send a single message and get response
    async fn send_message(&self, client: &Client, message: &str) -> Result<()> {
        let url = format!("{}/chat", self.endpoint);

        let request = ChatRequest {
            message: message.to_string(),
            session_id: self.session.clone(),
            user_id: Some(self.user.clone()),
            stream: false,
            context: None,
        };

        let mut req_builder = client.post(&url).json(&request);

        if let Some(api_key) = &self.api_key {
            req_builder = req_builder.header("X-API-Key", api_key);
        }

        let response = req_builder
            .send()
            .await
            .map_err(|e| Error::Network(format!("Failed to send request: {}", e)))?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(Error::Api(format!("Request failed ({}): {}", status, text)));
        }

        let chat_response: ChatResponse = response
            .json()
            .await
            .map_err(|e| Error::Parse(format!("Failed to parse response: {}", e)))?;

        // Print events if any
        if let Some(events) = &chat_response.events {
            for event in events {
                if let (Some(etype), Some(data)) = (
                    event.get("type").and_then(|v| v.as_str()),
                    event.get("data"),
                ) {
                    match etype {
                        "reasoning" => {
                            let content = data.as_str().unwrap_or("");
                            if !self.no_format {
                                println!("\x1b[2mThink: {}...\x1b[0m", &content[..content.len().min(100)]);
                            }
                        }
                        "tool_call" => {
                            let content = data.as_str().unwrap_or("");
                            if !self.no_format {
                                println!("\x1b[2m├─ Calling: {}\x1b[0m", content);
                            }
                        }
                        "tool_result" => {
                            let content = data.as_str().unwrap_or("");
                            if !self.no_format {
                                let truncated = if content.len() > 150 {
                                    format!("{}...", &content[..150])
                                } else {
                                    content.to_string()
                                };
                                println!("\x1b[2m└─ Result: {}\x1b[0m", truncated);
                            }
                        }
                        _ => {}
                    }
                }
            }
        }

        // Print final response
        if !self.no_format {
            println!("\n\x1b[1;31mBot:\x1b[0m");
            println!("{}", chat_response.message);
            println!();
        } else {
            println!("{}", chat_response.message);
        }

        Ok(())
    }

    /// Run interactive chat mode
    async fn run_interactive(&self, client: &Client) -> Result<()> {
        println!("Vikingbot Chat - Interactive Mode");
        println!("Endpoint: {}", self.endpoint);
        if let Some(session) = &self.session {
            println!("Session: {}", session);
        }
        println!("Type 'exit', 'quit', or press Ctrl+C to exit");
        println!("----------------------------------------\n");

        let mut session_id = self.session.clone();

        loop {
            // Read input
            print!("\x1b[1;32mYou:\x1b[0m ");
            std::io::stdout().flush().map_err(|e| Error::Io(e))?;

            let mut input = String::new();
            std::io::stdin().read_line(&mut input).map_err(|e| Error::Io(e))?;
            let input = input.trim();

            if input.is_empty() {
                continue;
            }

            // Check for exit
            if input.eq_ignore_ascii_case("exit") || input.eq_ignore_ascii_case("quit") {
                println!("\nGoodbye!");
                break;
            }

            // Send message
            let url = format!("{}/chat", self.endpoint);

            let request = ChatRequest {
                message: input.to_string(),
                session_id: session_id.clone(),
                user_id: Some(self.user.clone()),
                stream: false,
                context: None,
            };

            let mut req_builder = client.post(&url).json(&request);

            if let Some(api_key) = &self.api_key {
                req_builder = req_builder.header("X-API-Key", api_key);
            }

            match req_builder.send().await {
                Ok(response) => {
                    if response.status().is_success() {
                        match response.json::<ChatResponse>().await {
                            Ok(chat_response) => {
                                // Save session ID
                                if session_id.is_none() {
                                    session_id = Some(chat_response.session_id.clone());
                                }

                                // Print events
                                if let Some(events) = chat_response.events {
                                    for event in events {
                                        if let (Some(etype), Some(data)) = (
                                            event.get("type").and_then(|v| v.as_str()),
                                            event.get("data"),
                                        ) {
                                            match etype {
                                                "reasoning" => {
                                                    let content = data.as_str().unwrap_or("");
                                                    if content.len() > 100 {
                                                        println!("\x1b[2mThink: {}...\x1b[0m", &content[..100]);
                                                    } else {
                                                        println!("\x1b[2mThink: {}\x1b[0m", content);
                                                    }
                                                }
                                                "tool_call" => {
                                                    println!("\x1b[2m├─ Calling: {}\x1b[0m", data.as_str().unwrap_or(""));
                                                }
                                                "tool_result" => {
                                                    let content = data.as_str().unwrap_or("");
                                                    let truncated = if content.len() > 150 {
                                                        format!("{}...", &content[..150])
                                                    } else {
                                                        content.to_string()
                                                    };
                                                    println!("\x1b[2m└─ Result: {}\x1b[0m", truncated);
                                                }
                                                _ => {}
                                            }
                                        }
                                    }
                                }

                                // Print response
                                println!("\n\x1b[1;31mBot:\x1b[0m");
                                println!("{}", chat_response.message);
                                println!();
                            }
                            Err(e) => {
                                eprintln!("\x1b[1;31mError parsing response: {}\x1b[0m", e);
                            }
                        }
                    } else {
                        let status = response.status();
                        let text = response.text().await.unwrap_or_default();
                        eprintln!("\x1b[1;31mRequest failed ({}): {}\x1b[0m", status, text);
                    }
                }
                Err(e) => {
                    eprintln!("\x1b[1;31mFailed to send request: {}\x1b[0m", e);
                }
            }
        }

        println!("\nGoodbye!");
        Ok(())
    }
}

impl ChatCommand {
    /// Execute the chat command (public wrapper)
    pub async fn run(&self) -> Result<()> {
        self.execute().await
    }
}

impl ChatCommand {
    /// Create a new ChatCommand with the given parameters
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        endpoint: String,
        api_key: Option<String>,
        session: Option<String>,
        user: String,
        message: Option<String>,
        stream: bool,
        no_format: bool,
    ) -> Self {
        Self {
            endpoint,
            api_key,
            session,
            user,
            message,
            stream,
            no_format,
        }
    }
}
