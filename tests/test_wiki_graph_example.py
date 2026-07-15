# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

import httpx

from examples.wiki_graph import graph_preview


def test_default_local_output_is_on_desktop(tmp_path: Path, monkeypatch):
    output = tmp_path / "Desktop" / "ov_graph" / "openviking_graph.html"
    monkeypatch.setattr(graph_preview, "DEFAULT_LOCAL_OUTPUT", output)

    assert graph_preview._local_output(None) == output
    assert output.parent.is_dir()


def test_graph_preview_reuses_build_graph_and_download(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text("{}")
    output = tmp_path / "graph.html"
    requests = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"url": None}

        async def initialize(self):
            return None

        async def close(self):
            return None

        async def _request(self, method, path, **kwargs):
            requests.append((method, path, kwargs))
            request = httpx.Request(method, f"http://openviking.test{path}")
            if path.endswith("/build_graph"):
                assert kwargs["json"] == {"space_uris": graph_preview.DEFAULT_SPACES}
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "status": "ok",
                        "result": {"html": "<html>graph</html>"},
                    },
                )
            raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(graph_preview, "AsyncHTTPClient", Client)

    result = graph_preview.main(
        ["--config", str(config_path), "--output", str(output), "--no-open"]
    )

    assert result == 0
    assert output.read_bytes() == b"<html>graph</html>"
    assert requests == [
        (
            "POST",
            "/api/v1/relations/build_graph",
            {"json": {"space_uris": graph_preview.DEFAULT_SPACES}},
        )
    ]
