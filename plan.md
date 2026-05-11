````
# Private GitHub & GitLab Repo Support for OpenViking

## Context

In the `sstreichan/OpenViking` project, the command `ov add-resource <url>` needs
to be extended to support private GitHub and GitLab repositories.

Currently the command only works with public repos. The `path` parameter in
`add_resource()` (openviking_cli/client/http.py) is passed directly as a URL to
`POST /api/v1/resources`. The server clones the repo via `git clone <url>`,
which fails for private repos without credentials.

## Relevant Files (already known)

- `openviking_cli/client/http.py` — `add_resource()` method
- `openviking_cli/client/sync_http.py` — synchronous equivalent
- `openviking_cli/utils/config/ovcli_config.py` — CLI config (~/.openviking/ovcli.conf)
- `openviking_cli/utils/config/open_viking_config.py` — central server config
- `openviking_cli/setup_wizard.py` — interactive setup wizard
- `docs/en/guides/` — English documentation (VitePress)
- `docs/zh/guides/` — Chinese documentation (VitePress)
- `docs/.vitepress/` — VitePress configuration

---

## Task

Implement full support for private GitHub and GitLab repos across all phases below.
All changes must be backwards-compatible — public repos must continue to work
without a token.

---

## Phase 1 — Credential Storage

### 1.1 New File: `openviking_cli/utils/git_credentials.py`

Create a helper file with the following functions:

```python
def is_git_url(path: str) -> bool:
    """
    Returns True if path is a GitHub or GitLab HTTPS URL.
    Recognized formats:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://gitlab.com/owner/repo
    - https://gitlab.com/group/subgroup/repo
    - https://<any-self-hosted-gitlab-host>/...
    """

def inject_token_into_url(url: str, token: str) -> str:
    """
    Embeds the token into the URL using HTTPS Basic Auth.

    GitHub:  https://<token>@github.com/owner/repo.git
    GitLab:  https://oauth2:<token>@gitlab.com/owner/repo.git

    Rules:
    - github.com → token directly as username: <token>@
    - gitlab.com or any other host → oauth2:<token>@
    - Ensures the URL ends with .git
    - Raises ValueError if the URL does not use HTTPS scheme
    """

def strip_token_from_url(url: str) -> str:
    """
    Removes embedded credentials from a URL.
    https://token@github.com/... → https://github.com/...
    https://oauth2:token@gitlab.com/... → https://gitlab.com/...
    """

def resolve_git_token(url: str, credentials: dict[str, str]) -> str | None:
    """
    Looks up the matching token for the URL's hostname in credentials.
    Returns None if no token is found.
    Example: "https://github.com/..." → credentials.get("github.com")
    """

def mask_token_in_url(url: str) -> str:
    """
    Masks tokens in URLs for logging.
    https://ghp_xxx@github.com/... → https://*****@github.com/...
    """
```

### 1.2 Extend `ovcli_config.py`

Add a new optional field to the existing config dataclass:

```python
git_credentials: dict[str, str] = field(default_factory=dict)
# key = hostname (e.g. "github.com", "gitlab.mycompany.com")
# value = Personal Access Token
```

Extend the `load_ovcli_config()` function to read a new TOML section
`[git_credentials]` from `~/.openviking/ovcli.conf`:

```toml
[git_credentials]
"github.com" = "ghp_xxxxxxxxxxxxxxxxxxxx"
"gitlab.com" = "glpat-xxxxxxxxxxxxxxxxxxxx"
"gitlab.mycompany.com" = "glpat-xxxxxxxxxxxxxxxxxxxx"
```

Extend the config write function (if present) so that `git_credentials` is
serialized correctly.

When writing the config file: set file permissions to `0o600` (`chmod 600`)
if the file is newly created.

---

## Phase 2 — URL Transformation in the HTTP Client

### 2.1 `openviking_cli/client/http.py`

Add to the `__init__` of `AsyncHTTPClient`:

```python
self._git_credentials: dict[str, str] = {}
# Load from cli_config if available:
# self._git_credentials = cli_config.git_credentials
```

Update the `add_resource()` method. **Before** the block that checks
`path_obj = Path(path)`, insert the following logic:

```python
# If path is a Git URL and credentials are available, embed the token
if is_git_url(path):
    token = resolve_git_token(path, self._git_credentials)
    if token:
        path = inject_token_into_url(path, token)
    # Otherwise: pass URL unchanged (public repo)
```

Also add an optional parameter `git_token: Optional[str] = None` to the
`add_resource()` signature. If `git_token` is provided, it takes precedence
over the stored credential:

```python
if is_git_url(path):
    token = git_token or resolve_git_token(path, self._git_credentials)
    if token:
        path = inject_token_into_url(path, token)
```

### 2.2 `openviking_cli/client/sync_http.py`

Apply the same changes as in 2.1 to the synchronous client.
The synchronous client wraps the async client — update the method signature
and the call accordingly.

---

## Phase 3 — CLI Interface

### 3.1 New Subcommand: `ov configure git-credentials`

Implement the following CLI options within the existing CLI command structure:

```
ov configure git-credentials --host <hostname> --token <token>
    → Saves the token for the given host in ovcli.conf

ov configure git-credentials --list
    → Lists all stored hosts with masked tokens:
      github.com          ghp_**...**
      gitlab.com          glpat-**...**

ov configure git-credentials --host <hostname> --remove
    → Removes the entry for the host

ov configure git-credentials --host <hostname> --test <repo-url>
    → Tests whether the token for the host is valid by running
      `git ls-remote <authenticated-url>`.
      Outputs success or failure.
```

### 3.2 `--token` Flag for `ov add-resource`

Add an optional flag to the existing `add-resource` command:

```
ov add-resource <url> [--token <token>]
```

The token is **not** stored — it is only used for this single invocation.
Useful for CI/CD pipelines or one-time use.

Examples:
```bash
# With stored token (from ovcli.conf)
ov add-resource https://github.com/myorg/private-repo

# With one-time token
ov add-resource https://github.com/myorg/private-repo --token ghp_xxxx

# Public repo (unchanged, as before)
ov add-resource https://github.com/volcengine/OpenViking
```

---

## Phase 4 — Server-Side Token Sanitization

In the server code that handles `POST /api/v1/resources` (ingester/resource handler):

### 4.1 Remove Token from Stored Metadata

After a successful `git clone`:
1. Use the original URL (without token) in the stored metadata.
   Use `strip_token_from_url()` or an equivalent server-side function.
2. Ensure that `source_name` is derived from the **sanitized** URL
   (format: `owner/repo` or just `repo`).

### 4.2 Log Sanitization

Implement a log filter that masks URLs with embedded credentials before they
are written to logs:

```
# Before filter:
Cloning https://ghp_abc123@github.com/org/repo.git...

# After filter:
Cloning https://*****@github.com/org/repo.git...
```

The filter should be implemented as a logging middleware or formatter that
matches the regex pattern `://[^@\s]+@` in all log messages and replaces it
with `://*****@`.

### 4.3 Clean Git Remote After Cloning

After cloning: run `git remote set-url origin <url-without-token>` in the
cloned directory before any further processing steps.

---

## Phase 5 — Setup Wizard

In `openviking_cli/setup_wizard.py`: add a new optional step after the
existing API key step:

```
[Step X/N] Git Credentials for Private Repositories (optional)

Would you like to use private GitHub or GitLab repositories? [y/N]: _

  GitHub Personal Access Token (leave blank to skip):
  > Token: ____
  Required permissions: repo (read), or contents:read for fine-grained tokens

  GitLab Token (leave blank to skip):
  > Host (e.g. gitlab.com or gitlab.mycompany.com): ____
  > Token: ____
  Required permissions: read_repository

  Add another GitLab host? [y/N]: _

✓ Git credentials saved.
```

If the wizard step is skipped: do not write an empty `[git_credentials]`
block to the config.

---

## Phase 6 — Documentation

### 6.1 New File: `docs/en/guides/13-private-git-repos.md`

Create a new guide page with the following content (in English):

**Frontmatter:**
```yaml
***
title: Private Git Repositories
description: How to add private GitHub and GitLab repositories as resources
***
```

**Required sections:**

1. **Overview**
   Brief explanation of why tokens are needed and which platforms are supported
   (github.com, gitlab.com, self-hosted GitLab).

2. **Prerequisites**
   Which token types are supported and what permissions are required:
   - GitHub: Classic PAT with `repo` scope, or Fine-grained PAT with
     `Contents: Read-only`
   - GitLab: Personal Access Token with `read_repository` scope

3. **Quick Start**
   Minimal example first, ready to copy-paste immediately:
   ```bash
   ov configure git-credentials --host github.com --token ghp_xxxx
   ov add-resource https://github.com/myorg/private-repo
   ```

4. **Storing Credentials**
   All `ov configure git-credentials` subcommands with examples:
   `--host / --token`, `--list`, `--remove`, `--test`.
   Also show the resulting format in `~/.openviking/ovcli.conf`.

5. **One-time Token Usage**
   Explanation and example of the `--token` flag on `add-resource`.
   Use case: CI/CD, one-time use.

6. **GitLab (Self-Hosted)**
   Example for `gitlab.mycompany.com` with a custom hostname.
   Note: The hostname must exactly match the host in the URL.

7. **CI/CD Usage**
   Recommended pattern for automated pipelines:
   ```bash
   ov add-resource https://github.com/myorg/private-repo \
     --token $GITHUB_TOKEN
   ```
   Note: Do not use `ov configure git-credentials` in CI, as the config file
   is not persisted between runs.

8. **Security Considerations**
   - Config file has permissions `0o600`
   - Tokens never appear in logs or stored resource URIs
   - Recommendation: use Fine-grained PATs instead of Classic PATs
   - Token rotation: simply overwrite with `--host ... --token <new-token>`

9. **Troubleshooting**
   Common errors as a table:
   | Error | Cause | Solution |
   |-------|-------|----------|
   | `Authentication failed` | Token missing or expired | `ov configure git-credentials --host <host> --token <token>` |
   | `Repository not found` | No access to repo | Check token permissions |
   | `Host not found` | Self-hosted GitLab misconfigured | Hostname must exactly match the URL host |

### 6.2 New File: `docs/zh/guides/13-private-git-repos.md`

Create the same page in Simplified Chinese. Use the existing guides in
`docs/zh/guides/` as reference for style, terminology, and formatting.

### 6.3 Update VitePress Navigation

Update the VitePress config under `docs/.vitepress/`. Add the new guide
to the sidebar — after the entry for `04-authentication.md`:

```typescript
// English sidebar:
{ text: 'Private Git Repositories', link: '/en/guides/13-private-git-repos' }

// Chinese sidebar:
{ text: '私有 Git 仓库', link: '/zh/guides/13-private-git-repos' }
```

### 6.4 Update `docs/en/guides/01-configuration.md`

In the existing configuration guide: add a new `[git_credentials]` section
to the `ovcli.conf` options documentation. Find the section that describes
TOML sections and add:

```markdown
### `[git_credentials]`

Optional. Stores Personal Access Tokens for private GitHub and GitLab
repositories. Keys are hostnames, values are tokens.

```toml
[git_credentials]
"github.com" = "ghp_xxxxxxxxxxxxxxxxxxxx"
"gitlab.com" = "glpat-xxxxxxxxxxxxxxxxxxxx"
"gitlab.mycompany.com" = "glpat-xxxxxxxxxxxxxxxxxxxx"
```

See [Private Git Repositories](./13-private-git-repos.md) for setup instructions.
```

### 6.5 Update `README.md`, `README_CN.md`, `README_JA.md`

In each README: add a brief note below the section describing `ov add-resource`
for public repos — in English, Chinese, and Japanese respectively:

```markdown
**Private repositories** are supported via Personal Access Tokens.
See [Private Git Repositories](docs/en/guides/13-private-git-repos.md) for setup.
```

---

## Phase 7 — Skill & Error Message Updates

### 7.1 Context-Aware Error Message on Auth Failures

Find all locations in the code where Git clone errors are caught and returned
as user-facing error messages. Add a context-aware message when the HTTP
status code is 401/403 or the Git error message contains
`"Authentication failed"`, `"Repository not found"`, or
`"could not read Username"`:

```
Error: Failed to clone repository — authentication failed.

This may be a private repository. To add a private repo, configure a token:
  ov configure git-credentials --host github.com --token <your-token>

Or pass a token directly:
  ov add-resource <url> --token <your-token>

See: https://docs.openviking.ai/en/guides/13-private-git-repos
```

### 7.2 Setup Wizard Help Text

In the setup wizard: update the description text of the new Git credentials
step to reference the new documentation page
`docs/en/guides/13-private-git-repos.md`.

### 7.3 CLI Help Texts

All new CLI commands and flags must have precise `--help` texts:

- `ov configure git-credentials --help`:
  Short description + reference to the guide URL.
- `ov add-resource --help`:
  The new `--token` parameter with a short description and a note pointing to
  `ov configure git-credentials` for persistent use.

---

## Security Requirements (mandatory for all phases)

1. **No tokens in logs** — `mask_token_in_url()` must be used in all log calls
   that output URLs.
2. **No tokens in Viking URIs** — the stored URI of a resource must never
   contain a token.
3. **No tokens in API responses** — the server must not return the original
   `path` (with token) in any response.
4. **Config file permissions** — `~/.openviking/ovcli.conf` must be `0o600`
   when git_credentials are stored.
5. **No token caching beyond request lifetime** — keep tokens in memory only;
   never write them to temp files or the database.

---

## Tests

### Unit Tests (`tests/unit/test_git_credentials.py`)

```python
# Test: is_git_url correctly identifies GitHub and GitLab URLs
# Test: is_git_url returns False for local paths
# Test: inject_token_into_url for GitHub (token as username)
# Test: inject_token_into_url for GitLab (oauth2:<token>)
# Test: inject_token_into_url appends .git suffix if missing
# Test: strip_token_from_url removes GitHub token
# Test: strip_token_from_url removes GitLab oauth2 token
# Test: resolve_git_token finds matching token
# Test: resolve_git_token returns None if no token found
# Test: mask_token_in_url masks correctly
```

### Integration Tests (`tests/integration/test_add_resource_private.py`)

```python
# Test: add_resource with stored token in ovcli.conf
# Test: add_resource with --token flag overrides stored token
# Test: add_resource without token for public repo is unchanged
# Test: token is not stored in the resource URI
# Test: token does not appear in server logs (mock log handler)
```

---

## Acceptance Criteria

- [ ] `ov add-resource https://github.com/myorg/private-repo` works when a
      token for `github.com` is stored in `ovcli.conf`.
- [ ] `ov add-resource https://github.com/myorg/private-repo --token ghp_xxx`
      works without a stored token.
- [ ] `ov add-resource https://github.com/volcengine/OpenViking` (public repo)
      continues to work without any changes.
- [ ] The stored Viking URI of the resource contains no token.
- [ ] Server logs contain no plaintext token.
- [ ] `ov configure git-credentials --list` shows stored hosts with masked tokens.
- [ ] On auth failures, a context-aware error message with a resolution hint
      is displayed.
- [ ] `docs/en/guides/13-private-git-repos.md` exists and is complete.
- [ ] `docs/zh/guides/13-private-git-repos.md` exists and is complete.
- [ ] VitePress sidebar includes the new guide in both EN and ZH.
- [ ] `01-configuration.md` includes the new `[git_credentials]` section.
- [ ] All new unit and integration tests pass.
- [ ] Backwards compatibility: existing `ovcli.conf` without `[git_credentials]`
      works without errors.
````