#!/bin/bash
# Deploy local Langfuse using Docker Compose

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANGFUSE_DIR="$SCRIPT_DIR/langfuse"
LANGFUSE_ENV_FILE="$LANGFUSE_DIR/.env"

RESET_EXISTING=false
if [ "$#" -gt 1 ]; then
  echo "Error: expected at most one argument." >&2
  echo "Usage: $0 [--reset]" >&2
  exit 2
fi

case "${1:-}" in
  "")
    ;;
  --reset)
    RESET_EXISTING=true
    ;;
  -h|--help)
    echo "Usage: $0 [--reset]"
    echo "  --reset  Delete existing local Langfuse data and generate new credentials."
    exit 0
    ;;
  *)
    echo "Error: unsupported argument '$1'." >&2
    echo "Usage: $0 [--reset]" >&2
    exit 2
    ;;
esac

cd "$LANGFUSE_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

generate_env_file() {
  local target_file="$1"
  if ! command -v openssl >/dev/null 2>&1; then
    echo "Error: openssl is required to generate Langfuse deployment secrets." >&2
    return 1
  fi

  umask 077
  local postgres_password
  local clickhouse_password
  local minio_user
  local minio_password
  local redis_password
  local langfuse_public_key
  local langfuse_secret_key
  local admin_password
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
    > "$target_file"
  chmod 600 "$target_file"
}

COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$LANGFUSE_DIR")}"
existing_postgres_volumes="$(
  docker volume ls \
    --quiet \
    --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.volume=langfuse_postgres_data"
)"

if [ ! -e "$LANGFUSE_ENV_FILE" ] \
  && [ -n "$existing_postgres_volumes" ] \
  && [ "$RESET_EXISTING" = false ]; then
  echo "Error: existing Langfuse data was detected, but $LANGFUSE_ENV_FILE is missing." >&2
  echo "Refusing to generate new credentials because they would not match the existing data." >&2
  echo "" >&2
  echo "To preserve the deployment, restore an .env with its current credentials and" >&2
  echo "complete a service-specific credential and encryption-key migration." >&2
  echo "To discard all local Langfuse data, rerun this script with --reset." >&2
  exit 1
fi

temporary_env_file=""
cleanup_temporary_env() {
  if [ -n "$temporary_env_file" ] && [ -e "$temporary_env_file" ]; then
    rm -f -- "$temporary_env_file"
  fi
}
trap cleanup_temporary_env EXIT

if [ "$RESET_EXISTING" = true ]; then
  temporary_env_file="$(mktemp "$LANGFUSE_DIR/.env.tmp.XXXXXX")"
  generate_env_file "$temporary_env_file"
  echo "⚠️  Reset requested: deleting existing local Langfuse containers and data volumes."
  if [ -e "$LANGFUSE_ENV_FILE" ]; then
    "${COMPOSE_CMD[@]}" down --volumes --remove-orphans
  else
    "${COMPOSE_CMD[@]}" --env-file "$temporary_env_file" down --volumes --remove-orphans
  fi

  remaining_postgres_volumes="$(
    docker volume ls \
      --quiet \
      --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
      --filter "label=com.docker.compose.volume=langfuse_postgres_data"
  )"
  if [ -n "$remaining_postgres_volumes" ]; then
    echo "Error: the existing Langfuse Postgres volume was not removed; reset aborted." >&2
    exit 1
  fi

  mv -f -- "$temporary_env_file" "$LANGFUSE_ENV_FILE"
  temporary_env_file=""
  echo "Generated new Langfuse credentials in $LANGFUSE_ENV_FILE"
elif [ ! -e "$LANGFUSE_ENV_FILE" ]; then
  temporary_env_file="$(mktemp "$LANGFUSE_DIR/.env.tmp.XXXXXX")"
  generate_env_file "$temporary_env_file"
  mv -f -- "$temporary_env_file" "$LANGFUSE_ENV_FILE"
  temporary_env_file=""
  echo "Generated unique Langfuse credentials in $LANGFUSE_ENV_FILE"
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
