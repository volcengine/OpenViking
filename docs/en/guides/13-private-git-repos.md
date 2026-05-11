# Private Git Repositories

By default `ov add-resource <url>` works for public repositories on GitHub and GitLab. For private repositories you need to supply a personal access token (PAT). OpenViking supports three ways to provide that token, evaluated in priority order.

## Token resolution order

| Priority | Source | Applies to |
|----------|--------|------------|
| 1 (highest) | `--token` flag on the CLI | Single command |
| 2 | `GITHUB_TOKEN` / `GITLAB_TOKEN` environment variable | All repos on that host |
| 3 (lowest) | `git_credentials` map in `~/.openviking/ovcli.conf` | All repos on that host |

## Option 1 — Pass a token at the command line

```bash
ov add-resource https://github.com/my-org/private-repo --token ghp_xxxxxxxxxxxx
```

The `--token` flag is consumed by the Python CLI wrapper before the Rust binary sees the URL; the token never appears in process lists or log files.

## Option 2 — Environment variables

Export the token in your shell session (or CI/CD environment):

```bash
# GitHub
export GITHUB_TOKEN=ghp_xxxxxxxxxxxx
ov add-resource https://github.com/my-org/private-repo

# GitLab
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx
ov add-resource https://gitlab.com/my-group/private-project
```

The variables are read automatically for any `github.com` or `gitlab.com` URL.
For self-hosted instances set a credential entry instead (see Option 3).

## Option 3 — Persistent credentials in `ovcli.conf`

Use the interactive wizard to store a token once:

```bash
ov configure git-credentials
```

You will be prompted for the hostname and token:

```
Host (e.g. github.com): github.com
Token: ghp_xxxxxxxxxxxx
Credentials saved to ~/.openviking/ovcli.conf
```

After that every `ov add-resource` command for that host picks up the token automatically.

### Manual configuration

Alternatively, edit `~/.openviking/ovcli.conf` directly:

```jsonc
{
  // ... other settings ...
  "git_credentials": {
    "github.com": "ghp_xxxxxxxxxxxx",
    "gitlab.com": "glpat-xxxxxxxxxxxx",
    "gitlab.example.com": "glpat-xxxxxxxxxxxx"
  }
}
```

Keys are bare hostnames — no port, no path, no protocol.

## Self-hosted instances

Both self-hosted GitHub Enterprise and self-hosted GitLab are supported.
Use the actual hostname as the key:

```bash
ov configure git-credentials
# Host: git.corp.example.com
# Token: <your-PAT>

ov add-resource https://git.corp.example.com/team/repo
```

## Creating a personal access token

### GitHub

1. Go to **Settings → Developer settings → Personal access tokens → Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Select the `repo` scope (read access is sufficient for `add-resource`).
4. Copy the token — it is shown only once.

GitHub fine-grained tokens also work; grant **Contents: Read** permission for the target repository.

### GitLab

1. Go to **User Settings → Access Tokens**.
2. Click **Add new token**.
3. Select the `read_repository` scope.
4. Copy the token.

## Security notes

- Tokens stored in `ovcli.conf` are written as plain text. Protect the file with appropriate filesystem permissions (`chmod 600 ~/.openviking/ovcli.conf`).
- The `--token` value is stripped from the argument list before the Rust binary runs, so it does not appear in `ps` output.
- Tokens are not sent to the OpenViking server in any API request field; they are injected into the repository URL only for the duration of the clone/archive fetch operation.
- After cloning, OpenViking sanitizes the remote URL stored in metadata so the token is not persisted in the context database.

## Related

- [Configuration Reference](01-configuration.md) — full `ovcli.conf` schema
- [Deployment Guide](03-deployment.md) — running OpenViking in CI/CD
- [Encryption](08-encryption.md) — encrypting stored data at rest
