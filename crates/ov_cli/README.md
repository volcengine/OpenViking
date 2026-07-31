# OpenViking CLI

Command-line interface for [OpenViking](https://github.com/volcengine/OpenViking), an Agent-native context database.

This package builds the native `ov` binary. Use it to configure an OpenViking endpoint, import resources, browse `viking://` paths, retrieve context, inspect server status, manage sessions, and run administrative workflows.

中文文档见 [README_CN.md](README_CN.md).

## Installation

### Install from npm

```bash
npm i -g @openviking/cli
```

The npm package installs the platform-specific `ov` binary for macOS, Linux, or Windows.

### Install from source

```bash
# OpenViking requires Rust >= 1.91.1.
cargo install --path crates/ov_cli
```

If you are developing inside this crate:

```bash
cd crates/ov_cli
cargo install --path .
```

## Configuration

The recommended setup path is the interactive config manager:

```bash
ov config
```

`ov config` can add, edit, delete, validate, and switch saved configs. It writes the active client config to `~/.openviking/ovcli.conf`. Saved named configs are stored as `~/.openviking/ovcli.conf.<name>`, and `ov config switch <name>` copies the selected saved config to the active config path.

Recent CLI versions require a saved display language before most commands run in non-interactive shells:

```bash
ov language en
# or
ov language zh-CN
```

For scripts and agents, prefer deterministic config commands and pass secrets through stdin or existing environment variables:

```bash
# OpenViking Service
printf '%s' "$OPENVIKING_API_KEY" | \
  ov config add ov-service --name prod --api-key-stdin --activate -o json

# Custom local server without auth
ov config add custom --name local --url http://127.0.0.1:1933 --activate -o json

# Custom remote server with a user API key
printf '%s' "$OPENVIKING_API_KEY" | \
  ov config add custom --name remote --url https://ov.example.com --api-key-stdin --activate -o json
```

Validate the active config:

```bash
ov config show
ov config list -o json
ov config validate
ov health
ov status
```

`ov config show` redacts secrets. Avoid printing raw `~/.openviking/ovcli.conf` unless you understand it may contain API keys.

### Manual config file

Manual editing is still supported. A minimal custom-server config looks like:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-api-key",
  "account": "acme",
  "user": "alice"
}
```

`account` and `user` are usually optional when using a regular user API key because the server can derive identity from the key. They are recommended for `trusted` auth mode and tenant-scoped operations. They are required for root-key-only configs because a root key has no built-in tenant identity.

For more setup details, see [docs/en/getting-started/05-cli-setup.md](../../docs/en/getting-started/05-cli-setup.md).

## Quick Start

```bash
# Check connectivity
ov health
ov status

# Add a resource and wait for processing
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/docs/en/about/01-about-us.md --wait

# Browse context
ov ls viking://resources
ov tree viking://resources -L 2
ov read viking://resources/...

# Retrieve context
ov find "what is openviking"
ov grep "openviking" --uri viking://resources
```

Run `ov --help` and `ov <command> --help` for the exact command surface of your installed version.

## Command Groups

### Resource Management

- `add-resource` - Import local files, directories, URLs, Git repositories, and supported document sources.
- `add-skill` - Add a skill from a directory, `SKILL.md`, or raw content.
- `skills` - List, find, show, update, remove, and validate installed skills.
- `export` / `import` - Export or import context as `.ovpack`.
- `backup` / `restore` - Back up and restore public OpenViking scopes as restore-only `.ovpack` files.

### Filesystem

- `ls` - List directory contents.
- `tree` - Show a hierarchical tree.
- `mkdir` - Create a directory.
- `rm` - Remove a resource or directory.
- `mv` - Move or rename a resource.
- `stat` - Show resource metadata.
- `attrs` - Get logical extended attributes.
- `get` - Download a file to a local path.

### Content Access

- `read` - Read L2 full content.
- `abstract` - Read L0 abstract content.
- `overview` - Read L1 overview content.
- `write` - Replace, append, or create text content.

### Search

- `find` - Semantic retrieval.
- `search` - Context-aware retrieval. Experimental.
- `grep` - Content pattern search.
- `glob` - File glob pattern search.

### Sessions And Memory

- `session new` - Create a session.
- `session list` - List sessions.
- `session get` - Get session details.
- `session get-session-context` - Get merged session context.
- `session add-message` / `session add-messages` - Add messages to a session.
- `session commit` - Archive messages and extract memories.
- `add-memory` - Create a session, add messages, and commit in one shot. Experimental.

### Interactive

- `tui` - Interactive file explorer.
- `chat` - Chat with the vikingbot agent.

### Status And Observability

- `health` - Quick health check.
- `status` - Aggregated server component status.
- `wait` - Wait for queued async processing.
- `task status` / `task list` - Track async tasks.
- `task watch` - Manage auto-refresh watch tasks.
- `observer queue` - Queue status.
- `observer vikingdb` - VikingDB status.
- `observer models` - VLM, embedding, and rerank model status.
- `observer retrieval` - Retrieval quality metrics.
- `observer fs` - Filesystem operation metrics.
- `observer system` - Overall system status.

### Configuration

- `config` - Interactive config manager.
- `config show` - Show the active config with secrets redacted.
- `config validate` - Validate the active config.
- `config list` - List saved configs.
- `config switch` - Switch the active config.
- `config add` - Add a saved config non-interactively.
- `config edit` - Edit a saved config non-interactively.
- `config delete` - Delete a saved config.
- `language` / `lang` - Choose CLI display language (`en` or `zh-CN`).
- `version` - Show CLI version.

### Versioned Workspace Snapshots

- `snapshot commit` - Create a workspace snapshot.
- `snapshot restore` - Restore a path or workspace to a previous snapshot.
- `snapshot show` - Show commit metadata or blob content.
- `snapshot log` - Walk snapshot history.
- `snapshot ignore-get` / `snapshot ignore-set` / `snapshot ignore-delete` - Manage account `.ovgitignore`.

### Relations And Privacy

- `relations` - List resource relations. Experimental.
- `link` - Create relation links. Experimental.
- `unlink` - Remove a relation link. Experimental.
- `privacy` - Manage privacy configuration categories, targets, versions, and active config.

### Admin

Use `--sudo` for commands that require the configured `root_api_key`.

- `admin create-account` - Create an account and first admin user.
- `admin list-accounts` - List accounts. ROOT only.
- `admin delete-account` - Delete an account. ROOT only.
- `admin register-user` - Register a user.
- `admin list-users` - List users in an account.
- `admin remove-user` - Remove a user.
- `admin set-role` - Change a user's role. ROOT only.
- `admin regenerate-key` - Rotate a user's API key.
- `admin migrate` - Migrate legacy agent/session data. ROOT only.
- `system` - Administrative system utility commands.
- `reindex` - Rebuild semantic and vector artifacts for a URI.

## Output Formats

The default output is human-readable table/card rendering. Use JSON for scripts:

```bash
ov -o json ls viking://resources
ov -o json config list
```

Some command help may also show the long `--output json` form. `-o json` is the compact form used throughout tests and automation examples.

## Examples

```bash
# Add URL and wait for processing
ov add-resource https://example.com/docs --wait --timeout 60

# Add a local directory with filters
ov add-resource ./dir \
  --wait --timeout 600 \
  --ignore-dirs "node_modules,dist" \
  --include "*.md,*.py" \
  --exclude "*.tmp,*.log"

# Import into a predictable parent path
ov add-resource ./docs -p "viking://resources/docs/{calendar:today}" --wait

# Search with filters
ov find "API authentication" --threshold 0.7 --limit 5
ov find "authentication" --uri viking://resources/project --level 0,1

# Recursive list
ov ls viking://resources --recursive

# Temporarily override identity from CLI flags
ov --account acme --user alice ls viking://

# Use a root API key for administrative commands
ov --sudo admin create-account acme --admin alice --seed alice-seed
ov admin register-user acme bob --role user --seed bob-seed
ov admin regenerate-key acme bob --seed bob-new-seed

# Glob search
ov glob "**/*.md" --uri viking://resources

# Session workflow
SESSION=$(ov -o json session new | jq -r '.result.session_id')
ov session add-message --session-id "$SESSION" --role user --content "Hello"
ov session commit --session-id "$SESSION"

# Watch task management
ov add-resource https://example.com/docs --to viking://resources/docs --watch-interval 60
ov task watch ls
ov task watch trigger viking://resources/docs
```

## Development

```bash
# Build
cargo build --release

# Smoke the exact binary that was built
target/release/ov --version
target/release/ov -o json health

# Run tests
cargo test

# Install locally
cargo install --path .
```

When driving external e2e harnesses, point them at `target/release/ov` explicitly instead of relying on an older `ov` that may already be installed on `PATH`.
