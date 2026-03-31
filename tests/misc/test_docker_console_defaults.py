# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfile_defaults_to_console_entrypoint_runtime():
    dockerfile = _read_text("Dockerfile")

    assert (
        "COPY docker/openviking-console-entrypoint.sh /usr/local/bin/openviking-console-entrypoint"
        in dockerfile
    )
    assert "EXPOSE 1933 8020" in dockerfile
    assert 'CMD ["openviking-console-entrypoint"]' in dockerfile


def test_console_entrypoint_starts_server_then_console():
    entrypoint = _read_text("docker/openviking-console-entrypoint.sh")

    assert "openviking-server" in entrypoint
    assert 'SERVER_URL="http://127.0.0.1:1933"' in entrypoint
    assert 'SERVER_HEALTH_URL="${SERVER_URL}/health"' in entrypoint
    assert 'CONSOLE_PORT="${OPENVIKING_CONSOLE_PORT:-8020}"' in entrypoint
    assert "python -m openviking.console.bootstrap" in entrypoint
    assert '--port "${CONSOLE_PORT}"' in entrypoint
    assert '--openviking-url "${SERVER_URL}"' in entrypoint


def test_docker_compose_exposes_console_port():
    compose = _read_text("docker-compose.yml")

    assert '- "1933:1933"' in compose
    assert '- "8020:8020"' in compose
