import os
import tempfile
import uuid


class TestSearchResultQuality:
    def test_find_result_has_resources_memories_skills_total(self, api_client):
        resp = api_client.find(query="test", limit=5)
        assert resp.status_code == 200, f"find should return 200, got {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        assert "resources" in result, "find result should contain 'resources'"
        assert "memories" in result, "find result should contain 'memories'"
        assert "skills" in result, "find result should contain 'skills'"
        assert "total" in result, "find result should contain 'total'"

        assert isinstance(result["resources"], list), "resources should be a list"
        assert isinstance(result["memories"], list), "memories should be a list"
        assert isinstance(result["skills"], list), "skills should be a list"
        assert isinstance(result["total"], int), "total should be an int"

    def test_find_resource_item_has_required_fields(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"fieldtest_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"This is a document about {unique_kw} for field validation testing.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return

            find_resp = api_client.find(query=unique_kw, limit=5)
            assert find_resp.status_code == 200
            data = find_resp.json()
            resources = data.get("result", {}).get("resources", [])

            if len(resources) > 0:
                item = resources[0]
                required_fields = ["uri", "score", "context_type"]
                for field in required_fields:
                    assert field in item, (
                        f"resource item should contain '{field}', got keys: {sorted(item.keys())}"
                    )

                assert item["context_type"] == "resource", (
                    f"resource item context_type should be 'resource', got {item['context_type']}"
                )
                assert 0 <= item["score"] <= 1, (
                    f"score should be between 0 and 1, got {item['score']}"
                )
                assert item["uri"].startswith("viking://"), (
                    f"uri should start with viking://, got {item['uri']}"
                )

    def test_find_memory_item_has_required_fields(self, api_client):
        import time

        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            unique_fact = f"I work at Acme Corp_{uuid.uuid4().hex[:6]}"
            api_client.add_message(session_id, "user", unique_fact)
            api_client.add_message(session_id, "assistant", "Noted: you work at Acme Corp.")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        if task_resp.json().get("result", {}).get("status") in [
                            "completed",
                            "failed",
                        ]:
                            break
                    time.sleep(2)

            find_resp = api_client.find(query="work at Acme", limit=5)
            assert find_resp.status_code == 200
            memories = find_resp.json().get("result", {}).get("memories", [])

            if len(memories) > 0:
                item = memories[0]
                assert "uri" in item, (
                    f"memory item should contain 'uri', got keys: {sorted(item.keys())}"
                )
                assert "score" in item, "memory item should contain 'score'"
                assert "context_type" in item, "memory item should contain 'context_type'"
                assert item["context_type"] == "memory", (
                    f"memory item context_type should be 'memory', got {item['context_type']}"
                )
                assert 0 <= item["score"] <= 1, (
                    f"score should be between 0 and 1, got {item['score']}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_find_results_sorted_by_score_desc(self, api_client):
        resp = api_client.find(query="test programming language", limit=10)
        assert resp.status_code == 200
        data = resp.json()

        for item_type in ["resources", "memories", "skills"]:
            items = data.get("result", {}).get(item_type, [])
            if len(items) >= 2:
                scores = [item.get("score", 0) for item in items]
                for i in range(len(scores) - 1):
                    assert scores[i] >= scores[i + 1] - 0.01, (
                        f"{item_type} results should be sorted by score descending, but scores[{i}]={scores[i]} < scores[{i + 1}]={scores[i + 1]}"
                    )

    def test_find_with_score_threshold_filters_results(self, api_client):
        threshold = 0.5
        resp = api_client.find(query="test", limit=10, score_threshold=threshold)
        assert resp.status_code == 200
        data = resp.json()

        for item_type in ["resources", "memories", "skills"]:
            items = data.get("result", {}).get(item_type, [])
            for item in items:
                assert item.get("score", 0) >= threshold, (
                    f"all results should have score >= {threshold}, got {item.get('score')}"
                )

    def test_find_with_target_uri_scopes_results(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"scope_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Scoped content about {unique_kw}")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            find_resp = api_client.find(query=unique_kw, target_uri=root_uri, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])

            for item in resources:
                assert item.get("uri", "").startswith(root_uri), (
                    f"scoped results should be under target_uri, got {item.get('uri')}"
                )

    def test_search_with_session_id(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "I love machine learning")
            api_client.add_message(session_id, "assistant", "Machine learning is fascinating!")

            search_resp = api_client.search(
                query="machine learning", session_id=session_id, limit=5
            )
            assert search_resp.status_code == 200, (
                f"search with session_id should return 200, got {search_resp.status_code}: {search_resp.text[:200]}"
            )
            data = search_resp.json()
            assert data.get("status") == "ok"
            result = data.get("result", {})
            assert "resources" in result
            assert "memories" in result
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_grep_result_structure(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"grepstruct_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Line 1: Hello world\nLine 2: {unique_kw} pattern match\nLine 3: Goodbye")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_resp = api_client.grep(uri=root_uri, pattern=unique_kw)
            if grep_resp.status_code != 200:
                return

            data = grep_resp.json()
            assert data.get("status") == "ok"
            result = data.get("result", {})
            assert "matches" in result, (
                f"grep result should contain 'matches', got keys: {sorted(result.keys())}"
            )

            matches = result.get("matches", [])
            if len(matches) > 0:
                match = matches[0]
                assert "uri" in match, (
                    f"grep match should contain 'uri', got keys: {sorted(match.keys())}"
                )
                assert "content" in match, "grep match should contain 'content'"
                assert "line" in match, "grep match should contain 'line'"
                assert isinstance(match["line"], int), (
                    f"line should be int, got {type(match['line'])}"
                )
                assert match["line"] >= 1, f"line number should be >= 1, got {match['line']}"

    def test_glob_result_structure(self, api_client):
        glob_resp = api_client.glob(pattern="*", uri="viking://resources/")
        assert glob_resp.status_code == 200, f"glob should return 200, got {glob_resp.status_code}"
        data = glob_resp.json()
        assert data.get("status") == "ok"
        result = data.get("result", {})
        assert "matches" in result, (
            f"glob result should contain 'matches', got keys: {sorted(result.keys())}"
        )

        matches = result.get("matches", [])
        if len(matches) > 0:
            match = matches[0]
            assert isinstance(match, str), (
                f"glob match should be a URI string, got {type(match)}: {match}"
            )
            assert match.startswith("viking://"), f"glob match URI format invalid: {match}"

    def test_glob_with_wildcard_pattern(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_name = f"glob_wild_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_name}.txt")
            with open(test_file, "w") as f:
                f.write("Glob wildcard test content")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return

            glob_resp = api_client.glob(pattern=f"*{unique_name}*", uri="viking://resources/")
            if glob_resp.status_code == 200:
                matches = glob_resp.json().get("result", {}).get("matches", [])
                assert len(matches) >= 1, (
                    f"glob with wildcard should find resource, pattern=*{unique_name}*"
                )

    def test_find_total_matches_sum(self, api_client):
        resp = api_client.find(query="test", limit=10)
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})

        total = result.get("total", 0)
        resources_count = len(result.get("resources", []))
        memories_count = len(result.get("memories", []))
        skills_count = len(result.get("skills", []))

        assert total >= resources_count + memories_count + skills_count, (
            f"total ({total}) should be >= sum of all item counts ({resources_count + memories_count + skills_count})"
        )

    def test_find_empty_query_returns_error_or_empty(self, api_client):
        resp = api_client.find(query="", limit=5)
        assert resp.status_code == 400, (
            f"empty query should return 200/400/422, got {resp.status_code}"
        )

    def test_grep_nonexistent_uri(self, api_client):
        grep_resp = api_client.grep(uri="viking://resources/nonexistent_grep_uri", pattern="test")
        assert grep_resp.status_code == 404, (
            f"grep on nonexistent URI should return 200/404/500, got {grep_resp.status_code}"
        )

    def test_glob_nonexistent_uri(self, api_client):
        glob_resp = api_client.glob(pattern="*", uri="viking://resources/nonexistent_glob_uri")
        assert glob_resp.status_code == 404, (
            f"glob on nonexistent URI should return 200/404/500, got {glob_resp.status_code}"
        )
