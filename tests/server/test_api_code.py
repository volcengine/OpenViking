# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for /api/v1/code/* endpoints."""

from openviking_cli.exceptions import PermissionDeniedError

PY_SAMPLE = '''"""Module top doc."""


class Greeter:
    def greet(self, who: str) -> str:
        return f"Hello {who}"


def make_greeter() -> Greeter:
    return Greeter()
'''


# ---------------------------------------------------------------------------
# /api/v1/code/outline
# ---------------------------------------------------------------------------


class TestCodeOutlineEndpoint:
    async def test_success(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/outline", json={"uri": "viking://resources/greeter.py"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "class Greeter" in body["result"]
        assert "def make_greeter" in body["result"]

    async def test_invalid_uri(self, client):
        resp = await client.post("/api/v1/code/outline", json={"uri": "/tmp/foo.py"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["result"].startswith("Error:")
        assert "viking://" in body["result"]

    async def test_read_permission_denied_uses_error_response(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            raise PermissionDeniedError("denied")

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post("/api/v1/code/outline", json={"uri": "viking://resources/x.py"})
        assert resp.status_code == 403
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "PERMISSION_DENIED"

    async def test_unsupported_language(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return "# just a markdown heading"

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/outline", json={"uri": "viking://resources/notes.md"}
        )
        assert resp.status_code == 200
        assert resp.json()["result"].startswith("Error: unsupported language")

    async def test_non_text_content(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return b"\x00\x01binary"

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post("/api/v1/code/outline", json={"uri": "viking://resources/x.py"})
        assert resp.status_code == 200
        assert "is not text" in resp.json()["result"]


# ---------------------------------------------------------------------------
# /api/v1/code/search
# ---------------------------------------------------------------------------


class TestCodeSearchEndpoint:
    async def test_success(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [
                {"uri": "viking://r/a.py", "isDir": False},
                {"uri": "viking://r/sub", "isDir": True},
            ]

        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "greet"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "Greeter" in body["result"]
        assert "viking://r/a.py" in body["result"]

    async def test_invalid_uri(self, client):
        resp = await client.post("/api/v1/code/search", json={"uri": "/tmp/dir", "query": "foo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"].startswith("Error:")
        assert "viking://" in body["result"]

    async def test_empty_query(self, client):
        resp = await client.post("/api/v1/code/search", json={"uri": "viking://r", "query": ""})
        assert resp.status_code == 200
        assert resp.json()["result"] == "Error: empty query"

    async def test_no_code_files(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [{"uri": "viking://r/notes.md", "isDir": False}]

        monkeypatch.setattr(service.fs, "ls", fake_ls)

        resp = await client.post("/api/v1/code/search", json={"uri": "viking://r", "query": "foo"})
        assert resp.status_code == 200
        assert "No supported source files" in resp.json()["result"]

    async def test_ls_permission_denied_uses_error_response(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            raise PermissionDeniedError("denied")

        monkeypatch.setattr(service.fs, "ls", fake_ls)

        resp = await client.post("/api/v1/code/search", json={"uri": "viking://r", "query": "foo"})
        assert resp.status_code == 403
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "PERMISSION_DENIED"

    async def test_file_cap_warning(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [{"uri": f"viking://r/f{i}.py", "isDir": False} for i in range(250)]

        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "greet"}
        )
        assert resp.status_code == 200
        assert "200-file cap" in resp.json()["result"]

    async def test_partial_read_failure_skipped(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [
                {"uri": "viking://r/a.py", "isDir": False},
                {"uri": "viking://r/b.py", "isDir": False},
            ]

        async def fake_read(uri, ctx=None, **_):
            if uri.endswith("b.py"):
                raise RuntimeError("denied")
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "greet"}
        )
        assert resp.status_code == 200
        assert "viking://r/a.py" in resp.json()["result"]

    async def test_all_read_failures_reported(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [
                {"uri": "viking://r/a.py", "isDir": False},
                {"uri": "viking://r/b.py", "isDir": False},
            ]

        async def fake_read(uri, ctx=None, **_):
            raise RuntimeError("denied")

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "greet"}
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == (
            "Error: failed to read all 2 source files under viking://r"
        )


# ---------------------------------------------------------------------------
# /api/v1/code/expand
# ---------------------------------------------------------------------------


class TestCodeExpandEndpoint:
    async def test_success(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/expand",
            json={"uri": "viking://r/a.py", "symbol": "make_greeter"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "def make_greeter" in body["result"]
        assert "(make_greeter)" in body["result"]

    async def test_qualified_symbol(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/expand",
            json={"uri": "viking://r/a.py", "symbol": "Greeter.greet"},
        )
        assert resp.status_code == 200
        assert "def greet" in resp.json()["result"]

    async def test_invalid_uri(self, client):
        resp = await client.post("/api/v1/code/expand", json={"uri": "/tmp/x.py", "symbol": "foo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"].startswith("Error:")
        assert "viking://" in body["result"]

    async def test_empty_symbol(self, client):
        resp = await client.post(
            "/api/v1/code/expand", json={"uri": "viking://r/x.py", "symbol": ""}
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == "Error: empty symbol"

    async def test_missing_symbol(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/expand",
            json={"uri": "viking://r/a.py", "symbol": "nonexistent"},
        )
        assert resp.status_code == 200
        assert "not found" in resp.json()["result"]

    async def test_read_permission_denied_uses_error_response(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            raise PermissionDeniedError("denied")

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/expand",
            json={"uri": "viking://r/a.py", "symbol": "Greeter"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "PERMISSION_DENIED"

    async def test_non_text_content(self, client, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return b"\x00binary"

        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/expand",
            json={"uri": "viking://r/a.py", "symbol": "Greeter"},
        )
        assert resp.status_code == 200
        assert "is not text" in resp.json()["result"]
