import asyncio
import json
import os
import shutil
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from vikingbot.channels.whatsapp_auth import (
    authenticate_bridge,
    bridge_auth_proof,
    resolve_bridge_token,
)


class _AuthenticatedSocket:
    def __init__(self, token: str, *, proof_token: str | None = None):
        self.token = token
        self.proof_token = proof_token or token
        self.sent: list[dict] = []
        self.recv_count = 0

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        self.recv_count += 1
        if self.recv_count == 1:
            challenge = self.sent[0]["challenge"]
            return json.dumps(
                {
                    "type": "auth_proof",
                    "proof": bridge_auth_proof(self.proof_token, challenge),
                }
            )
        return json.dumps({"type": "auth_ok"})


def test_generated_bridge_token_is_shared_and_owner_only(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _index: resolve_bridge_token("", tmp_path), range(8)))

    assert len(set(tokens)) == 1
    assert len(tokens[0]) >= 32
    token_path = tmp_path / "whatsapp" / "bridge-token"
    assert token_path.read_text(encoding="utf-8") == tokens[0]
    if os.name != "nt":
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_configured_bridge_token_does_not_create_state(tmp_path):
    assert resolve_bridge_token("configured-token", tmp_path) == "configured-token"
    assert not (tmp_path / "whatsapp").exists()


def test_bridge_identity_is_verified_before_token_disclosure():
    token = "test-token-with-at-least-thirty-two-bytes"
    socket = _AuthenticatedSocket(token)

    asyncio.run(authenticate_bridge(socket, token))

    assert [message["type"] for message in socket.sent] == ["auth_challenge", "auth"]
    assert "token" not in socket.sent[0]
    assert socket.sent[1]["token"] == token


def test_impostor_never_receives_the_bridge_token():
    token = "test-token-with-at-least-thirty-two-bytes"
    socket = _AuthenticatedSocket(token, proof_token="wrong-token-with-thirty-two-bytes")

    try:
        asyncio.run(authenticate_bridge(socket, token))
    except ConnectionError as exc:
        assert "identity proof failed" in str(exc)
    else:
        raise AssertionError("The impostor proof was accepted")

    assert [message["type"] for message in socket.sent] == ["auth_challenge"]
    assert all("token" not in message for message in socket.sent)


def test_bridge_source_change_rebuilds_installed_bridge(tmp_path, monkeypatch):
    from vikingbot.cli import commands

    installed = tmp_path / "bridge"
    (installed / "dist").mkdir(parents=True)
    (installed / "dist" / "index.js").write_text("stale", encoding="utf-8")
    (installed / "src").mkdir()
    (installed / "src" / "server.ts").write_text("stale", encoding="utf-8")
    (installed / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(commands, "get_bridge_path", lambda: installed)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/npm")

    calls = []

    def fake_run(args, *, cwd, **_kwargs):
        calls.append(args)
        if args == ["npm", "run", "build"]:
            (Path(cwd) / "dist").mkdir(exist_ok=True)
            (Path(cwd) / "dist" / "index.js").write_text("built", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert commands._get_bridge_dir() == installed
    assert calls == [["npm", "install"], ["npm", "run", "build"]]
    assert (installed / "src" / "auth.ts").exists()


def test_channels_login_always_supplies_a_bridge_token(tmp_path, monkeypatch):
    from vikingbot.cli import commands
    from vikingbot.config.schema import WhatsAppChannelConfig
    from vikingbot.utils import helpers

    channel = WhatsAppChannelConfig()
    config = SimpleNamespace(
        bot_data_path=tmp_path,
        channels_config=SimpleNamespace(get_all_channels=lambda: [channel]),
    )
    monkeypatch.setattr(helpers, "_bot_data_path", None)
    monkeypatch.setattr(commands, "load_config", lambda: config)
    monkeypatch.setattr(commands, "_get_bridge_dir", lambda: tmp_path / "bridge")

    captured = {}

    def fake_run(args, *, cwd, check, env):
        captured.update(args=args, cwd=cwd, check=check, env=env)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    commands.channels_login()

    assert captured["args"] == ["npm", "start"]
    assert captured["env"]["BRIDGE_TOKEN"]
    assert captured["env"]["BRIDGE_TOKEN"] == (tmp_path / "whatsapp" / "bridge-token").read_text(
        encoding="utf-8"
    )
