#!/bin/bash
# Deploy local Langfuse using Docker Compose

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANGFUSE_DIR="$SCRIPT_DIR/langfuse"

cd "$LANGFUSE_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

echo "🚀 Starting Langfuse..."
"${COMPOSE_CMD[@]}" up -d

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
echo "📝 To view logs: ${COMPOSE_CMD[*]} -f $LANGFUSE_DIR/docker-compose.yml logs -f"
echo "📝 To stop: ${COMPOSE_CMD[*]} -f $LANGFUSE_DIR/docker-compose.yml down"
