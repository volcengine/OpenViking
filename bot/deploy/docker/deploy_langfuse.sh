#!/bin/bash
# Deploy local Langfuse using Docker Compose

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANGFUSE_DIR="$SCRIPT_DIR/langfuse"

cd "$LANGFUSE_DIR"

echo "🚀 Starting Langfuse..."
docker-compose up -d

echo ""
echo "✅ Langfuse deployed successfully!"
echo ""
echo "🌐 Web UI: http://localhost:3000"
echo ""
echo "📧 Login credentials:"
echo "   Email: admin@vikingbot.local"
echo "   Password: vikingbot-admin-password-2026"
echo ""
echo "🔑 API keys:"
echo "   Public key: pk-lf-vikingbot-public-key-2026"
echo "   Secret key: sk-lf-vikingbot-secret-key-2026"
echo ""
echo "📝 To view logs: docker-compose -f $LANGFUSE_DIR/docker-compose.yml logs -f"
echo "📝 To stop: docker-compose -f $LANGFUSE_DIR/docker-compose.yml down"
