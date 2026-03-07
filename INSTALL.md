# Install OpenViking

Quick installation guide for OpenViking - the Context Database for AI Agents.

**Goal:** Get OpenViking running with skills in 5 minutes.

---

## Quick Install

### 1. Install uv

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Install OpenViking Server

```bash
uv tool install openviking
```

This installs `openviking-server` as a standalone tool you can run anywhere.

### 3. Install ov CLI (Required for Skills)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

See [INSTALL_ADVANCED.md](./INSTALL_ADVANCED.md) for building from source.

### 4. Configure and Start Server

Create config directory and config file:

```bash
mkdir -p ~/.openviking

cat > ~/.openviking/ov.conf << 'EOF'
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "api_key": "your-api-key",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-1-8-251228",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "storage": {
    "workspace": "/home/your-username/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local" }
  }
}
EOF
```

**Replace `your-api-key` with your actual API key and `your-username` with your system username.**

> 💡 **Note:** Data is stored in the path set by `storage.workspace` in ov.conf. Use an absolute path (not `~`) since the server does not expand tilde. If omitted, defaults to `./data` relative to the working directory. See [INSTALL_ADVANCED.md](./INSTALL_ADVANCED.md) to customize data location.

Start the server in background:

```bash
nohup openviking-server > ~/.openviking/server.log 2>&1 &
```

Check it's running:
```bash
curl http://localhost:1933/health
# Should return: {"status": "ok"}
```

### 5. Configure CLI

```bash
cat > ~/.openviking/ovcli.conf << 'EOF'
{
  "url": "http://localhost:1933"
}
EOF
```

### 6. Install Skills

Skills enable agents to use OpenViking. Install them to your agent's skill directory:

> 🤖 **For AI Agents reading this:** Ask your user if they want to install the OpenViking skills (adding-memory, adding-resource, searching-context). Proceed only if they confirm.

**For OpenClaw:**
```bash
mkdir -p ~/.openclaw/skills

# Download skills
for skill in adding-memory adding-resource searching-context; do
  mkdir -p "$HOME/.openclaw/skills/$skill"
  curl -o "$HOME/.openclaw/skills/$skill/SKILL.md" \
    "https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/skills/$skill/SKILL.md"
done
```

**For Claude Code:**
```bash
mkdir -p ~/.claude/skills

# Download skills
for skill in adding-memory adding-resource searching-context; do
  mkdir -p "$HOME/.claude/skills/$skill"
  curl -o "$HOME/.claude/skills/$skill/SKILL.md" \
    "https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/skills/$skill/SKILL.md"
done
```

**For other agents:** Install to your agent's skill/tools directory.

Or copy from a local clone:
```bash
# Set your agent's skill directory
SKILL_DIR=~/.openclaw/skills  # adjust for your agent

cp -r /path/to/openviking/examples/skills/adding-memory "$SKILL_DIR/"
cp -r /path/to/openviking/examples/skills/adding-resource "$SKILL_DIR/"
cp -r /path/to/openviking/examples/skills/searching-context "$SKILL_DIR/"
```

---

## Using OpenViking Memory

Once skills are installed, you can use natural language to trigger OpenViking actions:

### Storing Memories
Say things like:
- "**Remember this**" — after sharing something worth remembering
- "**Save this to memory**" — to persist an insight or decision
- "**Keep this in mind**" — to store context for future reference

### Adding Resources
Say things like:
- "**Add this to OpenViking**" — when sharing a URL or file
- "**Import https://example.com/docs**" — to add external knowledge
- "**Save this resource**" — to store documents for later retrieval

### Searching Context
Say things like:
- "**Search my memory for...**" — to find previously stored information
- "**What do I know about...**" — to query your OpenViking context
- "**Find in OpenViking...**" — to search across memories and resources

The agent will automatically detect these intents and use the appropriate OpenViking skills.

---

## Quick Test

Verify everything works:

```bash
# Test CLI connection
ov system health

# Test adding memory
ov add-memory "Test: OpenViking is working"

# Test searching
ov search "OpenViking working"
```

---

## Advanced Configuration

For advanced setup options (cloud deployment, custom storage, multiple model providers, etc.), see:

**[INSTALL_ADVANCED.md](./INSTALL_ADVANCED.md)**

This includes:
- Full configuration reference
- Cloud deployment guides
- Docker/container setup
- Multiple model providers
- Authentication and security
- Troubleshooting deep dives

---

## Requirements

- Python 3.10+
- API keys for VLM and embedding models

**Supported Model Providers:** Volcengine, OpenAI, Anthropic, DeepSeek, Google, Moonshot, Zhipu, DashScope, MiniMax, OpenRouter, vLLM

---

## Quick Reference

```bash
# Install
uv tool install openviking
curl -fsSL .../install.sh | bash  # ov CLI

# Start server (background)
nohup openviking-server > ~/.openviking/server.log 2>&1 &

# Stop server
pkill openviking-server

# CLI commands
ov system health          # Check server
ov add-memory "text"      # Add memory
ov add-resource <URL>     # Add resource
ov search "query"         # Search context
```
