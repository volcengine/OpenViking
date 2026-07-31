# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Utilities for code hosting platform URL parsing.

This module provides shared functionality for parsing URLs from code hosting
platforms like GitHub and GitLab.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from openviking_cli.utils.config import get_openviking_config

# Platform-specific domain lists select specialized behavior. All other
# supported Git hosts live in the generic ``code_hosting_domains`` allowlist.
_CODE_HOSTING_DOMAIN_CONFIG_FIELDS = (
    "github_domains",
    "gitlab_domains",
    "azure_devops_domains",
    "code_hosting_domains",
)

# Repo-page path segments that mark an http(s) URL as a browse page rather
# than a cloneable repository (github.com/org/repo/issues/123 etc.).
_NON_REPO_PATH_SEGMENTS = frozenset(
    {
        "blob",
        "commit",
        "commits",
        "issues",
        "merge_requests",
        "pull",
        "pulls",
        "raw",
        "releases",
        "wiki",
    }
)

# Hosting-specific browse routes for public platforms in the generic domain
# category. These are URL parsing profiles, not additional allowlists: the host
# must still be configured in ``code_hosting_domains`` before they apply.
_KNOWN_PLATFORM_NON_REPO_PATH_SEGMENTS = {
    "gitcode.com": frozenset({"blob", "raw"}),
    "bitbucket.org": frozenset({"src"}),
    "codeberg.org": frozenset({"src"}),
    "gitea.com": frozenset({"src"}),
}

# Public platforms where ``owner/repo/tree/ref/...`` is an unambiguous browse
# route. This profile must not be applied to generic configured Git hosts:
# there, a nested repository can legitimately contain a ``tree`` namespace.
_KNOWN_PLATFORM_TREE_ROUTE_HOSTS = frozenset(
    {"atomgit.com", "git.sr.ht", "gitcode.com", "gitee.com"}
)

# Top-level namespaces reserved by each hosting platform. Keeping these rules
# attached to platform categories prevents generic custom hosts from inheriting
# unrelated GitHub/GitLab route semantics.
_COMMON_RESERVED_TOP_LEVEL_SEGMENTS = frozenset(
    {"dashboard", "help", "join", "login", "new", "search", "settings"}
)
_PLATFORM_RESERVED_TOP_LEVEL_SEGMENTS = {
    "github_domains": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS
    | frozenset(
        {
            "apps",
            "codespaces",
            "collections",
            "enterprise",
            "features",
            "issues",
            "marketplace",
            "notifications",
            "orgs",
            "pricing",
            "pulls",
            "sponsors",
            "topics",
            "trending",
        }
    ),
    "gitlab_domains": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS
    | frozenset(
        {
            "-",
            "admin",
            "explore",
            "groups",
            "users",
        }
    ),
}

# Public-platform URL profiles are parsing rules, not an allowlist. A host must
# still be present in one of the configured domain lists before these apply.
_KNOWN_PLATFORM_RESERVED_TOP_LEVEL_SEGMENTS = {
    "gitcode.com": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS | frozenset({"explore"}),
    "gitee.com": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS
    | frozenset({"enterprise", "explore", "organizations"}),
    "bitbucket.org": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS | frozenset({"account", "product"}),
    "codeberg.org": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS | frozenset({"explore"}),
    "gitea.com": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS | frozenset({"explore"}),
    "atomgit.com": _COMMON_RESERVED_TOP_LEVEL_SEGMENTS | frozenset({"explore"}),
}

# Generic Git hosts may use nested namespaces when a clone URL ends in .git.
# GitHub is explicitly excluded because its archive fast path is owner/repo
# only; accepting a nested URL would fetch a different repository.
_NESTED_REPOSITORY_DOMAIN_CONFIG_FIELDS = (
    "gitlab_domains",
    "code_hosting_domains",
)


def _get_code_config():
    return get_openviking_config().code


def _get_domains_for_field(field_name: str, code_config=None) -> list[str]:
    """Return one configured platform-domain category."""
    if code_config is None:
        code_config = _get_code_config()
    return list(getattr(code_config, field_name, []) or [])


def get_configured_code_hosting_domains(code_config=None) -> set[str]:
    """Return the union of every platform-specific and generic domain list."""
    if code_config is None:
        code_config = _get_code_config()

    domains: set[str] = set()
    for field_name in _CODE_HOSTING_DOMAIN_CONFIG_FIELDS:
        domains.update(domain.lower() for domain in _get_domains_for_field(field_name, code_config))
    return domains


def _matches_domain_field(parsed: ParseResult, field_name: str) -> bool:
    return _domain_matches(parsed, _get_domains_for_field(field_name))


def _get_reserved_top_level_segments(parsed: ParseResult) -> frozenset[str]:
    """Return reserved first-path segments for the URL's configured platform."""
    reserved: set[str] = set()
    for field_name, segments in _PLATFORM_RESERVED_TOP_LEVEL_SEGMENTS.items():
        if _matches_domain_field(parsed, field_name):
            reserved.update(segments)
    reserved.update(
        _KNOWN_PLATFORM_RESERVED_TOP_LEVEL_SEGMENTS.get(
            (parsed.hostname or "").lower(), frozenset()
        )
    )
    return frozenset(reserved)


def _get_known_platform_non_repo_segments(parsed: ParseResult) -> frozenset[str]:
    """Return browse-route markers for a configured public platform."""
    return _KNOWN_PLATFORM_NON_REPO_PATH_SEGMENTS.get((parsed.hostname or "").lower(), frozenset())


def _find_known_platform_non_repo_segment(
    parsed: ParseResult,
    path_parts: list[str],
) -> Optional[int]:
    """Return the index of a known platform browse-route marker, if any.

    Known public platforms can support nested repository paths, so their route
    marker is not necessarily the third path segment. Generic configured hosts
    intentionally do not use this scan because a nested namespace may
    legitimately be named ``blob`` or ``src``.
    """
    markers = _get_known_platform_non_repo_segments(parsed)
    if not markers:
        return None
    return next(
        (index for index, part in enumerate(path_parts[2:], start=2) if part in markers),
        None,
    )


def _supports_nested_repository_host(host: str) -> bool:
    return _supports_nested_repository_path(urlparse(f"ssh://{host}"))


def _supports_nested_repository_path(parsed: ParseResult) -> bool:
    return bool(
        parsed.hostname
        and not _matches_domain_field(parsed, "github_domains")
        and any(
            _matches_domain_field(parsed, field_name)
            for field_name in _NESTED_REPOSITORY_DOMAIN_CONFIG_FIELDS
        )
    )


def _looks_like_commit_sha(ref: str) -> bool:
    """Return True if ref looks like a git commit SHA (7-40 hex chars)."""
    return 7 <= len(ref) <= 40 and all(c in "0123456789abcdefABCDEF" for c in ref)


def _domain_matches(parsed: ParseResult, domains: Iterable[str]) -> bool:
    """Return True when parsed URL host matches configured domains.

    ``urlparse().netloc`` includes optional userinfo and port values. Repository
    clone URLs commonly use forms like ``ssh://git@github.com/org/repo.git``,
    where the netloc is ``git@github.com`` but the actual host is
    ``github.com``.
    """
    hostname = parsed.hostname
    if not hostname:
        return False

    normalized_domains = {domain.lower() for domain in domains}
    host = hostname.lower()
    candidates = {host}

    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        candidates.add(f"{host}:{port}")

    return any(candidate in normalized_domains for candidate in candidates)


def _extract_host(url: str) -> str:
    """Extract normalized host for supported git/code-hosting URL forms."""
    if url.startswith("git@"):
        rest = url[4:]
        if ":" not in rest:
            return ""
        return rest.split(":", 1)[0].strip().lower()

    parsed = urlparse(url)
    return (parsed.hostname or parsed.netloc or "").strip().lower()


def _get_all_domains() -> list[str]:
    return sorted(get_configured_code_hosting_domains())


def _get_azure_devops_domains() -> set[str]:
    return set(_get_domains_for_field("azure_devops_domains"))


def _sanitize_segment(segment: str) -> str:
    decoded_segment = unquote(segment)
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in decoded_segment)


def _extract_azure_devops_repo_parts(path_parts: list[str]) -> Optional[list[str]]:
    """Return Azure DevOps repository path parts ending in repo name."""
    try:
        git_index = path_parts.index("_git")
    except ValueError:
        return None

    if git_index < 2 or git_index + 1 >= len(path_parts) or len(path_parts) != git_index + 2:
        return None

    repo_parts = path_parts[:git_index] + [path_parts[git_index + 1]]
    if not all(repo_parts):
        return None
    return repo_parts


def _extract_azure_devops_ssh_repo_parts(path_parts: list[str]) -> Optional[list[str]]:
    """Return Azure DevOps SSH repository path parts ending in repo name."""
    if len(path_parts) != 4 or path_parts[0] != "v3":
        return None

    repo_parts = path_parts[1:]
    if not all(repo_parts):
        return None
    return repo_parts


def _is_azure_devops_browse_url(query: str) -> bool:
    """Return True for Azure DevOps repo browsing URLs like ?path=/README.md."""
    return "path" in parse_qs(query, keep_blank_values=True)


@dataclass(frozen=True)
class ParsedGitRepoURL:
    """A validated Git source normalized for cloning and repository metadata."""

    clone_url: str
    repo_path: str
    route_kind: Literal["clone", "tree", "commit"]
    branch: Optional[str] = None
    commit: Optional[str] = None


def _strip_repo_git_suffix(repo_parts: list[str]) -> Optional[list[str]]:
    """Return repository identity parts with only the final ``.git`` removed."""
    if not repo_parts:
        return None

    normalized = list(repo_parts)
    if normalized[-1].endswith(".git"):
        normalized[-1] = normalized[-1][:-4]
    if not normalized[-1]:
        return None
    return normalized


def _repo_path_from_parts(repo_parts: list[str]) -> Optional[str]:
    normalized = _strip_repo_git_suffix(repo_parts)
    if normalized is None:
        return None
    return "/".join(_sanitize_segment(part) for part in normalized)


def _build_url_git_result(
    parsed: ParseResult,
    repo_parts: list[str],
    *,
    clone_parts: Optional[list[str]] = None,
    route_kind: Literal["clone", "tree", "commit"] = "clone",
    branch: Optional[str] = None,
    commit: Optional[str] = None,
) -> Optional[ParsedGitRepoURL]:
    """Build a strict result for URL-style Git sources."""
    repo_path = _repo_path_from_parts(repo_parts)
    if repo_path is None:
        return None

    clone_path = "/" + "/".join(clone_parts or repo_parts)
    clone_url = parsed._replace(path=clone_path, params="", query="", fragment="").geturl()
    return ParsedGitRepoURL(
        clone_url=clone_url,
        repo_path=repo_path,
        route_kind=route_kind,
        branch=branch,
        commit=commit,
    )


def _build_scp_git_result(
    url: str,
    repo_parts: list[str],
    *,
    identity_parts: Optional[list[str]] = None,
) -> Optional[ParsedGitRepoURL]:
    """Build a strict result for scp-style ``git@host:path`` sources."""
    repo_path = _repo_path_from_parts(identity_parts or repo_parts)
    if repo_path is None:
        return None
    return ParsedGitRepoURL(clone_url=url, repo_path=repo_path, route_kind="clone")


def _parse_scp_git_repo_url(url: str) -> Optional[ParsedGitRepoURL]:
    """Parse a strict scp-style Git repository URL."""
    rest = url[4:]
    if ":" not in rest:
        return None

    host, path = rest.split(":", 1)
    host = host.strip().lower()
    if not host or host not in get_configured_code_hosting_domains():
        return None

    path_parts = [part for part in path.split("/") if part]
    if host in _get_azure_devops_domains():
        azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
        if azure_repo_parts is None:
            return None
        return _build_scp_git_result(
            url,
            path_parts,
            identity_parts=azure_repo_parts,
        )

    if not path_parts:
        return None

    # The scp-style form is an explicit Git transport, not an HTTP browse URL.
    # Preserve the complete path and let the remote decide whether it exists.
    return _build_scp_git_result(url, path_parts)


def _is_known_platform_tree_route(parsed: ParseResult, path_parts: list[str]) -> bool:
    """Return whether the URL uses a known platform's owner/repo tree route."""
    return (
        (parsed.hostname or "").lower() in _KNOWN_PLATFORM_TREE_ROUTE_HOSTS
        and len(path_parts) >= 3
        and path_parts[2] == "tree"
    )


def parse_git_repo_url(url: str) -> Optional[ParsedGitRepoURL]:
    """Parse a whitelisted, cloneable Git source URL.

    Unlike :func:`parse_code_hosting_url`, this is a strict ingress parser.
    Browse pages and ambiguous paths return ``None``. Accepted tree/commit
    snapshot URLs are normalized to a cloneable base URL with their ref
    returned separately. Explicit Git transports preserve any non-empty
    repository path because they do not share HTTP browse-route ambiguity.
    """
    if url.startswith("git@"):
        return _parse_scp_git_repo_url(url)

    if not url.startswith(("http://", "https://", "git://", "ssh://")):
        return None

    parsed = urlparse(url)
    if not _domain_matches(parsed, _get_all_domains()):
        return None

    path_parts = [part for part in parsed.path.split("/") if part]

    # Azure DevOps has a distinct repository grammar for both HTTPS and SSH.
    if _domain_matches(parsed, list(_get_azure_devops_domains())):
        azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
        if azure_repo_parts is not None:
            if _is_azure_devops_browse_url(parsed.query):
                return None
            return _build_url_git_result(
                parsed,
                azure_repo_parts,
                clone_parts=path_parts,
            )

        azure_ssh_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
        if azure_ssh_repo_parts is not None:
            return _build_url_git_result(
                parsed,
                azure_ssh_repo_parts,
                clone_parts=path_parts,
            )
        return None

    if not path_parts:
        return None

    # Explicit Git transports have no browser-route ambiguity. Preserve their
    # complete repository path instead of applying HTTP-only tree/blob/reserved
    # namespace rules. Azure DevOps remains handled by its grammar above.
    if parsed.scheme in {"git", "ssh"}:
        return _build_url_git_result(parsed, path_parts)

    if path_parts[0].lower() in _get_reserved_top_level_segments(parsed):
        return None
    if len(path_parts) == 1:
        return None

    # GitLab's /-/ separator is unambiguous: everything before it is the
    # possibly nested repository path, and everything after it is a web route.
    dash_index = (
        path_parts.index("-")
        if _matches_domain_field(parsed, "gitlab_domains") and "-" in path_parts
        else -1
    )
    if dash_index >= 2:
        repo_parts = path_parts[:dash_index]
        browse_parts = path_parts[dash_index + 1 :]
        if len(browse_parts) == 2 and browse_parts[0] == "tree":
            ref = unquote(browse_parts[1])
            if not ref:
                return None
            if _looks_like_commit_sha(ref):
                return _build_url_git_result(
                    parsed,
                    repo_parts,
                    route_kind="tree",
                    commit=ref,
                )
            return _build_url_git_result(
                parsed,
                repo_parts,
                route_kind="tree",
                branch=ref,
            )
        if (
            len(browse_parts) == 2
            and browse_parts[0] == "commit"
            and _looks_like_commit_sha(browse_parts[1])
        ):
            return _build_url_git_result(
                parsed,
                repo_parts,
                route_kind="commit",
                commit=browse_parts[1],
            )
        return None

    # A known platform's owner/repo/tree/ref route takes precedence over a
    # trailing .git filename. This rejects browse files such as config.git.
    if _is_known_platform_tree_route(parsed, path_parts):
        if len(path_parts) != 4:
            return None
        ref = unquote(path_parts[3])
        if not ref:
            return None
        if _looks_like_commit_sha(ref):
            return _build_url_git_result(
                parsed,
                path_parts[:2],
                route_kind="tree",
                commit=ref,
            )
        return _build_url_git_result(
            parsed,
            path_parts[:2],
            route_kind="tree",
            branch=ref,
        )

    # Exact commit routes are accepted only when the pin is SHA-shaped.
    if len(path_parts) == 4 and path_parts[2] == "commit" and _looks_like_commit_sha(path_parts[3]):
        return _build_url_git_result(
            parsed,
            path_parts[:2],
            route_kind="commit",
            commit=path_parts[3],
        )

    if _find_known_platform_non_repo_segment(parsed, path_parts) is not None:
        return None

    has_git_suffix = path_parts[-1].endswith(".git")
    if (
        len(path_parts) >= 3
        and path_parts[2] in _NON_REPO_PATH_SEGMENTS
        and not (has_git_suffix and len(path_parts) == 3)
    ):
        return None

    # A trailing .git is the strong clone signal. It wins over generic route
    # words on nested-capable hosts, so a subgroup literally named "tree" is
    # not mistaken for a branch.
    if has_git_suffix:
        if len(path_parts) == 2 or (
            len(path_parts) > 2 and _supports_nested_repository_path(parsed)
        ):
            return _build_url_git_result(parsed, path_parts)
        return None

    if len(path_parts) == 2:
        return _build_url_git_result(parsed, path_parts)

    # Without the strong .git signal, only an exact single-segment tree ref is
    # accepted. Longer paths are ambiguous with slashed refs or file browsing.
    if len(path_parts) == 4 and path_parts[2] == "tree":
        ref = unquote(path_parts[3])
        if not ref:
            return None
        if _looks_like_commit_sha(ref):
            return _build_url_git_result(
                parsed,
                path_parts[:2],
                route_kind="tree",
                commit=ref,
            )
        return _build_url_git_result(
            parsed,
            path_parts[:2],
            route_kind="tree",
            branch=ref,
        )
    return None


def parse_code_hosting_url(url: str) -> Optional[str]:
    """Parse code hosting platform URL to get org/repo path.

    Args:
        url: Code hosting URL like https://github.com/volcengine/OpenViking
             or git@github.com:volcengine/OpenViking.git

    Returns:
        org/repo path like "volcengine/OpenViking" or None if not a valid
        code hosting URL
    """
    parsed_git_url = parse_git_repo_url(url)
    # Keep this metadata helper's historical owner/repo contract. The strict
    # ingress parser accepts single-segment SSH/git repositories for cloning,
    # while callers that need a namespaced repository identity continue to use
    # their existing fallback naming.
    if parsed_git_url is not None and "/" in parsed_git_url.repo_path:
        return parsed_git_url.repo_path

    all_domains = _get_all_domains()
    # Handle git@ SSH URLs: git@host:org/repo.git
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return None
        host_part, path_part = url[4:].split(":", 1)
        if host_part not in all_domains:
            return None
        path_parts = [p for p in path_part.split("/") if p]
        if host_part in _get_azure_devops_domains():
            azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
            if azure_repo_parts:
                return "/".join(
                    _sanitize_segment(part.removesuffix(".git")) for part in azure_repo_parts
                )
        if len(path_parts) < 2:
            return None
        # Only platforms configured with nested namespaces keep every segment.
        if path_parts[-1].endswith(".git") and _supports_nested_repository_host(host_part):
            repo_parts = list(path_parts)
        else:
            repo_parts = path_parts[:2]
        if repo_parts[-1].endswith(".git"):
            repo_parts[-1] = repo_parts[-1][:-4]
        return "/".join(_sanitize_segment(part) for part in repo_parts)

    if not url.startswith(("http://", "https://", "git://", "ssh://")):
        return None

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    if _domain_matches(parsed, list(_get_azure_devops_domains())):
        azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
        if azure_repo_parts is None:
            azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
        if azure_repo_parts:
            return "/".join(
                _sanitize_segment(part.removesuffix(".git")) for part in azure_repo_parts
            )
        return None

    # GitLab "/-/" browse separator: everything before "-" is the full
    # (possibly nested) repository path.
    from_dash_separator = False
    if (
        _matches_domain_field(parsed, "gitlab_domains")
        and "-" in path_parts
        and path_parts.index("-") >= 2
    ):
        path_parts = path_parts[: path_parts.index("-")]
        from_dash_separator = True

    # For code hosting URLs with org/repo structure
    if _domain_matches(parsed, all_domains) and len(path_parts) >= 2:
        has_git_suffix = path_parts[-1].endswith(".git")
        known_browse_index = _find_known_platform_non_repo_segment(parsed, path_parts)
        # Segments between repo and filename that mark a browse URL
        # (org/repo/blob/main/file.git must not be taken as a nested path).
        browse_markers = set(path_parts[2:-1]) & (_NON_REPO_PATH_SEGMENTS | {"tree"})
        if known_browse_index is not None:
            repo_parts = path_parts[:known_browse_index]
        elif from_dash_separator or (
            has_git_suffix and not browse_markers and _supports_nested_repository_path(parsed)
        ):
            repo_parts = list(path_parts)
        else:
            # Plain browser URL: take first two parts (org/repo)
            repo_parts = path_parts[:2]
        if repo_parts[-1].endswith(".git"):
            repo_parts[-1] = repo_parts[-1][:-4]
        return "/".join(_sanitize_segment(part) for part in repo_parts)

    return None


def is_github_url(url: str) -> bool:
    """Check if a URL is a GitHub URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitHub URL
    """
    config = get_openviking_config()
    return _extract_host(url) in config.code.github_domains


def is_gitlab_url(url: str) -> bool:
    """Check if a URL is a GitLab URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitLab URL
    """
    config = get_openviking_config()
    return _extract_host(url) in config.code.gitlab_domains


def is_code_hosting_url(url: str) -> bool:
    """Check if a URL is a code hosting platform URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a code hosting platform URL
    """
    all_domains = _get_all_domains()

    # Handle git@ SSH URLs
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return False
        host_part = url[4:].split(":", 1)[0]
        return host_part in all_domains

    return _domain_matches(urlparse(url), all_domains)


def is_code_hosting_blob_url(url: str) -> bool:
    """Check whether a URL points to a single file on a code hosting platform.

    Recognizes GitHub/GitLab ``blob`` URLs and GitHub ``raw`` URLs, e.g.
    ``https://github.com/org/repo/blob/main/README.md`` or
    ``https://raw.githubusercontent.com/org/repo/main/README.md``. These
    historically go through HTTPAccessor (with optional blob->raw rewriting),
    not the recursive web crawler.
    """
    if not url.startswith(("http://", "https://")):
        return False

    config = get_openviking_config()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False

    raw_hosts = {"raw.githubusercontent.com"}
    if host in raw_hosts:
        return True

    code_hosts = set(config.code.github_domains) | set(config.code.gitlab_domains)
    if host not in code_hosts:
        return False

    path_parts = [p for p in parsed.path.split("/") if p]
    return "blob" in path_parts or "raw" in path_parts


def validate_git_ssh_uri(url: str) -> None:
    """Validate a git@ SSH URI format.

    Args:
        url: URL to validate (e.g. git@github.com:org/repo.git)

    Raises:
        ValueError: If the URL is not a valid git@ SSH URI
    """
    if not url.startswith("git@"):
        raise ValueError(f"Not a git@ SSH URI: {url}")
    rest = url[4:]
    if ":" not in rest or not rest.split(":", 1)[1]:
        raise ValueError(f"Invalid git@ SSH URI (missing colon or empty path): {url}")


def is_git_repo_url(url: str) -> bool:
    """Strict check for cloneable git repository URLs.

    Distinguishes repo URLs (github.com/org/repo) from non-repo URLs
    (github.com/org/repo/issues/123). The domain must always be whitelisted.

    Accepted http(s) shapes:
    - owner/repo, with or without a .git suffix
    - nested clone URLs ending in .git, e.g. GitLab subgroups or
      GitCode/Gitee nested groups (host/group/subgroup/repo.git)
    - browser tree URLs with an unambiguous single-segment ref:
      owner/repo/tree/<ref> and GitLab-style group/.../repo/-/tree/<ref>
    - commit pins: owner/repo/commit/<sha> and group/.../repo/-/commit/<sha>
    - Azure DevOps org/project/_git/repo (browse URLs with ?path= excluded)

    Explicit git@, ssh://, and git:// transports accept any non-empty path on
    a configured host. Their complete path is preserved as the repository
    identity.

    Rejected: platform-reserved top-level pages (topics, orgs, groups, ...),
    repo browse pages (issues, pull, blob, ...), and Azure DevOps URLs
    without the _git form.

    Args:
        url: URL to check

    Returns:
        True if the URL points to a cloneable git repository
    """
    return parse_git_repo_url(url) is not None
