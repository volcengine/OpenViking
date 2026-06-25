# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for /api/v1/code/* endpoints."""


from openviking.parse.parsers.code.ast.code_tools import (
    CODE_LOCATE_FILE_CAP,
    CODE_SCAN_LS_LEVEL_LIMIT,
    CODE_SCAN_LS_NODE_LIMIT,
    CODE_SEARCH_FILE_CAP,
)
from openviking.server.routers.code import _select_local_code_files
from openviking_cli.exceptions import PermissionDeniedError

PY_SAMPLE = '''"""Module top doc."""


class Greeter:
    def greet(self, who: str) -> str:
        return f"Hello {who}"


def make_greeter() -> Greeter:
    return Greeter()
'''


def test_local_locate_selection_keeps_weak_path_diagnostic_candidate_under_locate_cap(tmp_path):
    for index in range(260):
        path = tmp_path / "sphinx" / "builders" / "latex" / f"generated_{index}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("class Generated: pass\n")
    std_path = tmp_path / "sphinx" / "domains" / "std.py"
    std_path.parent.mkdir(parents=True, exist_ok=True)
    std_path.write_text('logger.warning("no number is assigned for %s: %s")\n')

    paths, capped, _skipped = _select_local_code_files(
        tmp_path,
        'Sphinx warning "no number is assigned for table" during latex build',
    )

    assert len(paths) < CODE_LOCATE_FILE_CAP
    assert capped is False
    assert std_path in paths


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

        resp = await client.post(
            "/api/v1/code/outline", json={"uri": "viking://resources/x.py"}
        )
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

        resp = await client.post(
            "/api/v1/code/outline", json={"uri": "viking://resources/x.py"}
        )
        assert resp.status_code == 200
        assert "is not text" in resp.json()["result"]


# ---------------------------------------------------------------------------
# /api/v1/code/search
# ---------------------------------------------------------------------------


class TestCodeSearchEndpoint:
    async def test_success(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **kwargs):
            ls_call = {
                "recursive": recursive,
                "output": output,
                "node_limit": kwargs.get("node_limit"),
                "level_limit": kwargs.get("level_limit"),
            }
            fake_ls.call = ls_call
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
        assert fake_ls.call == {
            "recursive": True,
            "output": "original",
            "node_limit": CODE_SCAN_LS_NODE_LIMIT,
            "level_limit": CODE_SCAN_LS_LEVEL_LIMIT,
        }

    async def test_invalid_uri(self, client):
        resp = await client.post(
            "/api/v1/code/search", json={"uri": "/tmp/dir", "query": "foo"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"].startswith("Error:")
        assert "viking://" in body["result"]

    async def test_empty_query(self, client):
        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": ""}
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == "Error: empty query"

    async def test_no_code_files(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [{"uri": "viking://r/notes.md", "isDir": False}]

        monkeypatch.setattr(service.fs, "ls", fake_ls)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "foo"}
        )
        assert resp.status_code == 200
        assert "No supported source files" in resp.json()["result"]

    async def test_ls_permission_denied_uses_error_response(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            raise PermissionDeniedError("denied")

        monkeypatch.setattr(service.fs, "ls", fake_ls)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "foo"}
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "PERMISSION_DENIED"

    async def test_file_cap_warning(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [
                {"uri": f"viking://r/f{i}.py", "isDir": False}
                for i in range(CODE_SEARCH_FILE_CAP + 1)
            ]

        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "greet"}
        )
        assert resp.status_code == 200
        assert "1000-file cap" in resp.json()["result"]

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

    async def test_hybrid_search_returns_content_matches(self, client, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [{"uri": "viking://r/a.py", "isDir": False}]

        async def fake_read(uri, ctx=None, **_):
            return """\
def ordinary_function():
    return None

# W0511: TODO marker in a comment, not a Python symbol
"""

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/search", json={"uri": "viking://r", "query": "W0511"}
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert "1 code matches for \"W0511\"" in result
        assert "viking://r/a.py" in result
        assert "content:" in result
        assert "L4: # W0511: TODO marker in a comment, not a Python symbol" in result

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
# /api/v1/code/locate
# ---------------------------------------------------------------------------


class TestCodeLocateEndpoint:
    async def test_local_source_requires_explicit_server_switch(self, client, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "greeter.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

        resp = await client.post(
            "/api/v1/code/locate",
            json={
                "source": {"type": "local", "path": str(repo)},
                "query": "changed greet behavior",
                "output_format": "json",
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["warnings"][0]["code"] == "local_source_disabled"

    async def test_viking_json_separates_edit_and_behavior_candidates(
        self, client, service, monkeypatch
    ):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **kwargs):
            fake_ls.call = {
                "recursive": recursive,
                "output": output,
                "node_limit": kwargs.get("node_limit"),
                "level_limit": kwargs.get("level_limit"),
            }
            return [
                {"uri": "viking://r/pylint/checkers/misc.py", "isDir": False},
                {"uri": "viking://r/tests/checkers/unittest_misc.py", "isDir": False},
            ]

        async def fake_read(uri, ctx=None, **_):
            if uri.endswith("unittest_misc.py"):
                return """\
class TestFixme:
    def test_fixme_with_message(self):
        code = "# FIXME message"
"""
            return """\
class EncodingChecker:
    def open(self):
        notes = "|".join(re.escape(note) for note in self.config.notes)
        self._fixme_pattern = re.compile(notes, re.I)
"""

        monkeypatch.setattr(service.fs, "ls", fake_ls)
        monkeypatch.setattr(service.fs, "read", fake_read)

        resp = await client.post(
            "/api/v1/code/locate",
            json={
                "source": {"type": "viking", "uri": "viking://r"},
                "query": "Fix W0511 fixme notes handling in the misc checker",
                "failing_tests": ["test_fixme_with_message"],
                "output_format": "json",
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["schema_version"] == "code-locate/v1"
        assert result["edit_candidates"][0]["location"] == {
            "type": "viking",
            "uri": "viking://r/pylint/checkers/misc.py",
            "relative_path": "pylint/checkers/misc.py",
        }
        assert "path" not in result["edit_candidates"][0]["location"]
        assert result["behavior_references"][0]["location"]["type"] == "viking"
        assert result["verification"][-1]["command"] is None
        assert fake_ls.call == {
            "recursive": True,
            "output": "original",
            "node_limit": CODE_SCAN_LS_NODE_LIMIT,
            "level_limit": CODE_SCAN_LS_LEVEL_LIMIT,
        }

    async def test_local_json_reads_current_checkout_and_returns_local_paths(self, client, tmp_path):
        client._transport.app.state.config.allow_local_code_source_paths = True
        repo = tmp_path / "repo"
        repo.mkdir()
        package = repo / "pylint" / "checkers"
        tests = repo / "tests" / "checkers"
        package.mkdir(parents=True)
        tests.mkdir(parents=True)
        impl = package / "misc.py"
        test_file = tests / "unittest_misc.py"
        impl.write_text(
            "import re\n\nclass EncodingChecker:\n    def open(self):\n        return 'W0511 fixme notes'\n",
            encoding="utf-8",
        )
        test_file.write_text(
            "class TestFixme:\n    def test_fixme_with_message(self):\n        assert True\n",
            encoding="utf-8",
        )

        resp = await client.post(
            "/api/v1/code/locate",
            json={
                "source": {"type": "local", "path": str(repo)},
                "query": "Fix W0511 fixme notes handling in the misc checker",
                "failing_tests": ["test_fixme_with_message"],
                "output_format": "json",
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        location = result["edit_candidates"][0]["location"]
        assert location["type"] == "local"
        assert location["path"] == str(impl)
        assert location["relative_path"] == "pylint/checkers/misc.py"
        assert "uri" not in location
        assert result["verification"][0]["cwd"] == str(repo)
        assert result["verification"][0]["command"].startswith("python3 -m py_compile ")

    async def test_old_issue_shape_is_not_accepted(self, client):
        resp = await client.post(
            "/api/v1/code/locate",
            json={"uri": "viking://r", "issue": "old shape"},
        )

        assert resp.status_code == 400


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
        resp = await client.post(
            "/api/v1/code/expand", json={"uri": "/tmp/x.py", "symbol": "foo"}
        )
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
