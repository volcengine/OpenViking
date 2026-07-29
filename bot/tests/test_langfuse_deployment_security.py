from pathlib import Path

from vikingbot.config.schema import LangfuseConfig

BOT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = BOT_ROOT / "deploy/docker/langfuse/docker-compose.yml"
DEPLOY_SCRIPT = BOT_ROOT / "deploy/docker/deploy_langfuse.sh"


def test_langfuse_client_keys_have_no_repository_known_defaults():
    config = LangfuseConfig()

    assert config.public_key == ""
    assert config.secret_key == ""


def test_compose_requires_generated_secrets_and_binds_public_ports_to_loopback():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")

    for known_secret in [
        "miniosecret",
        "vikingbot-admin-password-2026",
        "sk-lf-vikingbot-secret-key-2026",
        "vikingbot-nextauth-secret-2026",
        "vikingbot-salt-2026",
        "0000000000000000000000000000000000000000000000000000000000000000",
    ]:
        assert known_secret not in compose

    for required_secret in [
        "MINIO_ROOT_PASSWORD",
        "NEXTAUTH_SECRET",
        "SALT",
        "ENCRYPTION_KEY",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY",
        "LANGFUSE_INIT_USER_PASSWORD",
    ]:
        assert f"${{{required_secret}:?" in compose

    assert "127.0.0.1:${LANGFUSE_PORT:-3000}:3000" in compose
    assert "127.0.0.1:${MINIO_PORT:-9090}:9000" in compose


def test_launcher_generates_private_unique_credentials():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "umask 077" in script
    assert "openssl rand -hex" in script
    assert 'chmod 600 "$LANGFUSE_ENV_FILE"' in script
    assert 'if [ ! -e "$LANGFUSE_ENV_FILE" ]; then' in script
