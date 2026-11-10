//! Chat command for interacting with Vikingbot via OpenAPI
//!
//! Features:
//! - Proper line editing with rustyline (no ^[[D characters)
//! - Markdown rendering for bot responses
//! - Command history support
//! - Streaming response support

use std::{collections::HashMap, time::Duration};

use clap::Parser;
use colored::Colorize;
use reqwest::Client;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;
use serde::{Deserialize, Serialize};
use termimad::MadSkin;
use unicode_width::UnicodeWidthStr;
use uuid::Uuid;

use crate::base_client::api_error_from_body;
use crate::config::Config;
use crate::i18n::{Language, copy};
use crate::theme;
use crate::utils;

use crate::error::{Error, Result};

const HISTORY_FILE: &str = ".ov_chat_history";
const CHAT_LABEL_WIDTH: usize = 12;
const CHAT_ACTION_WIDTH: usize = 16;

/// Chat with Vikingbot via OpenAPI
#[derive(Debug, Parser)]
pub struct ChatCommand {
    /// API endpoint URL
    #[arg(short, long)]
    pub endpoint: Option<String>,

    /// API key for authentication
    #[arg(short, long, env = "VIKINGBOT_API_KEY")]
    pub api_key: Option<String>,

    /// Account identifier to send as X-OpenViking-Account
    #[arg(long)]
    pub account: Option<String>,

    /// User identifier to send as X-OpenViking-User
    #[arg(long)]
    pub user: Option<String>,

    /// Actor peer scope to send as X-OpenViking-Actor-Peer
    #[arg(long, hide = true)]
    pub actor_peer_id: Option<String>,

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

#[derive(Debug, Deserialize, Default)]
struct OpenVikingHealth {
    #[serde(default)]
    auth_mode: Option<String>,
    #[serde(default)]
    role: Option<String>,
    #[serde(default)]
    account_id: Option<String>,
    #[serde(default)]
    user_id: Option<String>,
    #[serde(default)]
    gateway: Option<String>,
    #[serde(default)]
    upstream_configured: Option<bool>,
    #[serde(default)]
    upstream_url: Option<String>,
    #[serde(default)]
    gateway_token_required: bool,
}

struct ChatAuth {
    api_key: Option<String>,
    account: Option<String>,
    user: Option<String>,
    actor_peer_id: Option<String>,
    extra_headers: HashMap<String, String>,
    gateway_token: Option<String>,
    gateway_token_required: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ChatAuthWarning {
    MissingUserKey,
    CannotValidateKey,
    RootKey,
    InvalidUserKey,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ChatOpenVikingInfo {
    enabled: bool,
    server_url: Option<String>,
}

impl ChatCommand {
    /// Execute the chat command
    pub async fn execute(&self) -> Result<()> {
        let language = Language::current();
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .build()
            .map_err(|e| Error::from_reqwest("Failed to create HTTP client", e))?;

        let config = Config::load()?;
        let endpoint = self.resolve_endpoint_from_config(&config);
        let probe_auth = self.health_probe_auth_from_config(&config);
        let health = self
            .fetch_openviking_health(&client, &endpoint, Some(&probe_auth))
            .await;
        let openviking_info = openviking_info_from_health(&endpoint, health.as_ref());
        let gateway_token_required = health.as_ref().is_some_and(|health| {
            health.gateway.as_deref() == Some("vikingbot") && health.gateway_token_required
        });
        let auth = self.resolve_auth_from_config(
            config,
            health.as_ref().map(health_auth_mode),
            gateway_token_required,
        );
        let auth_warning = self
            .openviking_chat_auth_warning(&client, &endpoint, health.as_ref(), &auth)
            .await;

        if let Some(message) = &self.message {
            if let Some(warning) = auth_warning {
                eprint!("{}", render_chat_warning(warning, language));
            }
            // Single message mode
            self.send_message(&client, &endpoint, message, &auth).await
        } else {
            // Interactive mode
            self.run_interactive(
                &client,
                &endpoint,
                &auth,
                auth_warning,
                &openviking_info,
                language,
            )
            .await
        }
    }

    fn resolve_endpoint_from_config(&self, config: &Config) -> String {
        if let Some(endpoint) = non_empty_string(self.endpoint.clone()) {
            return endpoint.trim_end_matches('/').to_string();
        }
        chat_endpoint_from_base_url(&config.url)
    }

    fn resolve_auth_from_config(
        &self,
        config: Config,
        server_auth_mode: Option<&str>,
        gateway_token_required: bool,
    ) -> ChatAuth {
        let gateway_token = config.effective_gateway_token();
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
                actor_peer_id: non_empty_string(self.actor_peer_id.clone())
                    .or_else(|| config.effective_actor_peer_id()),
                extra_headers: config.effective_extra_headers().unwrap_or_default(),
                gateway_token,
                gateway_token_required,
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
            account: None,
            user: None,
            actor_peer_id: non_empty_string(self.actor_peer_id.clone())
                .or_else(|| config.effective_actor_peer_id()),
            extra_headers: config.effective_extra_headers().unwrap_or_default(),
            gateway_token,
            gateway_token_required,
        }
    }

    fn health_probe_auth_from_config(&self, config: &Config) -> ChatAuth {
        ChatAuth {
            api_key: non_empty_string(self.api_key.clone())
                .or_else(|| non_empty_string(config.root_api_key.clone()))
                .or_else(|| non_empty_string(config.api_key.clone())),
            account: non_empty_string(self.account.clone())
                .or_else(|| non_empty_string(config.account.clone())),
            user: non_empty_string(self.user.clone())
                .or_else(|| non_empty_string(config.user.clone())),
            actor_peer_id: non_empty_string(self.actor_peer_id.clone())
                .or_else(|| config.effective_actor_peer_id()),
            extra_headers: config.effective_extra_headers().unwrap_or_default(),
            gateway_token: config.effective_gateway_token(),
            gateway_token_required: false,
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
        if let Some(actor_peer_id) = &auth.actor_peer_id {
            req_builder = req_builder.header("X-OpenViking-Actor-Peer", actor_peer_id);
        }
        for (key, value) in &auth.extra_headers {
            req_builder = req_builder.header(key.as_str(), value);
        }
        if auth.gateway_token_required {
            if let Some(gateway_token) = &auth.gateway_token {
                req_builder = req_builder.header("X-Gateway-Token", gateway_token);
            }
        }
        req_builder
    }

    fn openviking_health_url(&self, endpoint: &str) -> Option<String> {
        let endpoint = endpoint.trim_end_matches('/');
        endpoint
            .strip_suffix("/bot/v1")
            .map(|server_url| format!("{}/health", server_url.trim_end_matches('/')))
    }

    async fn fetch_openviking_health(
        &self,
        client: &Client,
        endpoint: &str,
        auth: Option<&ChatAuth>,
    ) -> Option<OpenVikingHealth> {
        let health_url = self.openviking_health_url(endpoint)?;
        let mut req_builder = client.get(health_url).timeout(Duration::from_secs(5));
        if let Some(auth) = auth {
            req_builder = self.apply_auth_headers(req_builder, auth);
        }
        let retry = req_builder.try_clone();
        let mut response = req_builder.send().await.ok()?;
        let is_gateway_challenge = response.status() == reqwest::StatusCode::UNAUTHORIZED
            && response
                .headers()
                .get("X-VikingBot-Gateway")
                .and_then(|value| value.to_str().ok())
                .is_some_and(|value| value.eq_ignore_ascii_case("true"));
        if is_gateway_challenge {
            let gateway_token = auth.and_then(|auth| auth.gateway_token.as_deref())?;
            response = retry?
                .header("X-Gateway-Token", gateway_token)
                .send()
                .await
                .ok()?;
        }
        if !response.status().is_success() {
            return None;
        }
        response.json::<OpenVikingHealth>().await.ok()
    }

    async fn openviking_chat_auth_warning(
        &self,
        client: &Client,
        endpoint: &str,
        health: Option<&OpenVikingHealth>,
        auth: &ChatAuth,
    ) -> Option<ChatAuthWarning> {
        let Some(health) = health else {
            return None;
        };
        if health_auth_mode(health) != "api_key" {
            return None;
        }

        if auth
            .api_key
            .as_deref()
            .unwrap_or_default()
            .trim()
            .is_empty()
        {
            return Some(ChatAuthWarning::MissingUserKey);
        }

        let Some(authenticated_health) = self
            .fetch_openviking_health(client, endpoint, Some(auth))
            .await
        else {
            return Some(ChatAuthWarning::CannotValidateKey);
        };

        if health_has_user_identity(&authenticated_health) {
            return None;
        }

        if health_role(&authenticated_health) == "root" {
            Some(ChatAuthWarning::RootKey)
        } else {
            Some(ChatAuthWarning::InvalidUserKey)
        }
    }

    /// Send a single message and get response
    async fn send_message(
        &self,
        client: &Client,
        endpoint: &str,
        message: &str,
        auth: &ChatAuth,
    ) -> Result<()> {
        if self.stream {
            self.send_message_stream(client, endpoint, message, auth)
                .await
        } else {
            self.send_message_non_stream(client, endpoint, message, auth)
                .await
        }
    }

    /// Send a single message with non-streaming response
    async fn send_message_non_stream(
        &self,
        client: &Client,
        endpoint: &str,
        message: &str,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat", endpoint);

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
            .map_err(|e| Error::from_reqwest("Failed to send request", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .bytes()
                .await
                .map_err(|e| Error::from_reqwest("Failed to read error response", e))?;
            return Err(api_error_from_body(&body, status));
        }

        let chat_response: ChatResponse = response
            .json()
            .await
            .map_err(|e| Error::from_reqwest("Failed to parse response", e))?;

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
        endpoint: &str,
        message: &str,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat/stream", endpoint);

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
            .map_err(|e| Error::from_reqwest("Failed to send request", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .bytes()
                .await
                .map_err(|e| Error::from_reqwest("Failed to read error response", e))?;
            return Err(api_error_from_body(&body, status));
        }

        // Process the SSE stream
        let mut response = response;
        let mut buffer = String::new();
        let mut final_message = String::new();
        let mut response_id: Option<String> = None;

        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| Error::from_reqwest("Stream error", e))?
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
                                    print_chat_error(Language::current(), err);
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some(response_id) = response_id {
            eprintln!(
                "{}",
                chat_detail_line(
                    copy(Language::current(), "Response ID", "响应 ID"),
                    muted_value(&response_id),
                )
            );
        }

        // Print final response with markdown if we have it
        if !final_message.is_empty() {
            println!();
            self.print_response(&final_message);
        }

        Ok(())
    }

    /// Run interactive chat mode with rustyline
    async fn run_interactive(
        &self,
        client: &Client,
        endpoint: &str,
        auth: &ChatAuth,
        auth_warning: Option<ChatAuthWarning>,
        openviking_info: &ChatOpenVikingInfo,
        language: Language,
    ) -> Result<()> {
        print!(
            "{}",
            render_chat_banner(
                endpoint,
                self.session.as_deref(),
                &self.sender,
                auth_warning,
                openviking_info,
                language,
            )
        );

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
            let prompt = format!("{} ", theme::prompt(copy(language, "You:", "你：")).bold());
            match rl.readline(&prompt) {
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
                        println!("\n{}", theme::muted(copy(language, "Goodbye.", "已退出。")));
                        break;
                    }

                    // Send message
                    match self
                        .send_interactive_message(client, endpoint, input, &mut session_id, auth)
                        .await
                    {
                        Ok(_) => {}
                        Err(e) => {
                            print_chat_error(language, &e.to_string());
                        }
                    }
                }
                Err(ReadlineError::Interrupted) => {
                    // Ctrl+C
                    println!("\n{}", theme::muted(copy(language, "Goodbye.", "已退出。")));
                    break;
                }
                Err(ReadlineError::Eof) => {
                    // Ctrl+D
                    println!("\n{}", theme::muted(copy(language, "Goodbye.", "已退出。")));
                    break;
                }
                Err(e) => {
                    print_chat_error(
                        language,
                        &format!(
                            "{}: {e}",
                            copy(language, "Failed to read input", "读取输入失败")
                        ),
                    );
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
        endpoint: &str,
        input: &str,
        session_id: &mut Option<String>,
        auth: &ChatAuth,
    ) -> Result<()> {
        if self.stream {
            self.send_interactive_message_stream(client, endpoint, input, session_id, auth)
                .await
        } else {
            self.send_interactive_message_non_stream(client, endpoint, input, session_id, auth)
                .await
        }
    }

    /// Send a message in interactive mode (non-streaming)
    async fn send_interactive_message_non_stream(
        &self,
        client: &Client,
        endpoint: &str,
        input: &str,
        session_id: &mut Option<String>,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat", endpoint);

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
            .map_err(|e| Error::from_reqwest("Failed to send request", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .bytes()
                .await
                .map_err(|e| Error::from_reqwest("Failed to read error response", e))?;
            return Err(api_error_from_body(&body, status));
        }

        let chat_response: ChatResponse = response
            .json()
            .await
            .map_err(|e| Error::from_reqwest("Failed to parse response", e))?;

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
        endpoint: &str,
        input: &str,
        session_id: &mut Option<String>,
        auth: &ChatAuth,
    ) -> Result<()> {
        let url = format!("{}/chat/stream", endpoint);
        let request_session_id = session_id
            .clone()
            .or_else(|| self.session.clone())
            .or_else(|| Some(Uuid::new_v4().to_string()));

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
            .map_err(|e| Error::from_reqwest("Failed to send request", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .bytes()
                .await
                .map_err(|e| Error::from_reqwest("Failed to read error response", e))?;
            return Err(api_error_from_body(&body, status));
        }

        let response_session_id = response
            .headers()
            .get("X-VikingBot-Session-ID")
            .and_then(|value| value.to_str().ok())
            .and_then(non_empty_str)
            .map(ToString::to_string);
        let mut response = response;
        let mut buffer = String::new();
        let mut final_message = String::new();
        let mut response_id: Option<String> = None;

        if session_id.is_none() {
            *session_id = response_session_id.or(request_session_id);
        }

        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| Error::from_reqwest("Stream error", e))?
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
                                    print_chat_error(Language::current(), err);
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some(response_id) = response_id {
            eprintln!(
                "{}",
                chat_detail_line(
                    copy(Language::current(), "Response ID", "响应 ID"),
                    muted_value(&response_id),
                )
            );
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
                    print_reasoning(content, Language::current());
                }
            }
            "tool_call" => {
                if let Some(content) = event.data.as_str() {
                    print_tool_call(content, Language::current());
                }
            }
            "tool_result" => {
                if let Some(content) = event.data.as_str() {
                    let truncated = if content.len() > 300 {
                        format!("{}...", utils::truncate_utf8(content, 300))
                    } else {
                        content.to_string()
                    };
                    print_tool_result(&truncated, Language::current());
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

    /// Print thinking/events (for non-streaming mode)
    fn print_events(&self, events: &Option<Vec<serde_json::Value>>) {
        if self.no_format {
            return;
        }

        let language = Language::current();
        if let Some(events) = events {
            for event in events {
                if let (Some(etype), Some(data)) = (
                    event.get("type").and_then(|v| v.as_str()),
                    event.get("data"),
                ) {
                    match etype {
                        "reasoning" => {
                            let content = data.as_str().unwrap_or("");
                            print_reasoning(content, language);
                        }
                        "tool_call" => {
                            let content = data.as_str().unwrap_or("");
                            print_tool_call(content, language);
                        }
                        "tool_result" => {
                            let content = data.as_str().unwrap_or("");
                            let truncated = if content.len() > 300 {
                                format!("{}...", utils::truncate_utf8(content, 300))
                            } else {
                                content.to_string()
                            };
                            print_tool_result(&truncated, language);
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

        println!(
            "{}",
            theme::heading(copy(Language::current(), "VikingBot", "VikingBot")).bold()
        );

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

fn render_chat_banner(
    endpoint: &str,
    session: Option<&str>,
    sender: &str,
    warning: Option<ChatAuthWarning>,
    openviking_info: &ChatOpenVikingInfo,
    language: Language,
) -> String {
    let mut lines = vec![chat_title(language)];

    if let Some(warning) = warning {
        lines.push(String::new());
        lines.push(chat_section_title(copy(language, "Warning", "警告")));
        lines.extend(chat_warning_lines(warning, language));
    }

    lines.push(String::new());
    lines.push(chat_section_title(copy(language, "Connection", "连接")));
    lines.push(chat_detail_line(
        copy(language, "Endpoint", "端点"),
        value_text(endpoint),
    ));
    lines.push(chat_detail_line(
        "OpenViking",
        enabled_value(openviking_info.enabled, language),
    ));
    lines.push(chat_detail_line(
        copy(language, "OV Server", "OV Server"),
        match openviking_info.server_url.as_deref() {
            Some(server_url) => value_text(server_url),
            None => muted_value(copy(language, "not configured", "未配置")),
        },
    ));
    lines.push(chat_detail_line(
        copy(language, "Session", "会话"),
        match session {
            Some(session) => value_text(session),
            None => muted_value(copy(language, "new session", "新会话")),
        },
    ));
    lines.push(chat_detail_line(
        copy(language, "Sender", "发送者"),
        plain_value(sender),
    ));

    lines.push(String::new());
    lines.push(chat_section_title(copy(language, "Controls", "操作")));
    lines.push(chat_action_line(
        "exit / quit",
        copy(language, "End the chat", "退出对话"),
    ));
    lines.push(chat_action_line(
        "Ctrl+C",
        copy(language, "End the chat", "退出对话"),
    ));

    format!("{}\n", lines.join("\n"))
}

fn render_chat_warning(warning: ChatAuthWarning, language: Language) -> String {
    let mut lines = vec![chat_section_title(copy(language, "Warning", "警告"))];
    lines.extend(chat_warning_lines(warning, language));
    format!("{}\n", lines.join("\n"))
}

fn chat_warning_lines(warning: ChatAuthWarning, language: Language) -> Vec<String> {
    let (issue, fix) = chat_warning_copy(warning, language);
    vec![
        chat_detail_line(copy(language, "Issue", "问题"), warning_value(issue)),
        chat_detail_line(copy(language, "Fix", "处理"), muted_value(fix)),
    ]
}

fn chat_warning_copy(warning: ChatAuthWarning, language: Language) -> (&'static str, &'static str) {
    match warning {
        ChatAuthWarning::MissingUserKey => (
            copy(
                language,
                "OpenViking server is in api_key mode and requires a User/Admin API key",
                "OpenViking server 是 api_key 模式，需使用 User/Admin API Key 访问",
            ),
            copy(
                language,
                "Set api_key in ovcli.conf, or pass --api-key.",
                "在 ovcli.conf 配置 api_key，或传 --api-key。",
            ),
        ),
        ChatAuthWarning::CannotValidateKey => (
            copy(
                language,
                "Configured API key could not be validated",
                "当前 API Key 无法验证",
            ),
            copy(
                language,
                "Memory and file tools may be unavailable; check the key.",
                "memory/file tools 可能不可用，请检查 API Key。",
            ),
        ),
        ChatAuthWarning::RootKey => (
            copy(
                language,
                "OpenViking server is in api_key mode and requires a User/Admin API key. The current request uses root_api_key, so VikingBot cannot use OpenViking features correctly.",
                "OpenViking server 是 api_key 模式，需使用 User/Admin API Key 访问。当前请求实际使用的是 root_api_key，bot 将无法正常使用 OpenViking 功能。",
            ),
            copy(
                language,
                "Set api_key in ovcli.conf to a User/Admin API key.",
                "请在 ovcli.conf 中配置 api_key 为 User/Admin API Key。",
            ),
        ),
        ChatAuthWarning::InvalidUserKey => (
            copy(
                language,
                "Configured key is not a usable User/Admin API key",
                "当前 API Key 不是可用的 User/Admin API Key",
            ),
            copy(
                language,
                "Update api_key in ovcli.conf, or pass --api-key.",
                "更新 ovcli.conf 的 api_key，或传 --api-key。",
            ),
        ),
    }
}

fn chat_title(language: Language) -> String {
    theme::brand_title(copy(language, "VIKINGBOT CHAT", "VIKINGBOT 对话"))
        .bold()
        .to_string()
}

fn chat_section_title(title: &str) -> String {
    theme::heading(title).bold().to_string()
}

fn chat_detail_line(label: &str, value: String) -> String {
    let label = theme::muted(pad_to_display_width(label, CHAT_LABEL_WIDTH));
    format!("  {label}{value}")
}

fn chat_action_line(command: &str, description: &str) -> String {
    let command = theme::command(pad_to_display_width(command, CHAT_ACTION_WIDTH)).bold();
    format!("  {command}{}", theme::muted(description))
}

fn value_text(value: &str) -> String {
    theme::value(value).to_string()
}

fn plain_value(value: &str) -> String {
    theme::body(value).to_string()
}

fn muted_value(value: &str) -> String {
    theme::muted(value).to_string()
}

fn warning_value(value: &str) -> String {
    theme::warning(value).bold().to_string()
}

fn enabled_value(enabled: bool, language: Language) -> String {
    if enabled {
        theme::success(copy(language, "Yes", "是"))
            .bold()
            .to_string()
    } else {
        theme::muted(copy(language, "No", "否")).to_string()
    }
}

fn pad_to_display_width(value: &str, width: usize) -> String {
    format!(
        "{}{}",
        value,
        " ".repeat(width.saturating_sub(UnicodeWidthStr::width(value)))
    )
}

fn print_chat_error(language: Language, message: &str) {
    eprintln!(
        "{} {}",
        theme::error(copy(language, "Error:", "错误：")).bold(),
        theme::body(message)
    );
}

fn print_reasoning(content: &str, language: Language) {
    println!(
        "  {} {}",
        theme::muted(format!("{}:", copy(language, "Think", "思考"))),
        theme::muted(format!("{}...", utils::truncate_utf8(content, 200)))
    );
}

fn print_tool_call(content: &str, language: Language) {
    if let Some(paren_idx) = content.find('(') {
        let tool_name = &content[..paren_idx];
        let args = &content[paren_idx..];
        println!(
            "  {} {} {}{}",
            theme::muted("├─"),
            theme::muted(format!("{}:", copy(language, "Calling", "调用"))),
            theme::command(tool_name).bold(),
            theme::muted(args)
        );
    } else {
        println!(
            "  {} {} {}",
            theme::muted("├─"),
            theme::muted(format!("{}:", copy(language, "Calling", "调用"))),
            theme::body(content)
        );
    }
}

fn print_tool_result(content: &str, language: Language) {
    println!(
        "  {} {} {}",
        theme::muted("└─"),
        theme::muted(format!("{}:", copy(language, "Result", "结果"))),
        theme::body(content)
    );
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

fn openviking_info_from_health(
    endpoint: &str,
    health: Option<&OpenVikingHealth>,
) -> ChatOpenVikingInfo {
    let server_url_from_endpoint = openviking_server_url_from_endpoint(endpoint);
    let Some(health) = health else {
        return ChatOpenVikingInfo {
            enabled: false,
            server_url: None,
        };
    };

    if health_is_vikingbot_gateway(health) {
        let upstream_url = health
            .upstream_url
            .as_deref()
            .and_then(non_empty_str)
            .map(ToString::to_string);
        let upstream_configured = health
            .upstream_configured
            .unwrap_or_else(|| upstream_url.is_some());
        return ChatOpenVikingInfo {
            enabled: upstream_configured,
            server_url: upstream_configured.then_some(upstream_url).flatten(),
        };
    }

    ChatOpenVikingInfo {
        enabled: true,
        server_url: server_url_from_endpoint,
    }
}

fn health_is_vikingbot_gateway(health: &OpenVikingHealth) -> bool {
    health
        .gateway
        .as_deref()
        .map(|gateway| gateway.trim().eq_ignore_ascii_case("vikingbot"))
        .unwrap_or(false)
}

fn openviking_server_url_from_endpoint(endpoint: &str) -> Option<String> {
    endpoint
        .trim_end_matches('/')
        .strip_suffix("/bot/v1")
        .and_then(non_empty_str)
        .map(ToString::to_string)
}

fn chat_endpoint_from_base_url(url: &str) -> String {
    format!("{}/bot/v1", url.trim_end_matches('/'))
}

fn non_empty_str(value: &str) -> Option<&str> {
    let value = value.trim();
    if value.is_empty() { None } else { Some(value) }
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
            endpoint: None,
            api_key: api_key.map(ToString::to_string),
            account: None,
            user: None,
            actor_peer_id: None,
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

        let auth = command.resolve_auth_from_config(config, None, false);

        assert_eq!(auth.api_key.as_deref(), Some("user-key"));
    }

    #[test]
    fn auth_uses_api_key_override() {
        let command = command_with_api_key(Some("override-key"));
        let config = Config {
            api_key: Some("config-key".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, None, false);

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

        let auth = command.resolve_auth_from_config(config, Some("trusted"), false);

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

        let auth = command.resolve_auth_from_config(config, Some("trusted"), false);

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

        let auth = command.resolve_auth_from_config(config, Some("api_key"), false);

        assert_eq!(auth.api_key.as_deref(), Some("user-key"));
        assert!(auth.account.is_none());
        assert!(auth.user.is_none());
    }

    #[test]
    fn openviking_health_url_is_derived_from_bot_proxy_endpoint() {
        let command = command_with_api_key(None);

        assert_eq!(
            command
                .openviking_health_url("http://localhost:1933/bot/v1")
                .as_deref(),
            Some("http://localhost:1933/health")
        );
    }

    #[test]
    fn openviking_health_url_ignores_non_bot_proxy_endpoint() {
        let command = command_with_api_key(None);

        assert_eq!(
            command.openviking_health_url("http://localhost:18790"),
            None
        );
    }

    #[test]
    fn chat_endpoint_is_derived_from_config_url_when_endpoint_is_not_explicit() {
        let command = command_with_api_key(None);
        let config = Config {
            url: "http://gateway.example:18790/".to_string(),
            ..Config::default()
        };

        assert_eq!(
            command.resolve_endpoint_from_config(&config),
            "http://gateway.example:18790/bot/v1"
        );
    }

    #[test]
    fn chat_endpoint_prefers_explicit_endpoint() {
        let mut command = command_with_api_key(None);
        command.endpoint = Some("http://custom.example/bot/v1/".to_string());

        assert_eq!(
            command.resolve_endpoint_from_config(&Config::default()),
            "http://custom.example/bot/v1"
        );
    }

    #[test]
    fn auth_keeps_gateway_token_separate_from_general_headers() {
        let command = command_with_api_key(None);
        let config = Config {
            api_key: Some("user-key".to_string()),
            actor_peer_id: Some("peer-a".to_string()),
            gateway_token: Some("gateway-secret".to_string()),
            ..Config::default()
        };

        let auth = command.resolve_auth_from_config(config, Some("api_key"), false);

        assert_eq!(auth.actor_peer_id.as_deref(), Some("peer-a"));
        assert_eq!(auth.gateway_token.as_deref(), Some("gateway-secret"));
        assert!(!auth.gateway_token_required);
        assert!(!auth.extra_headers.contains_key("X-Gateway-Token"));
    }

    #[test]
    fn chat_banner_renders_aligned_english_summary() {
        let openviking_info = ChatOpenVikingInfo {
            enabled: true,
            server_url: Some("http://localhost:18791".to_string()),
        };
        let rendered = render_chat_banner(
            "http://localhost:18791/bot/v1",
            Some("session-1"),
            "user",
            Some(ChatAuthWarning::RootKey),
            &openviking_info,
            Language::En,
        );
        let plain = strip_ansi(&rendered);

        assert!(plain.contains("VIKINGBOT CHAT"));
        assert!(plain.contains("Warning"));
        assert!(plain.contains("Issue       OpenViking server is in api_key mode"));
        assert!(plain.contains("The current request uses root_api_key"));
        assert!(plain.contains("Fix         Set api_key in ovcli.conf to a User/Admin API key."));
        assert!(plain.contains("Connection"));
        assert!(plain.contains("Endpoint    http://localhost:18791/bot/v1"));
        assert!(plain.contains("OpenViking"));
        assert!(plain.contains("Yes"));
        assert!(plain.contains("OV Server   http://localhost:18791"));
        assert!(plain.contains("Session     session-1"));
        assert!(plain.contains("Controls"));
        assert!(plain.contains("exit / quit     End the chat"));
    }

    #[test]
    fn chat_banner_renders_chinese_copy() {
        let openviking_info = ChatOpenVikingInfo {
            enabled: false,
            server_url: None,
        };
        let rendered = render_chat_banner(
            "http://localhost:18791/bot/v1",
            None,
            "user",
            Some(ChatAuthWarning::MissingUserKey),
            &openviking_info,
            Language::ZhCn,
        );
        let plain = strip_ansi(&rendered);

        assert!(plain.contains("VIKINGBOT 对话"));
        assert!(plain.contains("警告"));
        assert!(plain.contains("OpenViking server 是 api_key 模式"));
        assert!(plain.contains("连接"));
        assert!(plain.contains("端点"));
        assert!(plain.contains("OpenViking"));
        assert!(plain.contains("否"));
        assert!(plain.contains("OV Server   未配置"));
        assert!(plain.contains("新会话"));
        assert!(plain.contains("操作"));
        assert!(plain.contains("退出对话"));
    }

    #[test]
    fn root_key_warning_tells_user_to_edit_ovcli_api_key_in_chinese() {
        let rendered = render_chat_warning(ChatAuthWarning::RootKey, Language::ZhCn);
        let plain = strip_ansi(&rendered);

        assert!(plain.contains("问题"));
        assert!(plain.contains("OpenViking server 是 api_key 模式"));
        assert!(plain.contains("当前请求实际使用的是 root_api_key"));
        assert!(plain.contains("bot 将无法正常使用 OpenViking 功能"));
        assert!(plain.contains("处理"));
        assert!(plain.contains("请在 ovcli.conf 中配置 api_key 为 User/Admin API Key。"));
    }

    #[test]
    fn openviking_info_uses_gateway_upstream() {
        let health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: None,
            account_id: None,
            user_id: None,
            gateway: Some("vikingbot".to_string()),
            upstream_configured: Some(true),
            upstream_url: Some("http://ov.local:1935".to_string()),
            gateway_token_required: false,
        };

        let info = openviking_info_from_health("http://gateway.local:18791/bot/v1", Some(&health));

        assert!(info.enabled);
        assert_eq!(info.server_url.as_deref(), Some("http://ov.local:1935"));
    }

    #[test]
    fn openviking_info_marks_standalone_gateway_disabled() {
        let health = OpenVikingHealth {
            auth_mode: None,
            role: None,
            account_id: None,
            user_id: None,
            gateway: Some("vikingbot".to_string()),
            upstream_configured: Some(false),
            upstream_url: None,
            gateway_token_required: false,
        };

        let info = openviking_info_from_health("http://gateway.local:18791/bot/v1", Some(&health));

        assert!(!info.enabled);
        assert!(info.server_url.is_none());
    }

    #[test]
    fn health_identity_accepts_user_or_admin_only() {
        let user_health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("user".to_string()),
            account_id: Some("default".to_string()),
            user_id: Some("alice".to_string()),
            ..OpenVikingHealth::default()
        };
        let admin_health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("admin".to_string()),
            account_id: Some("default".to_string()),
            user_id: Some("alice".to_string()),
            ..OpenVikingHealth::default()
        };
        let root_health = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("root".to_string()),
            account_id: Some("default".to_string()),
            user_id: Some("root".to_string()),
            ..OpenVikingHealth::default()
        };
        let missing_identity = OpenVikingHealth {
            auth_mode: Some("api_key".to_string()),
            role: Some("user".to_string()),
            account_id: None,
            user_id: Some("alice".to_string()),
            ..OpenVikingHealth::default()
        };

        assert!(health_has_user_identity(&user_health));
        assert!(health_has_user_identity(&admin_health));
        assert!(!health_has_user_identity(&root_health));
        assert!(!health_has_user_identity(&missing_identity));
    }

    fn strip_ansi(input: &str) -> String {
        let mut output = String::with_capacity(input.len());
        let mut chars = input.chars().peekable();

        while let Some(ch) = chars.next() {
            if ch == '\u{1b}' && chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
                continue;
            }
            output.push(ch);
        }

        output
    }
}
