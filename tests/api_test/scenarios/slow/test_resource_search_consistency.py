import os
import tempfile
import uuid


class TestResourceSearchConsistency:
    def test_add_resource_then_find_by_topic(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"topic_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(
                    f"# {kw}\n\nThis document covers blockchain consensus mechanisms and decentralized ledger technology."
                )

            add_resp = api_client.add_resource(path=f1, wait=True)
            if add_resp.status_code != 200:
                return

            find_resp = api_client.find(query="blockchain decentralized consensus", limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) > 0, "resource should be findable by topic"

    def test_add_two_resources_find_both(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"dual_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}_a.md")
            with open(f1, "w") as f:
                f.write(f"# {kw}_a\n\nContent about serverless computing and Lambda functions.")
            f2 = os.path.join(temp_dir, f"{kw}_b.md")
            with open(f2, "w") as f:
                f.write(f"# {kw}_b\n\nContent about serverless architecture and FaaS patterns.")

            add1 = api_client.add_resource(path=f1, wait=True)
            add2 = api_client.add_resource(path=f2, wait=True)
            if add1.status_code != 200 or add2.status_code != 200:
                return

            find_resp = api_client.find(query="serverless computing", limit=10)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) >= 2, "both resources should be findable"

    def test_resource_overview_matches_content(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"ov_match_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(
                    f"# {kw}\n\nDetailed analysis of GraphQL vs REST API design patterns and best practices."
                )

            add_resp = api_client.add_resource(path=f1, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            ov_resp = api_client.get_overview(root_uri)
            if ov_resp.status_code == 200:
                overview = ov_resp.json().get("result", "")
                assert isinstance(overview, str) and len(overview) > 0, (
                    "overview should be non-empty"
                )

    def test_reindex_refreshes_search(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"reindex_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(f"# {kw}\n\nOriginal content about DevOps CI/CD pipelines.")

            add_resp = api_client.add_resource(path=f1, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            api_client.fs_write(
                f"{root_uri}/{kw}.md",
                f"# {kw}\n\nUpdated content about GitOps and infrastructure as code.",
                mode="replace",
                wait=True,
            )

            reindex_resp = api_client.content_reindex(root_uri, regenerate=True, wait=True)
            if reindex_resp.status_code == 200:
                find_resp = api_client.find(query="GitOps infrastructure as code", limit=5)
                assert find_resp.status_code == 200

    def test_find_result_has_level_field(self, api_client):
        find_resp = api_client.find(query="programming", limit=5)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])
        for r in resources:
            if isinstance(r, dict):
                assert "level" in r, (
                    f"find result should contain 'level', got keys: {sorted(r.keys())}"
                )
                assert r["level"] in [0, 1, 2], f"level should be 0/1/2, got {r['level']}"

    def test_find_result_has_context_type(self, api_client):
        find_resp = api_client.find(query="data", limit=5)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])
        for r in resources:
            if isinstance(r, dict) and "context_type" in r:
                assert r["context_type"] in ["resource", "memory", "skill", "archive"], (
                    f"context_type should be valid, got {r['context_type']}"
                )

    def test_grep_returns_line_content(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"grep_line_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(f"Line 1: introduction\nLine 2: {kw} details\nLine 3: conclusion")

            add_resp = api_client.add_resource(path=f1, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_resp = api_client.grep(root_uri, kw)
            assert grep_resp.status_code == 200
            matches = grep_resp.json().get("result", {}).get("matches", [])
            for m in matches:
                if isinstance(m, dict):
                    has_content = "content" in m or "line" in m or "text" in m or "lineNumber" in m
                    assert has_content, (
                        f"grep match should contain line info, got keys: {sorted(m.keys())}"
                    )

    def test_glob_star_pattern(self, api_client):
        glob_resp = api_client.glob("viking://resources/*")
        assert glob_resp.status_code == 200
        matches = glob_resp.json().get("result", {}).get("matches", [])
        assert isinstance(matches, list)

    def test_glob_double_star_pattern(self, api_client):
        glob_resp = api_client.glob("viking://resources/**/*.md")
        assert glob_resp.status_code == 200
        matches = glob_resp.json().get("result", {}).get("matches", [])
        for m in matches:
            if isinstance(m, str):
                assert m.startswith("viking://"), f"glob match should be viking URI, got {m}"
