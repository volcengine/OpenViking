
//! Chat command for interacting with Vikingbot via OpenAPI (v2 with rustyline)

use std::time::Duration;

use clap::Parser;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use rustyline::error::ReadlineError;
use rustyline::{Editor, history::FileHistory};

use crate::error::{Error, Result};

const DEFAULT_ENDPOINT: &str = "http://localhost:1933/bot/v1";

/// Safely truncate a string at a UTF-8 character boundary
fn truncate_utf8(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }

    let mut boundary = max_bytes;
    while boundary > 0 && !s.is_char_boundary(boundary) {
        boundary -= 1;
    }

    if boundary == 0 {
        ""
    } else {
        &s[..boundary]
    }
}

#[derive(Debug, Parser)]
pub struct ChatCommand {
    #[arg(short, long, default_value = DEFAULT_ENDPOINT)]
    pub endpoint: String,

    #[arg(short, long, env = "VIKINGBOT_API_KEY")]
    pub api_key: Option<String>,

    #[arg(short, long)]
    pub session: Option<String>,

    #[arg(short, long, default_value = "cli_user")]
    pub user: String,

    #[arg(short = 'M', long)]
    pub message: Option<String>,

    #[arg(long)]
    pub stream: bool,

    #[arg(long)]
    pub no_format: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChatMessage {
    role: String,
    content: String,
}

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

#[derive(Debug, Deserialize)]
struct ChatResponse {
    session_id: String,
    message: String,
    #[serde(default)]
    events: Option<Vec<serde_json::Value>>,
}

#[derive(Debug, Deserialize)]
struct StreamEvent {
    event: String,
    data: serde_json::Value,
}

impl ChatCommand {
    pub async fn execute(&self) -> Result<()> {
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .build()
            .map_err(|e| Error::Network(format!("Failed to create HTTP client: {}", e)))?;

        if let Some(message) = &self.message {
            self.send_message(&client, message).await
        } else {
            self.run_interactive(&client).await
        }
    }

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
                                println!("\t\x1b[2mThink: {}...\x1b[0m", truncate_utf8(content, 100));
                            }
                        }
                        "tool_call" => {
                            let content = data.as_str().unwrap_or("");
                            if !self.no_format {
                                println!("\t\x1b[2m├─ Calling: {}\x1b[0m", content);
                            }
                        }
                        "tool_result" => {
                            let content = data.as_str().unwrap_or("");
                            if !self.no_format {
                                let truncated = if content.len() > 150 {
                                    format!("{}...", truncate_utf8(content, 150))
                                } else {
                                    content.to_string()
                                };
                                println!("\t\x1b[2m└─ Result: {}\x1b[0m", truncated);
                            }
                        }
                        _ => {}
                    }
                }
            }
        }

        if !self.no_format {
            println!("\n\x1b[1;31mBot:\x1b[0m");
            println!("{}", chat_response.message);
            println!();
        } else {
            println!("{}", chat_response.message);
        }

        Ok(())
    }

    async fn run_interactive(&self, client: &Client) -> Result<()> {
        println!("Vikingbot Chat - Interactive Mode (v2)");
        println!("Endpoint: {}", self.endpoint);
        if let Some(session) = &self.session {
            println!("Session: {}", session);
        }
        println!("Type 'exit', 'quit', or press Ctrl+C to exit");
        println!("----------------------------------------\n");

        let mut session_id = self.session.clone();

        let mut rl: Editor<(), FileHistory> = Editor::new()
            .map_err(|e| Error::Client(format!("Failed to create line editor: {}", e)))?;

        let _ = rl.load_history("ov_chat_history.txt");

        loop {
            let readline = rl.readline("\x1b[1;32mYou:\x1b[0m ");

            let input = match readline {
                Ok(line) => {
                    let trimmed = line.trim().to_string();
                    if !trimmed.is_empty() {
                        let _ = rl.add_history_entry(line);
                        let _ = rl.save_history("ov_chat_history.txt");
                    }
                    trimmed
                }
                Err(ReadlineError::Interrupted) => {
                    println!("\nGoodbye!");
                    break;
                }
                Err(ReadlineError::Eof) => {
                    println!("\nGoodbye!");
                    break;
                }
                Err(err) => {
                    eprintln!("\x1b[1;31mError reading input: {}\x1b[0m", err);
                    continue;
                }
            };

            if input.is_empty() {
                continue;
            }

            if input.eq_ignore_ascii_case("exit") || input.eq_ignore_ascii_case("quit") {
                println!("\nGoodbye!");
                break;
            }

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
                                if session_id.is_none() {
                                    session_id = Some(chat_response.session_id.clone());
                                }

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
                                                        println!("\t\x1b[2mThink: {}...\x1b[0m", truncate_utf8(content, 100));
                                                    } else {
                                                        println!("\t\x1b[2mThink: {}\x1b[0m", content);
                                                    }
                                                }
                                                "tool_call" => {
                                                    println!("\t\x1b[2m├─ Calling: {}\x1b[0m", data.as_str().unwrap_or(""));
                                                }
                                                "tool_result" => {
                                                    let content = data.as_str().unwrap_or("");
                                                    let truncated = if content.len() > 150 {
                                                        format!("{}...", truncate_utf8(content, 150))
                                                    } else {
                                                        content.to_string()
                                                    };
                                                    println!("\t\x1b[2m└─ Result: {}\x1b[0m", truncated);
                                                }
                                                _ => {}
                                            }
                                        }
                                    }
                                }

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
    pub async fn run(&self) -> Result<()> {
        self.execute().await
    }
}

impl ChatCommand {
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

