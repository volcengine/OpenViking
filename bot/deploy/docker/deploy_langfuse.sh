#!/bin/bash
# Deploy local Langfuse using Docker Compose

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANGFUSE_DIR="$SCRIPT_DIR/langfuse"
LANGFUSE_ENV_FILE="$LANGFUSE_DIR/.env"

if ! command -v openssl >/dev/null 2>&1; then
  echo "Error: openssl is required to generate Langfuse deployment secrets."
  exit 1
fi

if [ ! -e "$LANGFUSE_ENV_FILE" ]; then
  umask 077
  postgres_password="$(openssl rand -hex 24)"
  clickhouse_password="$(openssl rand -hex 24)"
  minio_user="minio-$(openssl rand -hex 8)"
  minio_password="$(openssl rand -hex 32)"
  redis_password="$(openssl rand -hex 32)"
  langfuse_public_key="pk-lf-$(openssl rand -hex 16)"
  langfuse_secret_key="sk-lf-$(openssl rand -hex 32)"
  admin_password="$(openssl rand -hex 24)"

  printf '%s\n' \
    "POSTGRES_USER=postgres" \
    "POSTGRES_PASSWORD=$postgres_password" \
    "POSTGRES_DB=postgres" \
    "DATABASE_URL=postgresql://postgres:$postgres_password@postgres:5432/postgres" \
    "CLICKHOUSE_USER=clickhouse" \
    "CLICKHOUSE_PASSWORD=$clickhouse_password" \
    "MINIO_ROOT_USER=$minio_user" \
    "MINIO_ROOT_PASSWORD=$minio_password" \
    "REDIS_AUTH=$redis_password" \
    "SALT=$(openssl rand -hex 32)" \
    "ENCRYPTION_KEY=$(openssl rand -hex 32)" \
    "NEXTAUTH_SECRET=$(openssl rand -hex 32)" \
    "LANGFUSE_INIT_ORG_ID=org-$(openssl rand -hex 8)" \
    "LANGFUSE_INIT_PROJECT_ID=project-$(openssl rand -hex 8)" \
    "LANGFUSE_INIT_PROJECT_PUBLIC_KEY=$langfuse_public_key" \
    "LANGFUSE_INIT_PROJECT_SECRET_KEY=$langfuse_secret_key" \
    "LANGFUSE_INIT_USER_PASSWORD=$admin_password" \
    > "$LANGFUSE_ENV_FILE"
  chmod 600 "$LANGFUSE_ENV_FILE"
  echo "Generated unique Langfuse credentials in $LANGFUSE_ENV_FILE"
fi

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
echo "🔐 Credentials are stored in:"
echo "   $LANGFUSE_ENV_FILE"
echo ""
echo "📧 Login email:"
echo "   Email: admin@vikingbot.local"
echo "   Read LANGFUSE_INIT_USER_PASSWORD from the credentials file."
echo ""
echo "🔑 Configure VikingBot with LANGFUSE_INIT_PROJECT_PUBLIC_KEY and"
echo "   LANGFUSE_INIT_PROJECT_SECRET_KEY from the credentials file."
echo ""
echo "📝 To view logs: ${COMPOSE_CMD[*]} -f $LANGFUSE_DIR/docker-compose.yml logs -f"
echo "📝 To stop: ${COMPOSE_CMD[*]} -f $LANGFUSE_DIR/docker-compose.yml down"
