//! Chat command for interacting with Vikingbot via OpenAPI
//!
//! Features:
//! - Proper line editing with rustyline (no ^[[D characters)
//! - Markdown rendering for bot responses
//! - Command history support

use std::time::Duration;

use clap::Parser;
use reqwest::Client;
use rustyline::error::ReadlineError;
use rustyline::DefaultEditor;
use serde::{Deserialize, Serialize};
use termimad::MadSkin;

use crate::error::{Error, Result};

const DEFAULT_ENDPOINT: &str = "http://localhost:1933/bot/v1";
const HISTORY_FILE: &str = ".ov_chat_history";

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

    /// Disable rich formatting / markdown rendering
    #[arg(long)]
    pub no_format: bool,

    /// Disable command history
    #[arg(long)]
    pub no_history: bool,
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

/// Stream event - kept for compatibility
#[allow(dead_code)]
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
            // Single message mode
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
        self.print_events(&chat_response.events);

        // Print final response
        self.print_response(&chat_response.message);

        Ok(())
    }

    /// Run interactive chat mode with rustyline
    async fn run_interactive(&self, client: &Client) -> Result<()> {
        println!("Vikingbot Chat - Interactive Mode");
        println!("Endpoint: {}", self.endpoint);
        if let Some(session) = &self.session {
            println!("Session: {}", session);
        }
        println!("Type 'exit', 'quit', or press Ctrl+C to exit");
        println!("----------------------------------------\n");

        // Initialize rustyline editor
        let mut rl = DefaultEditor::new()
            .map_err(|e| Error::Client(format!("Failed to initialize editor: {}", e)))?;

        // Load history if enabled
        let history_path = if !self.no_history {
            self.get_history_path()
        } else {
            None
        };
        if let Some(ref path) = history_path {
            let _ = rl.load_history(path);
        }

        let mut session_id = self.session.clone();

        loop {
            // Read input with rustyline
            let prompt = "\x1b[1;32mYou:\x1b[0m ";
            match rl.readline(prompt) {
                Ok(line) => {
                    let input: &str = line.trim();

                    if input.is_empty() {
                        continue;
                    }

                    // Add to history
                    if !self.no_history {
                        let _ = rl.add_history_entry(input);
                    }

                    // Check for exit
                    if input.eq_ignore_ascii_case("exit") || input.eq_ignore_ascii_case("quit") {
                        println!("\nGoodbye!");
                        break;
                    }

                    // Send message
                    match self.send_interactive_message(client, input, &mut session_id).await {
                        Ok(_) => {}
                        Err(e) => {
                            eprintln!("\x1b[1;31mError: {}\x1b[0m", e);
                        }
                    }
                }
                Err(ReadlineError::Interrupted) => {
                    // Ctrl+C
                    println!("\nGoodbye!");
                    break;
                }
                Err(ReadlineError::Eof) => {
                    // Ctrl+D
                    println!("\nGoodbye!");
                    break;
                }
                Err(e) => {
                    eprintln!("\x1b[1;31mError reading input: {}\x1b[0m", e);
                    break;
                }
            }
        }

        // Save history
        if let Some(ref path) = history_path {
            let _ = rl.save_history(path);
        }

        Ok(())
    }

    /// Send a message in interactive mode
    async fn send_interactive_message(
        &self,
        client: &Client,
        input: &str,
        session_id: &mut Option<String>,
    ) -> Result<()> {
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

        // Save session ID
        if session_id.is_none() {
            *session_id = Some(chat_response.session_id.clone());
        }

        // Print events
        self.print_events(&chat_response.events);

        // Print response with markdown
        println!();
        self.print_response(&chat_response.message);
        println!();

        Ok(())
    }

    /// Print thinking/events
    fn print_events(&self, events: &Option<Vec<serde_json::Value>>) {
        if self.no_format {
            return;
        }

        if let Some(events) = events {
            for event in events {
                if let (Some(etype), Some(data)) = (
                    event.get("type").and_then(|v| v.as_str()),
                    event.get("data"),
                ) {
                    match etype {
                        "reasoning" => {
                            let content = data.as_str().unwrap_or("");
                            println!(
                                "  \x1b[2mThink: {}...\x1b[0m",
                                truncate_utf8(content, 100)
                            );
                        }
                        "tool_call" => {
                            let content = data.as_str().unwrap_or("");
                            println!("  \x1b[2m├─ Calling: {}\x1b[0m", content);
                        }
                        "tool_result" => {
                            let content = data.as_str().unwrap_or("");
                            let truncated = if content.len() > 150 {
                                format!("{}...", truncate_utf8(content, 150))
                            } else {
                                content.to_string()
                            };
                            println!("  \x1b[2m└─ Result: {}\x1b[0m", truncated);
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    /// Print response with optional markdown rendering
    fn print_response(&self, message: &str) {
        if self.no_format {
            println!("{}", message);
            return;
        }

        println!("\x1b[1;31mBot:\x1b[0m");

        // Try to render markdown, fall back to plain text
        render_markdown(message);
    }

    /// Get history file path
    fn get_history_path(&self) -> Option<std::path::PathBuf> {
        dirs::home_dir().map(|home| home.join(HISTORY_FILE))
    }
}

impl ChatCommand {
    /// Execute the chat command (public wrapper)
    pub async fn run(&self) -> Result<()> {
        self.execute().await
    }
}

#[allow(dead_code)]
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
        no_history: bool,
    ) -> Self {
        Self {
            endpoint,
            api_key,
            session,
            user,
            message,
            stream,
            no_format,
            no_history,
        }
    }
}

/// Render markdown to terminal using termimad
fn render_markdown(text: &str) {
    let skin = MadSkin::default();
    skin.print_text(text);
}

/// Truncate UTF-8 string safely
fn truncate_utf8(s: &str, max_chars: usize) -> &str {
    if s.chars().count() <= max_chars {
        return s;
    }
    if let Some((idx, _)) = s.char_indices().nth(max_chars) {
        &s[..idx]
    } else {
        s
    }
}
