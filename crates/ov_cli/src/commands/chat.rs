//! Chat command for interacting with Vikingbot via OpenAPI
//!
//! Features:
//! - Proper line editing with rustyline (no ^[[D characters)
//! - Markdown rendering for bot responses
//! - Command history support
//! - Streaming response support

use std::time::Duration;

use clap::Parser;
use reqwest::Client;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;
use serde::{Deserialize, Serialize};
use termimad::MadSkin;

use crate::config::Config;
use crate::utils;

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

    /// Account identifier to send as X-OpenViking-Account
    #[arg(long)]
    pub account: Option<String>,

    /// User identifier to send as X-OpenViking-User
    #[arg(long)]
    pub user: Option<String>,

    /// Session ID to use (creates new if not provided)
    #[arg(short, long)]
    pub session: Option<String>,

    /// Sender ID
    #[arg(long, default_value = "user")]
    pub sender: String,

    /// Non-interactive mode (single message)
    #[arg(short, long)]
    pub message: Option<String>,

    /// Stream the response (default: true)
    #[arg(long, default_value_t = true)]
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

/// Chat response (non-streaming)
#[derive(Debug, Deserialize)]
struct ChatResponse {
    session_id: String,
    message: String,
    #[serde(default)]
    events: Option<Vec<serde_json::Value>>,
}

/// Stream event from SSE
#[derive(Debug, Deserialize)]
struct ChatStreamEvent {
    event: String, // "reasoning", "tool_call", "tool_result", "response"
    data: serde_json::Value,
}

#[derive(Debug, Deserialize)]
struct OpenVikingHealth {
    #[serde(default)]
    auth_mode: Option<String>,
    #[serde(default)]
    role: Option<String>,
    #[serde(default)]
    account_id: Option<String>,
    #[serde(default)]
    user_id: Option<String>,
}

struct ChatAuth {
    api_key: Option<String>,
    account: Option<String>,
    user: Option<String>,
}

impl ChatCommand {
    /// Execute the chat command
    pub async fn execute(&self) -> Result<()> {
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .build()
            .map_err(|e| Error::Network(format!("Failed to create HTTP client: {}", e)))?;

        let health = self.fetch_openviking_health(&client, None).await;
        let auth = self.resolve_auth(health.as_ref().map(health_auth_mode))?;
        self.warn_openviking_chat_auth(&client, health.as_ref(), &auth)
            .await;

        if let Some(message) = &self.message {
            // Single message mode
            self.send_message(&client, message, &auth).await
        } else {
            // Interactive mode
            self.run_interactive(&client, &auth).await
        }
    }

    fn resolve_auth(&self, server_auth_mode: Option<&str>) -> Result<ChatAuth> {
        let config = Config::load()?;
        Ok(self.resolve_auth_from_config(config, server_auth_mode))
    }

    fn resolve_auth_from_config(&self, config: Config, server_auth_mode: Option<&str>) -> ChatAuth {
        if server_auth_mode
            .map(|mode| mode.trim().eq_ignore_ascii_case("trusted"))
            .unwrap_or(false)
        {
            return ChatAuth {
                api_key: non_empty_string(self.api_key.clone())
                    .or_else(|| non_empty_string(config.root_api_key.clone()))
                    .or_else(|| non_empty_string(config.api_key.clone())),
                account: non_empty_string(self.account.clone())
                    .or_else(|| non_empty_string(config.account.clone())),
                user: non_empty_string(self.user.clone())
                    .or_else(|| non_empty_string(config.user.clone())),
            };
        }

        let auth = config.effective_auth_with_overrides(
            self.api_key.clone(),
            self.account.clone(),
            self.user.clone(),
            false,
        );

        ChatAuth {
            api_key: auth.api_key,
            account: auth.account,
            user: auth.user,
        }
    }

    fn apply_auth_headers(
        &self,
        mut req_builder: reqwest::RequestBuilder,
        auth: &ChatAuth,
    ) -> reqwest::RequestBuilder {
        if let Some(api_key) = &auth.api_key {
            req_builder = req_builder.header("X-API-Key", api_key);
        }
        if let Some(account) = &auth.account {
            req_builder = req_builder.header("X-OpenViking-Account", account);
        }
        if let Some(user) = &auth.user {
            req_builder = req_builder.header("X-OpenViking-User", user);
        }
        req_builder
    }

    fn openviking_health_url(&self) -> Option<String> {
        let endpoint = self.endpoint.trim_end_matches('/');
        endpoint
            .strip_suffix("/bot/v1")
            .map(|server_url| format!("{}/health", server_url.trim_end_matches('/')))
    }

    async fn fetch_openviking_health(
        &self,
        client: &Client,
        auth: Option<&ChatAuth>,
    ) -> Option<OpenVikingHealth> {
        let health_url = self.openviking_health_url()?;
        let mut req_builder = client.get(health_url).timeout(Duration::from_secs(5));
        if let Some(auth) = auth {
            req_builder = self.apply_auth_headers(req_builder, auth);
        }
        let response = req_builder.send().await.ok()?;
        if !response.status().is_success() {
            return None;
        }
        response.json::<OpenVikingHealth>().await.ok()
    }

    async fn warn_openviking_chat_auth(
        &self,
        client: &Client,
        health: Option<&OpenVikingHealth>,
        auth: &ChatAuth,
    ) {
        let Some(health) = health else {
            return;
        };
        if health_auth_mode(health) != "api_key" {
            return;
        }

        if auth
            .api_key
            .as_deref()
            .unwrap_or_default()
            .trim()
            .is_empty()
        {
            eprintln!(
                "Warning: OpenViking server is in api_key mode, but ov chat is not configured \
with a User API key. OpenViking-backed VikingBot memory and file tools may not work correctly. \
Configure api_key in ovcli.conf with a User/Admin API key, or pass --api-key."
            );
            return;
        }

        let Some(authenticated_health) = self.fetch_openviking_health(client, Some(auth)).await
        else {
            eprintln!(
                "Warning: OpenViking server is in api_key mode, but ov chat could not validate \
the configured API key. OpenViking-backed VikingBot memory and file tools may not work correctly."
            );
            return;
        };

        if health_has_user_identity(&authenticated_health) {
            return;
        }

        if health_role(&authenticated_health) == "root" {
            eprintln!(
                "Warning: ov chat is using a ROOT API key, but OpenViking api_key mode requires \
a User/Admin API key for VikingBot memory and file tools. Configure api_key in ovcli.conf with \
a User/Admin API key, or pass --api-key."
            );
        } else {
            eprintln!(
                "Warning: ov chat could not validate the configured API key as an OpenViking \
User/Admin API key. OpenViking-backed VikingBot memory and file tools may not work correctly."
            );
        }
    }

    /// Send a single message and get response
    async fn send_message(&self, client: &Client, message: &str, auth: &ChatAuth) -> Result<()> {
        if self.stream {
            self.send_message_stream(client, message, auth).await
        } else {
            self.send_message_non_stream(client, message, auth).await
        }
    }

    /// Send a single message with non-streaming response
    async fn send_message_non_stream(
        &self,
        client: &Client,
        message: &str,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat", self.endpoint);

        let request = ChatRequest {
            message: message.to_string(),
            session_id: self.session.clone(),
            user_id: Some(self.sender.clone()),
            stream: false,
            context: None,
        };

        let req_builder = self.apply_auth_headers(client.post(&url).json(&request), auth);

        let response = req_builder
            .send()
            .await
            .map_err(|e| Error::Network(format!("Failed to send request: {}", e)))?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(Error::api(format!("Request failed ({}): {}", status, text)));
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

    /// Send a single message with streaming response
    async fn send_message_stream(
        &self,
        client: &Client,
        message: &str,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat/stream", self.endpoint);

        let request = ChatRequest {
            message: message.to_string(),
            session_id: self.session.clone(),
            user_id: Some(self.sender.clone()),
            stream: true,
            context: None,
        };

        let req_builder = self.apply_auth_headers(client.post(&url).json(&request), auth);

        let response = req_builder
            .send()
            .await
            .map_err(|e| Error::Network(format!("Failed to send request: {}", e)))?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(Error::api(format!("Request failed ({}): {}", status, text)));
        }

        // Process the SSE stream
        let mut response = response;
        let mut buffer = String::new();
        let mut final_message = String::new();
        let mut response_id: Option<String> = None;

        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| Error::Network(format!("Stream error: {}", e)))?
        {
            let chunk_str = String::from_utf8_lossy(&chunk);
            buffer.push_str(&chunk_str);

            // Process complete lines from buffer
            while let Some(newline_pos) = buffer.find('\n') {
                let line = buffer[..newline_pos].trim_end().to_string();
                buffer = buffer[newline_pos + 1..].to_string();

                if line.is_empty() {
                    continue;
                }

                // Parse SSE line: "data: {json}"
                if let Some(data_str) = line.strip_prefix("data: ") {
                    if let Ok(event) = serde_json::from_str::<ChatStreamEvent>(data_str) {
                        self.print_stream_event(&event);
                        if event.event == "response" {
                            if let Some(msg) = event.data.as_str() {
                                final_message = msg.to_string();
                            } else if let Some(obj) = event.data.as_object() {
                                if let Some(msg) = obj.get("content").and_then(|m| m.as_str()) {
                                    final_message = msg.to_string();
                                }
                                if let Some(rid) = obj.get("response_id").and_then(|r| r.as_str()) {
                                    response_id = Some(rid.to_string());
                                }
                                if let Some(err) = obj.get("error").and_then(|e| e.as_str()) {
                                    eprintln!("\x1b[1;31mError: {}\x1b[0m", err);
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some(response_id) = response_id {
            eprintln!("\x1b[2mResponse ID: {}\x1b[0m", response_id);
        }

        // Print final response with markdown if we have it
        if !final_message.is_empty() {
            println!();
            self.print_response(&final_message);
        }

        Ok(())
    }

    /// Run interactive chat mode with rustyline
    async fn run_interactive(&self, client: &Client, auth: &ChatAuth) -> Result<()> {
        println!("Vikingbot Chat - Interactive Mode");
        println!("Endpoint: {}", self.endpoint);
        if let Some(session) = &self.session {
            println!("Session: {}", session);
        }
        println!("Sender: {}", self.sender);
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
                    match self
                        .send_interactive_message(client, input, &mut session_id, auth)
                        .await
                    {
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
        auth: &ChatAuth,
    ) -> Result<()> {
        if self.stream {
            self.send_interactive_message_stream(client, input, session_id, auth)
                .await
        } else {
            self.send_interactive_message_non_stream(client, input, session_id, auth)
                .await
        }
    }

    /// Send a message in interactive mode (non-streaming)
    async fn send_interactive_message_non_stream(
        &self,
        client: &Client,
        input: &str,
        session_id: &mut Option<String>,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat", self.endpoint);

        let request = ChatRequest {
            message: input.to_string(),
            session_id: session_id.clone(),
            user_id: Some(self.sender.clone()),
            stream: false,
            context: None,
        };

        let req_builder = self.apply_auth_headers(client.post(&url).json(&request), auth);

        let response = req_builder
            .send()
            .await
            .map_err(|e| Error::Network(format!("Failed to send request: {}", e)))?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(Error::api(format!("Request failed ({}): {}", status, text)));
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

    /// Send a message in interactive mode (streaming)
    async fn send_interactive_message_stream(
        &self,
        client: &Client,
        input: &str,
        session_id: &mut Option<String>,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat/stream", self.endpoint);
        let request_session_id = session_id.clone().or_else(|| self.session.clone());

        let request = ChatRequest {
            message: input.to_string(),
            session_id: request_session_id.clone(),
            user_id: Some(self.sender.clone()),
            stream: true,
            context: None,
        };

        let req_builder = self.apply_auth_headers(client.post(&url).json(&request), auth);

        let response = req_builder
            .send()
            .await
            .map_err(|e| Error::Network(format!("Failed to send request: {}", e)))?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(Error::api(format!("Request failed ({}): {}", status, text)));
        }

        let mut response = response;
        let mut buffer = String::new();
        let mut final_message = String::new();
        let mut response_id: Option<String> = None;

        if session_id.is_none() {
            *session_id = request_session_id;
        }

        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| Error::Network(format!("Stream error: {}", e)))?
        {
            let chunk_str = String::from_utf8_lossy(&chunk);
            buffer.push_str(&chunk_str);

            // Process complete lines from buffer
            while let Some(newline_pos) = buffer.find('\n') {
                let line = buffer[..newline_pos].trim_end().to_string();
                buffer = buffer[newline_pos + 1..].to_string();

                if line.is_empty() {
                    continue;
                }

                // Parse SSE line: "data: {json}"
                if let Some(data_str) = line.strip_prefix("data: ") {
                    if let Ok(event) = serde_json::from_str::<ChatStreamEvent>(data_str) {
                        self.print_stream_event(&event);
                        if event.event == "response" {
                            if let Some(msg) = event.data.as_str() {
                                final_message = msg.to_string();
                            } else if let Some(obj) = event.data.as_object() {
                                if let Some(msg) = obj.get("content").and_then(|m| m.as_str()) {
                                    final_message = msg.to_string();
                                }
                                if let Some(rid) = obj.get("response_id").and_then(|r| r.as_str()) {
                                    response_id = Some(rid.to_string());
                                }
                                if let Some(err) = obj.get("error").and_then(|e| e.as_str()) {
                                    eprintln!("\x1b[1;31mError: {}\x1b[0m", err);
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some(response_id) = response_id {
            eprintln!("\x1b[2mResponse ID: {}\x1b[0m", response_id);
        }

        // Print final response with markdown
        if !final_message.is_empty() {
            println!();
            self.print_response(&final_message);
        }
        println!();

        Ok(())
    }

    /// Print a single stream event as it arrives
    fn print_stream_event(&self, event: &ChatStreamEvent) {
        if self.no_format {
            return;
        }

        match event.event.as_str() {
            "reasoning" => {
                if let Some(content) = event.data.as_str() {
                    println!(
                        "  \x1b[2mThink: {}...\x1b[0m",
                        utils::truncate_utf8(content, 200)
                    );
                }
            }
            "tool_call" => {
                if let Some(content) = event.data.as_str() {
                    Self::print_tool_call(content);
                }
            }
            "tool_result" => {
                if let Some(content) = event.data.as_str() {
                    let truncated = if content.len() > 300 {
                        format!("{}...", utils::truncate_utf8(content, 300))
                    } else {
                        content.to_string()
                    };
                    Self::print_tool_result(&truncated);
                }
            }
            "iteration" => {
                // Ignore iteration events for now
            }
            "response" => {
                // Response is handled separately
            }
            _ => {}
        }
    }

    /// Parse and print a tool_call with formatted styling
    fn print_tool_call(content: &str) {
        if let Some(paren_idx) = content.find('(') {
            let tool_name = &content[..paren_idx];
            let args = &content[paren_idx..];
            print!("  \x1b[2m├─ Calling: \x1b[0m");
            print!("\x1b[1m{}\x1b[0m", tool_name);
            println!("\x1b[2m{}\x1b[0m", args);
        } else {
            // Fallback if format doesn't match
            println!("  \x1b[2m├─ Calling: {}\x1b[0m", content);
        }
    }

    /// Print a tool_result with formatted styling
    fn print_tool_result(content: &str) {
        println!("  \x1b[2m└─ Result: {}\x1b[0m", content);
    }

    /// Print thinking/events (for non-streaming mode)
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
                                utils::truncate_utf8(content, 200)
                            );
                        }
                        "tool_call" => {
                            let content = data.as_str().unwrap_or("");
                            Self::print_tool_call(content);
                        }
                        "tool_result" => {
                            let content = data.as_str().unwrap_or("");
                            let truncated = if content.len() > 300 {
                                format!("{}...", utils::truncate_utf8(content, 300))
                            } else {
                                content.to_string()
                            };
                            Self::print_tool_result(&truncated);
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

/// Render markdown to terminal using termimad
fn render_markdown(text: &str) {
    let skin = MadSkin::default();
    skin.print_text(text);
}

fn health_auth_mode(health: &OpenVikingHealth) -> &str {
    health.auth_mode.as_deref().unwrap_or_default().trim()
}

fn health_role(health: &OpenVikingHealth) -> &str {
    health.role.as_deref().unwrap_or_default().trim()
}

fn health_has_user_identity(health: &OpenVikingHealth) -> bool {
    let role = health_role(health);
    let account_id = health.account_id.as_deref().unwrap_or_default().trim();
    let user_id = health.user_id.as_deref().unwrap_or_default().trim();
    matches!(role, "user" | "admin") && !account_id.is_empty() && !user_id.is_empty()
}

fn non_empty_string(value: Option<String>) -> Option<String> {
    value.and_then(|text| {
        if text.trim().is_empty() {
            None
        } else {
            Some(text)
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn command_with_api_key(api_key: Option<&str>) -> ChatCommand {
        ChatCommand {
            endpoint: DEFAULT_ENDPOINT.to_string(),
            api_key: api_key.map(ToString::to_string),
            account: None,
            user: None,
            session: None,
            sender: "user".to_string(),
            message: None,
            stream: true,
            no_format: false,
            no_history: false,
        }
    }

    #[test]
    fn auth_uses_configured_api_key() {
        let command = command_with_api_key(None);
        let config = Config {
            api_key: Some("user-key".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, None);

        assert_eq!(auth.api_key.as_deref(), Some("user-key"));
    }

    #[test]
    fn auth_uses_api_key_override() {
        let command = command_with_api_key(Some("override-key"));
        let config = Config {
            api_key: Some("config-key".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, None);

        assert_eq!(auth.api_key.as_deref(), Some("override-key"));
    }

    #[test]
    fn trusted_auth_uses_root_api_key_and_identity() {
        let command = command_with_api_key(None);
        let config = Config {
            api_key: Some("stale-user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("acme".to_string()),
            user: Some("alice".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, Some("trusted"));

        assert_eq!(auth.api_key.as_deref(), Some("root-key"));
        assert_eq!(auth.account.as_deref(), Some("acme"));
        assert_eq!(auth.user.as_deref(), Some("alice"));
    }

    #[test]
    fn trusted_auth_honors_explicit_api_key_override_as_root_key() {
        let command = command_with_api_key(Some("override-root-key"));
        let config = Config {
            api_key: Some("stale-user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("acme".to_string()),
            user: Some("alice".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, Some("trusted"));

        assert_eq!(auth.api_key.as_deref(), Some("override-root-key"));
        assert_eq!(auth.account.as_deref(), Some("acme"));
        assert_eq!(auth.user.as_deref(), Some("alice"));
    }

    #[test]
    fn api_key_auth_still_uses_user_key_and_omits_stale_identity() {
        let command = command_with_api_key(None);
        let config = Config {
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("stale-account".to_string()),
            user: Some("stale-user".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, Some("api_key"));

        assert_eq!(auth.api_key.as_deref(), Some("user-key"));
        assert!(auth.account.is_none());
        assert!(auth.user.is_none());
    }

    #[test]
    fn openviking_health_url_is_derived_from_bot_proxy_endpoint() {
        let command = command_with_api_key(None);

        assert_eq!(
            command.openviking_health_url().as_deref(),
            Some("http://localhost:1933/health")
        );
    }

    #[test]
    fn openviking_health_url_ignores_non_bot_proxy_endpoint() {
        let mut command = command_with_api_key(None);
        command.endpoint = "http://localhost:18790".to_string();

        assert_eq!(command.openviking_health_url(), None);
    }

    #[test]
    fn health_identity_accepts_user_or_admin_only() {
        let user_health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("user".to_string()),
            account_id: Some("default".to_string()),
            user_id: Some("alice".to_string()),
        };
        let admin_health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("admin".to_string()),
            account_id: Some("default".to_string()),
            user_id: Some("alice".to_string()),
        };
        let root_health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("root".to_string()),
            account_id: Some("default".to_string()),
            user_id: Some("root".to_string()),
        };
        let missing_identity = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("user".to_string()),
            account_id: None,
            user_id: Some("alice".to_string()),
        };

        assert!(health_has_user_identity(&user_health));
        assert!(health_has_user_identity(&admin_health));
        assert!(!health_has_user_identity(&root_health));
        assert!(!health_has_user_identity(&missing_identity));
    }
}
