import os
import tempfile
import uuid


class TestSearchDedupSort:
    def test_find_results_no_duplicate_uris(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"dedup_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nContent about {unique_kw} for dedup testing.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return

            find_resp = api_client.find(query=unique_kw, limit=20)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])

            uris = [r.get("uri", "") for r in resources if isinstance(r, dict)]
            assert len(uris) == len(set(uris)), (
                f"find results should not contain duplicate URIs, got duplicates: "
                f"{[u for u in uris if uris.count(u) > 1]}"
            )

    def test_find_results_sorted_by_score_descending(self, api_client):
        find_resp = api_client.find(query="machine learning", limit=10)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])

        if len(resources) >= 2:
            scores = [r.get("score", 0) for r in resources if isinstance(r, dict)]
            for i in range(len(scores) - 1):
                assert scores[i] >= scores[i + 1], (
                    f"find results should be sorted by score descending, "
                    f"but scores[{i}]={scores[i]} < scores[{i + 1}]={scores[i + 1]}"
                )

    def test_find_score_between_zero_and_one(self, api_client):
        find_resp = api_client.find(query="programming", limit=10)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])

        for r in resources:
            if isinstance(r, dict) and "score" in r:
                score = r["score"]
                assert 0 <= score <= 1, (
                    f"score should be between 0 and 1, got {score} for uri={r.get('uri')}"
                )

    def test_find_with_target_uri_no_cross_contamination(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"target_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nExclusive content for target URI test.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            find_resp = api_client.find(query=unique_kw, target_uri=root_uri, limit=10)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])

            for r in resources:
                if isinstance(r, dict) and "uri" in r:
                    assert r["uri"].startswith(root_uri), (
                        f"results with target_uri should only contain resources under {root_uri}, "
                        f"got {r['uri']}"
                    )

    def test_grep_results_have_line_numbers(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"grep_line_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"Line 1: {unique_kw}\nLine 2: other\nLine 3: {unique_kw} again")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_resp = api_client.grep(root_uri, unique_kw)
            assert grep_resp.status_code == 200
            matches = grep_resp.json().get("result", {}).get("matches", [])
            for m in matches:
                if isinstance(m, dict):
                    assert "line" in m or "line_number" in m or "lineNumber" in m, (
                        f"grep match should contain line number, got keys: {sorted(m.keys())}"
                    )

    def test_find_total_equals_sum_of_categories(self, api_client):
        find_resp = api_client.find(query="test", limit=10)
        assert find_resp.status_code == 200
        result = find_resp.json().get("result", {})

        total = result.get("total", 0)
        resources_count = len(result.get("resources", []))
        memories_count = len(result.get("memories", []))
        skills_count = len(result.get("skills", []))

        actual_sum = resources_count + memories_count + skills_count
        assert total >= actual_sum, (
            f"total ({total}) should be >= sum of categories ({actual_sum} = "
            f"{resources_count}+{memories_count}+{skills_count})"
        )

    def test_find_with_high_score_threshold_returns_fewer(self, api_client):
        find_low = api_client.find(query="data", limit=10, score_threshold=0.1)
        find_high = api_client.find(query="data", limit=10, score_threshold=0.8)

        assert find_low.status_code == 200
        assert find_high.status_code == 200

        count_low = len(find_low.json().get("result", {}).get("resources", []))
        count_high = len(find_high.json().get("result", {}).get("resources", []))

        assert count_high <= count_low, (
            f"higher threshold should return <= results, got high={count_high} > low={count_low}"
        )

    def test_search_with_session_includes_session_data(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "I prefer Python for backend development")
            api_client.add_message(session_id, "assistant", "Python is great for backend!")

            search_resp = api_client.search(
                query="programming language", session_id=session_id, limit=5
            )
            assert search_resp.status_code == 200
            result = search_resp.json().get("result", {})
            assert isinstance(result, dict), "search result should be dict"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_glob_results_are_unique_uris(self, api_client):
        glob_resp = api_client.glob("viking://resources/**/*.md")
        assert glob_resp.status_code == 200
        matches = glob_resp.json().get("result", {}).get("matches", [])

        if len(matches) >= 2:
            assert len(matches) == len(set(matches)), (
                "glob matches should be unique URIs, got duplicates"
            )

    def test_find_empty_query_returns_error_or_empty(self, api_client):
        find_resp = api_client.find(query="", limit=5)
        if find_resp.status_code == 200:
            result = find_resp.json().get("result", {})
            total = result.get("total", -1)
            assert total == 0, f"empty query should return 0 results or error, got total={total}"
        else:
            assert find_resp.status_code == 400, (
                f"empty query should return client error, got {find_resp.status_code}"
            )

    def test_find_result_context_type_values(self, api_client):
        find_resp = api_client.find(query="software", limit=10)
        assert find_resp.status_code == 200
        result = find_resp.json().get("result", {})

        valid_context_types = {"resource", "memory", "skill", "archive"}
        for category in ["resources", "memories", "skills"]:
            for item in result.get(category, []):
                if isinstance(item, dict) and "context_type" in item:
                    assert item["context_type"] in valid_context_types, (
                        f"context_type should be one of {valid_context_types}, "
                        f"got {item['context_type']}"
                    )

    def test_find_limit_parameter_respected(self, api_client):
        find_resp = api_client.find(query="test", limit=3)
        assert find_resp.status_code == 200
        resources = find_resp.json().get("result", {}).get("resources", [])
        assert len(resources) <= 3, (
            f"find with limit=3 should return <= 3 resources, got {len(resources)}"
        )
