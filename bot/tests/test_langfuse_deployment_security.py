import os
import shutil
import subprocess
from pathlib import Path

from vikingbot.config.schema import LangfuseConfig

BOT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = BOT_ROOT / "deploy/docker/langfuse/docker-compose.yml"
DEPLOY_SCRIPT = BOT_ROOT / "deploy/docker/deploy_langfuse.sh"


def _stage_launcher(tmp_path: Path) -> tuple[Path, Path, Path]:
    docker_dir = tmp_path / "docker"
    langfuse_dir = docker_dir / "langfuse"
    mock_bin = tmp_path / "bin"
    langfuse_dir.mkdir(parents=True)
    mock_bin.mkdir()

    launcher = docker_dir / "deploy_langfuse.sh"
    shutil.copy2(DEPLOY_SCRIPT, launcher)
    (langfuse_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    docker_log = tmp_path / "docker.log"
    docker_mock = mock_bin / "docker"
    docker_mock.write_text(
        """#!/bin/sh
if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
  exit 0
fi
if [ "$1" = "volume" ] && [ "$2" = "ls" ]; then
  if [ -n "${MOCK_DOCKER_VOLUME:-}" ] && [ ! -e "$MOCK_DOCKER_VOLUME_REMOVED" ]; then
    printf '%s\\n' "$MOCK_DOCKER_VOLUME"
  fi
  exit 0
fi
if [ "$1" = "volume" ] && [ "$2" = "inspect" ]; then
  exit 1
fi
if [ "$1" = "compose" ]; then
  printf '%s\\n' "$*" >> "$MOCK_DOCKER_LOG"
  case " $* " in
    *" down "*)
      : > "$MOCK_DOCKER_VOLUME_REMOVED"
      ;;
  esac
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    docker_mock.chmod(0o755)
    return launcher, mock_bin, docker_log


def _launcher_env(mock_bin: Path, docker_log: Path, *, volume: str = "") -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{mock_bin}{os.pathsep}{env['PATH']}"
    env["MOCK_DOCKER_LOG"] = str(docker_log)
    env["MOCK_DOCKER_VOLUME"] = volume
    env["MOCK_DOCKER_VOLUME_REMOVED"] = str(docker_log.with_suffix(".volume-removed"))
    return env


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
    assert 'chmod 600 "$target_file"' in script
    assert 'mktemp "$LANGFUSE_DIR/.env.tmp.XXXXXX"' in script
    assert 'if [ ! -e "$LANGFUSE_ENV_FILE" ]; then' in script


def test_launcher_refuses_to_replace_secrets_for_an_existing_volume(tmp_path):
    launcher, mock_bin, docker_log = _stage_launcher(tmp_path)

    result = subprocess.run(
        [str(launcher)],
        cwd=launcher.parent,
        env=_launcher_env(
            mock_bin,
            docker_log,
            volume="langfuse_langfuse_postgres_data",
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "existing Langfuse data" in result.stderr
    assert "--reset" in result.stderr
    assert not (launcher.parent / "langfuse/.env").exists()


def test_launcher_reset_replaces_existing_local_data_explicitly(tmp_path):
    launcher, mock_bin, docker_log = _stage_launcher(tmp_path)

    result = subprocess.run(
        [str(launcher), "--reset"],
        cwd=launcher.parent,
        env=_launcher_env(
            mock_bin,
            docker_log,
            volume="langfuse_langfuse_postgres_data",
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    env_file = launcher.parent / "langfuse/.env"
    assert result.returncode == 0, result.stderr
    assert env_file.exists()
    assert env_file.stat().st_mode & 0o777 == 0o600
    docker_calls = docker_log.read_text(encoding="utf-8")
    assert "down --volumes --remove-orphans" in docker_calls
    assert "up -d" in docker_calls


def test_launcher_reset_does_not_depend_on_existing_env_file(tmp_path):
    launcher, mock_bin, docker_log = _stage_launcher(tmp_path)
    env_file = launcher.parent / "langfuse/.env"
    env_file.write_text("INCOMPLETE=old\n", encoding="utf-8")
    env_file.chmod(0o600)

    result = subprocess.run(
        [str(launcher), "--reset"],
        cwd=launcher.parent,
        env=_launcher_env(
            mock_bin,
            docker_log,
            volume="langfuse_langfuse_clickhouse_data",
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "INCOMPLETE=old" not in env_file.read_text(encoding="utf-8")
    docker_calls = docker_log.read_text(encoding="utf-8")
    assert "--env-file" in docker_calls
    assert "down --volumes --remove-orphans" in docker_calls


def test_launcher_generates_credentials_for_a_fresh_install(tmp_path):
    launcher, mock_bin, docker_log = _stage_launcher(tmp_path)

    result = subprocess.run(
        [str(launcher)],
        cwd=launcher.parent,
        env=_launcher_env(mock_bin, docker_log),
        capture_output=True,
        text=True,
        check=False,
    )

    env_file = launcher.parent / "langfuse/.env"
    assert result.returncode == 0, result.stderr
    assert env_file.exists()
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert "POSTGRES_PASSWORD=" in env_file.read_text(encoding="utf-8")
    docker_calls = docker_log.read_text(encoding="utf-8")
    assert "down " not in docker_calls
    assert "up -d" in docker_calls


def test_launcher_preserves_an_existing_env_file(tmp_path):
    launcher, mock_bin, docker_log = _stage_launcher(tmp_path)
    env_file = launcher.parent / "langfuse/.env"
    env_file.write_text("SENTINEL=keep\n", encoding="utf-8")
    env_file.chmod(0o600)

    result = subprocess.run(
        [str(launcher)],
        cwd=launcher.parent,
        env=_launcher_env(mock_bin, docker_log),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert env_file.read_text(encoding="utf-8") == "SENTINEL=keep\n"
    assert "up -d" in docker_log.read_text(encoding="utf-8")
