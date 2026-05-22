use url::Url;

/// Return `true` if the URL looks like a cloneable git URL.
pub fn is_git_url(url: &str) -> bool {
    url.starts_with("https://")
        || url.starts_with("http://")
        || url.starts_with("git@")
        || url.starts_with("git://")
        || url.starts_with("ssh://")
}

/// Inject a personal access token into an HTTPS/HTTP git URL.
///
/// GitHub hosts use bare token as userinfo (`token@host`).
/// GitLab and other hosts use `oauth2:token@host` per the GitLab PAT convention.
/// SSH (`git@`, `ssh://`) and `git://` URLs are returned unchanged.
pub fn inject_token(url: &str, token: &str) -> String {
    if !url.starts_with("https://") && !url.starts_with("http://") {
        return url.to_string();
    }

    let Ok(mut parsed) = Url::parse(url) else {
        return url.to_string();
    };

    let hostname = parsed.host_str().unwrap_or("").to_lowercase();
    if hostname.contains("github") {
        let _ = parsed.set_username(token);
        let _ = parsed.set_password(None);
    } else {
        let _ = parsed.set_username("oauth2");
        let _ = parsed.set_password(Some(token));
    }

    parsed.into()
}

/// Mask any embedded token in a URL for safe logging.
///
/// Replaces the userinfo portion with `***`.
pub fn mask_token_in_url(url: &str) -> String {
    if !url.starts_with("https://") && !url.starts_with("http://") {
        return url.to_string();
    }

    let Ok(mut parsed) = Url::parse(url) else {
        return url.to_string();
    };

    if parsed.username().is_empty() {
        return url.to_string();
    }

    let _ = parsed.set_username("***");
    let _ = parsed.set_password(None);
    parsed.into()
}

/// Extract a normalized hostname from a URL.
///
/// Handles standard HTTP(S) URLs and `git@` SSH URLs.
pub fn extract_url_host(url: &str) -> Option<String> {
    if let Some(rest) = url.strip_prefix("git@") {
        let host = rest.split(':').next().unwrap_or("").trim().to_lowercase();
        if host.is_empty() {
            return None;
        }
        return Some(host);
    }

    Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_lowercase()))
}

/// Look up a token for a URL from a credentials map.
pub fn get_token_for_url<'a>(
    url: &str,
    credentials: Option<&'a std::collections::HashMap<String, String>>,
) -> Option<&'a str> {
    let host = extract_url_host(url)?;
    let bare_host = host.split(':').next().unwrap_or(&host);

    if let Some(creds) = credentials {
        if let Some(token) = creds.get(&host).or_else(|| creds.get(bare_host)) {
            return Some(token);
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_git_url_https() {
        assert!(is_git_url("https://github.com/org/repo.git"));
    }

    #[test]
    fn test_is_git_url_ssh() {
        assert!(is_git_url("git@github.com:org/repo.git"));
    }

    #[test]
    fn test_is_git_url_local_path() {
        assert!(!is_git_url("/local/path/to/repo"));
    }

    #[test]
    fn test_inject_token_github() {
        let result = inject_token("https://github.com/org/repo", "mytoken");
        assert_eq!(result, "https://mytoken@github.com/org/repo");
    }

    #[test]
    fn test_inject_token_gitlab() {
        let result = inject_token("https://gitlab.com/group/repo", "gltoken");
        assert_eq!(result, "https://oauth2:gltoken@gitlab.com/group/repo");
    }

    #[test]
    fn test_inject_token_non_github_self_hosted() {
        let result = inject_token("https://git.example.com/repo", "tok");
        assert_eq!(result, "https://oauth2:tok@git.example.com/repo");
    }

    #[test]
    fn test_inject_token_ssh_unchanged() {
        let url = "git@github.com:org/repo.git";
        assert_eq!(inject_token(url, "mytoken"), url);
    }

    #[test]
    fn test_inject_token_replaces_existing() {
        let result = inject_token("https://oldtok@github.com/org/repo", "newtok");
        assert_eq!(result, "https://newtok@github.com/org/repo");
    }

    #[test]
    fn test_mask_token_in_url_masks() {
        let result = mask_token_in_url("https://mytoken@github.com/org/repo");
        assert_eq!(result, "https://***@github.com/org/repo");
        assert!(!result.contains("mytoken"));
    }

    #[test]
    fn test_mask_token_in_url_oauth2_format() {
        let result = mask_token_in_url("https://oauth2:gltoken@gitlab.com/group/repo");
        assert_eq!(result, "https://***@gitlab.com/group/repo");
        assert!(!result.contains("gltoken"));
    }

    #[test]
    fn test_mask_token_in_url_no_token_unchanged() {
        let url = "https://github.com/org/repo";
        assert_eq!(mask_token_in_url(url), url);
    }

    #[test]
    fn test_extract_url_host_https() {
        let result = extract_url_host("https://github.com/org/repo");
        assert_eq!(result, Some("github.com".to_string()));
    }

    #[test]
    fn test_extract_url_host_git_ssh() {
        let result = extract_url_host("git@gitlab.com:group/project.git");
        assert_eq!(result, Some("gitlab.com".to_string()));
    }

    #[test]
    fn test_extract_url_host_with_token() {
        let result = extract_url_host("https://mytoken@github.com/org/repo");
        assert_eq!(result, Some("github.com".to_string()));
    }

    #[test]
    fn test_get_token_for_url_exact_match() {
        let mut creds = std::collections::HashMap::new();
        creds.insert("github.com".to_string(), "mytoken".to_string());
        let result = get_token_for_url("https://github.com/org/repo", Some(&creds));
        assert_eq!(result, Some("mytoken"));
    }

    #[test]
    fn test_get_token_for_url_no_match() {
        let mut creds = std::collections::HashMap::new();
        creds.insert("github.com".to_string(), "mytoken".to_string());
        let result = get_token_for_url("https://gitlab.com/group/repo", Some(&creds));
        assert_eq!(result, None);
    }

    #[test]
    fn test_get_token_for_url_bare_host_match() {
        let mut creds = std::collections::HashMap::new();
        creds.insert("github.com".to_string(), "tok".to_string());
        let result = get_token_for_url("https://github.com:443/org/repo", Some(&creds));
        assert_eq!(result, Some("tok"));
    }
}
