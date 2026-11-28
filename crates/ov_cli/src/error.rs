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

pub(crate) fn single_line_detail(detail: &str) -> String {
    detail.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(crate) fn proxy_hint_for_error(
    language: crate::i18n::Language,
    target_url: Option<&str>,
    detail: &str,
) -> Option<String> {
    let resolved_url = target_url.or_else(|| extract_url_from_detail(detail));
    let var = active_proxy_var_from_lookup(resolved_url, |key| std::env::var(key).ok())?;
    Some(match language {
        crate::i18n::Language::En => format!(
            "Proxy environment detected ({var}); check the proxy or set NO_PROXY if the server should be reached directly."
        ),
        crate::i18n::Language::ZhCn => format!(
            "检测到代理环境变量（{var}）；如需直连服务器，请检查代理或配置 NO_PROXY。"
        ),
    })
}

fn extract_url_from_detail(detail: &str) -> Option<&str> {
    let rest = detail.split_once("for url (")?.1;
    let url = rest.split_once(')')?.0.trim();
    (!url.is_empty()).then_some(url)
}

/// Mirrors `hyper_util::client::proxy::matcher::Matcher::from_env` precedence
/// and scheme acceptance while returning only the variable name (never its
/// value, which may carry proxy credentials).
fn active_proxy_var_from_lookup(
    target_url: Option<&str>,
    mut get_env: impl FnMut(&str) -> Option<String>,
) -> Option<&'static str> {
    if get_env("REQUEST_METHOD").is_some() {
        return None;
    }

    let parsed = target_url.and_then(|raw| url::Url::parse(raw).ok());
    if let (Some(url), Some((_, no_proxy))) = (
        parsed.as_ref(),
        first_defined(&["NO_PROXY", "no_proxy"], &mut get_env),
    ) && url_bypasses_proxy(url, &no_proxy)
    {
        return None;
    }

    let scheme_specific: &[&str] = match parsed.as_ref().map(url::Url::scheme) {
        Some("https") => &["HTTPS_PROXY", "https_proxy"],
        Some("http") => &["HTTP_PROXY", "http_proxy"],
        _ => &[],
    };

    if !scheme_specific.is_empty() {
        if let Some((name, val)) = first_defined(scheme_specific, &mut get_env)
            && is_active_proxy_value(&val)
        {
            return Some(name);
        }
        return first_defined(&["ALL_PROXY", "all_proxy"], &mut get_env)
            .and_then(|(name, val)| is_active_proxy_value(&val).then_some(name));
    }

    for group in [
        &["HTTPS_PROXY", "https_proxy"][..],
        &["HTTP_PROXY", "http_proxy"][..],
        &["ALL_PROXY", "all_proxy"][..],
    ] {
        if let Some((name, val)) = first_defined(group, &mut get_env)
            && is_active_proxy_value(&val)
        {
            return Some(name);
        }
    }
    None
}

fn first_defined(
    names: &[&'static str],
    get_env: &mut impl FnMut(&str) -> Option<String>,
) -> Option<(&'static str, String)> {
    for &name in names {
        if let Some(val) = get_env(name) {
            return Some((name, val));
        }
    }
    None
}

fn is_active_proxy_value(val: &str) -> bool {
    let val = val.trim();
    if val.is_empty() || val.chars().any(char::is_whitespace) {
        return false;
    }
    match val.split_once("://") {
        Some((scheme, rest)) => {
            !rest.is_empty()
                && matches!(
                    scheme.to_ascii_lowercase().as_str(),
                    "http" | "https" | "socks4" | "socks4a" | "socks5" | "socks5h"
                )
        }
        None => true,
    }
}

fn url_bypasses_proxy(url: &url::Url, no_proxy: &str) -> bool {
    let Some(raw_host) = url.host_str() else {
        return false;
    };
    let host = raw_host
        .trim_start_matches('[')
        .trim_end_matches(']')
        .to_ascii_lowercase();
    let target_port = url.port_or_known_default();

    no_proxy.split(',').map(str::trim).any(|entry| {
        if entry.is_empty() {
            return false;
        }
        if entry == "*" {
            return true;
        }
        let (entry_host, entry_port) = match entry.rsplit_once(':') {
            Some((h, p)) if !h.contains(':') || h.starts_with('[') => {
                (h, p.parse::<u16>().ok())
            }
            _ => (entry, None),
        };
        if entry_port.is_some() && entry_port != target_port {
            return false;
        }
        let domain = entry_host
            .trim_start_matches('[')
            .trim_end_matches(']')
            .trim_start_matches('.')
            .to_ascii_lowercase();
        let is_ip = host.parse::<std::net::IpAddr>().is_ok();
        !domain.is_empty()
            && (host == domain
                || (!is_ip
                    && host.len() > domain.len()
                    && host.ends_with(&domain)
                    && host.as_bytes()[host.len() - domain.len() - 1] == b'.'))
    })
}

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

    #[tokio::test]
    async fn reqwest_socks5_proxy_scheme_is_supported() {
        let dead_proxy_port = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        let client = reqwest::Client::builder()
            .proxy(reqwest::Proxy::all(format!("socks5://127.0.0.1:{dead_proxy_port}")).unwrap())
            .build()
            .unwrap();
        let err = client
            .get("http://private-server:1933/health")
            .send()
            .await
            .unwrap_err();
        let detail = Error::from_reqwest("HTTP request failed", err).to_string();
        assert!(
            !detail.contains("unsupported scheme socks5"),
            "socks feature should handle socks5:// scheme, got: {detail}"
        );
    }

    #[test]
    fn active_proxy_lookup_respects_scheme_precedence_no_proxy_and_redacts_values() {
        let env = |pairs: &[(&str, &str)]| {
            let map: std::collections::HashMap<String, String> = pairs
                .iter()
                .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
                .collect();
            move |key: &str| map.get(key).cloned()
        };

        assert_eq!(
            active_proxy_var_from_lookup(
                Some("http://private-server:1933/health"),
                env(&[("ALL_PROXY", "socks5://alice:secret@127.0.0.1:10808")]),
            ),
            Some("ALL_PROXY")
        );
        assert_eq!(
            active_proxy_var_from_lookup(
                Some("http://private-server:1933/health"),
                env(&[
                    ("ALL_PROXY", "socks5://127.0.0.1:10808"),
                    ("NO_PROXY", "localhost, .private-server"),
                ]),
            ),
            None
        );
        assert_eq!(
            active_proxy_var_from_lookup(
                Some("http://127.0.0.1:1933/health"),
                env(&[
                    ("ALL_PROXY", "socks5://127.0.0.1:10808"),
                    ("NO_PROXY", "127.0.0.1"),
                ]),
            ),
            None
        );
        assert_eq!(
            active_proxy_var_from_lookup(
                Some("http://private-server:1933/health"),
                env(&[
                    ("HTTP_PROXY", ""),
                    ("ALL_PROXY", "socks5h://127.0.0.1:10808"),
                ]),
            ),
            Some("ALL_PROXY")
        );
        assert_eq!(
            active_proxy_var_from_lookup(
                Some("http://private-server:1933/health"),
                env(&[("ALL_PROXY", "pac+http://127.0.0.1:10808")]),
            ),
            None
        );
    }
}
