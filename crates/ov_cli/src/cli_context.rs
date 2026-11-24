//! Shared command context, identity overrides, and HTTP client construction.

use crate::cli::UploadCliOptions;
use crate::client;
use crate::config::Config;
use crate::output::OutputFormat;

/// CLI context shared across commands
#[derive(Debug, Clone)]
pub struct CliContext {
    pub config: Config,
    pub output_format: OutputFormat,
    pub compact: bool,
    pub sudo: bool,
    /// Whether to show upload progress (override config)
    pub show_progress: Option<bool>,
    /// Whether to enable verbose output (override config)
    pub verbose: Option<bool>,
    pub profile: Option<bool>,
}

impl CliContext {
    pub(crate) fn from_config(
        mut config: Config,
        output_format: OutputFormat,
        compact: bool,
        account: Option<String>,
        user: Option<String>,
        actor_peer_id: Option<String>,
        sudo: bool,
        show_progress: Option<bool>,
        verbose: Option<bool>,
        profile: Option<bool>,
    ) -> Self {
        if account.is_some() {
            config.account = account;
        }
        if user.is_some() {
            config.user = user;
        }
        if actor_peer_id.is_some() {
            config.actor_peer_id = actor_peer_id;
            config.agent_id = None;
        }
        Self {
            config,
            output_format,
            compact,
            sudo,
            show_progress,
            verbose,
            profile,
        }
    }

    /// Check if progress should be shown
    pub fn should_show_progress(&self) -> bool {
        self.show_progress.unwrap_or(self.config.show_progress)
    }

    /// Check if verbose output is enabled
    pub fn is_verbose(&self) -> bool {
        self.verbose.unwrap_or(self.config.verbose)
    }

    pub fn get_client(&self) -> client::HttpClient {
        self.get_client_with_timeout(None)
    }

    pub fn get_client_with_timeout(&self, timeout_secs: Option<f64>) -> client::HttpClient {
        let auth = self.config.effective_auth(self.sudo);
        let mut client = client::HttpClient::new(
            &self.config.url,
            auth.api_key,
            auth.account,
            auth.user,
            self.config.effective_actor_peer_id(),
            timeout_secs.unwrap_or(self.config.timeout),
            self.profile.unwrap_or(self.config.profile),
            self.config.effective_extra_headers(),
        )
        .with_gateway_token(self.config.effective_gateway_token());

        // Add LDAP or OIDC authentication if configured
        if let Some(auth_mode) = &self.config.auth_mode {
            match auth_mode.as_str() {
                "ldap" => {
                    client = client.with_auth_mode(Some("ldap".to_string()));
                    if let Some(ldap_username) = &self.config.ldap_username {
                        client = client.with_ldap_username(Some(ldap_username.clone()));
                    }
                    if let Some(ldap_password) = &self.config.ldap_password {
                        client = client.with_ldap_password(Some(ldap_password.clone()));
                    }
                }
                "oidc" => {
                    client = client.with_auth_mode(Some("oidc".to_string()));
                    if let Some(token) = &self.config.oidc_token {
                        client = client.with_oidc_token(Some(token.clone()));
                    } else if let Some(api_key) = &self.config.api_key {
                        // Fallback: use api_key as OIDC token if it looks like a JWT
                        client = client.with_oidc_token(Some(api_key.clone()));
                    }
                }
                _ => {}
            }
        }

        client
    }
}

impl CliContext {
    pub(crate) fn with_upload_options(mut self, options: UploadCliOptions) -> Self {
        self.show_progress = options.show_progress_override();
        self.verbose = options.verbose_override();
        self
    }
}

#[cfg(test)]
#[path = "cli_context/tests.rs"]
mod tests;
