#!/bin/bash
# OpenViking MCP Server Setup - One-time setup

set -e

REPO_URL="${OPENVIKING_REPO:-https://github.com/volcengine/OpenViking.git}"
INSTALL_DIR="${OPENVIKING_DIR:-$HOME/code/openviking}"

echo "=== OpenViking MCP Server Setup ==="

# Check prerequisites
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Clone if not exists
if [ -d "$INSTALL_DIR" ]; then
    echo "✓ OpenViking already cloned at $INSTALL_DIR"
else
    echo "→ Cloning OpenViking to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR/examples/mcp-query"

# Install dependencies
echo "→ Installing dependencies..."
uv sync

# Create config if not exists
if [ -f "ov.conf" ]; then
    echo "✓ Config already exists (ov.conf)"
else
    echo "→ Creating config template..."
    cp ov.conf.example ov.conf
    echo ""
    echo "⚠️  ACTION REQUIRED: Edit ov.conf and add your API keys:"
    echo "   embedding.dense.api_key - Volcengine/Ark API key for embeddings"
    echo "   vlm.api_key             - Volcengine/Ark API key for LLM"
    echo ""
    echo "   File location: $INSTALL_DIR/examples/mcp-query/ov.conf"
    echo ""
    read -p "Press Enter after you've configured ov.conf..."
fi

echo ""
echo "=== Setup Complete ==="
echo "Start server: cd $INSTALL_DIR/examples/mcp-query && uv run server.py"
echo ""
echo "Tools available:"
echo "  - query: Full RAG pipeline (search + LLM answer)"
echo "  - search: Semantic search only"
echo "  - add_resource: Add files/URLs to knowledge base"
echo ""
echo "Connect to Claude:"
echo "  claude mcp add --transport http openviking http://localhost:8000/mcp"
