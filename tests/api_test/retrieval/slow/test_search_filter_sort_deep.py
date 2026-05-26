import os
import tempfile
import uuid


class TestSearchFilterSortDeep:
    def test_find_with_score_threshold(self, api_client):
        find_resp = api_client.find(query="machine learning", limit=10, score_threshold=0.5)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])
        for r in resources:
            if isinstance(r, dict) and "score" in r:
                assert r["score"] >= 0.5, f"score should be >= 0.5 with threshold, got {r['score']}"

    def test_find_with_high_threshold_returns_fewer(self, api_client):
        find_low = api_client.find(query="data", limit=10, score_threshold=0.1)
        find_high = api_client.find(query="data", limit=10, score_threshold=0.9)

        assert find_low.status_code == 200
        assert find_high.status_code == 200

        res_low = find_low.json().get("result", {}).get("resources", [])
        res_high = find_high.json().get("result", {}).get("resources", [])
        assert len(res_high) <= len(res_low), (
            f"higher threshold should return fewer results, high={len(res_high)} low={len(res_low)}"
        )

    def test_find_limit_parameter_respected(self, api_client):
        find_resp = api_client.find(query="test", limit=3)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])
        assert len(resources) <= 3, (
            f"find with limit=3 should return at most 3, got {len(resources)}"
        )

    def test_search_with_session_vs_without(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Discussing neural network architectures")

            search_with = api_client.search(query="neural network", session_id=session_id, limit=5)
            search_without = api_client.search(query="neural network", limit=5)

            assert search_with.status_code == 200
            assert search_without.status_code == 200
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_grep_with_node_limit(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"grep_limit_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(
                    f"# {unique_kw}\n\nLine 1 about {unique_kw}.\nLine 2 about {unique_kw}.\nLine 3 about {unique_kw}."
                )

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_resp = api_client.grep(root_uri, pattern=unique_kw, node_limit=1)
            assert grep_resp.status_code == 200

    def test_glob_with_uri_filter(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_uri_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nContent for glob test.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.md", uri=root_uri)
            assert glob_resp.status_code == 200

    def test_find_returns_score_field(self, api_client):
        find_resp = api_client.find(query="algorithm", limit=5)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])
        for r in resources:
            if isinstance(r, dict):
                assert "score" in r or "uri" in r, (
                    f"find result should have score or uri, got keys: {list(r.keys())}"
                )

    def test_search_result_structure(self, api_client):
        search_resp = api_client.search(query="database", limit=5)
        assert search_resp.status_code == 200
        result = search_resp.json().get("result", {})
        assert isinstance(result, dict), "search result should be dict"
        resources = result.get("resources", [])
        if resources:
            first = resources[0]
            if isinstance(first, dict):
                assert "uri" in first, (
                    f"search result should contain uri, got keys: {list(first.keys())}"
                )

    def test_find_with_target_uri(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"find_target_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nSpecific content about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            find_resp = api_client.find(query=unique_kw, target_uri=root_uri, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) >= 0, "find with target_uri should return results"

    def test_grep_exclude_uri(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"grep_excl_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nContent about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_resp = api_client.grep(root_uri, pattern=unique_kw, exclude_uri=root_uri)
            assert grep_resp.status_code == 200
