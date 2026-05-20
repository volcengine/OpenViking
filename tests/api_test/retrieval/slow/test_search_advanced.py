import os
import tempfile
import uuid


class TestSearchAdvanced:
    def test_find_with_filter_parameter(self, api_client):
        find_resp = api_client.find(
            query="test",
            limit=5,
            filter={"type": "resource"},
        )
        assert find_resp.status_code == 200, (
            f"find with filter should return 200, got {find_resp.status_code}: {find_resp.text[:200]}"
        )
        if find_resp.status_code == 200:
            data = find_resp.json()
            assert data.get("status") == "ok"

    def test_search_vs_find_difference(self, api_client):
        find_resp = api_client.find(query="programming", limit=5)
        search_resp = api_client.search(query="programming", limit=5)

        assert find_resp.status_code == 200
        assert search_resp.status_code == 200

        find_data = find_resp.json().get("result", {})
        search_data = search_resp.json().get("result", {})

        assert "resources" in find_data, "find should have resources"
        assert "memories" in find_data, "find should have memories"
        assert "skills" in find_data, "find should have skills"
        assert "total" in find_data, "find should have total"

        assert "resources" in search_data, "search should have resources"
        assert "memories" in search_data, "search should have memories"

    def test_find_returns_context_type_field(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"ctx_type_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"Document about {unique_kw} for context_type testing")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return

            find_resp = api_client.find(query=unique_kw, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            if len(resources) > 0:
                for item in resources:
                    assert "context_type" in item, (
                        f"each find result should have context_type, got keys: {sorted(item.keys())}"
                    )
                    assert item["context_type"] in ["resource", "memory", "skill"], (
                        f"context_type should be resource/memory/skill, got {item['context_type']}"
                    )

    def test_grep_with_regex_pattern(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"regex_{uuid.uuid4().hex[:8]}.txt")
            with open(test_file, "w") as f:
                f.write("Email: test@example.com\nPhone: 123-456-7890\nDate: 2026-04-24\n")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json().get("result", {}).get("root_uri", "")

            grep_resp = api_client.grep(uri=root_uri, pattern=r"\d{3}-\d{3}-\d{4}")
            if grep_resp.status_code == 200:
                matches = grep_resp.json().get("result", {}).get("matches", [])
                if len(matches) > 0:
                    assert "123-456-7890" in matches[0].get("content", ""), (
                        f"regex grep should find phone number, got: {matches[0].get('content', '')[:100]}"
                    )

    def test_grep_with_exclude_uri(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"exclude_{uuid.uuid4().hex[:8]}.txt")
            with open(test_file, "w") as f:
                f.write("Content with exclude_pattern_keyword for testing")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json().get("result", {}).get("root_uri", "")

            grep_resp = api_client.grep(
                uri="viking://resources/",
                pattern="exclude_pattern_keyword",
                exclude_uri=f"{root_uri}/**.abstract.md",
            )
            if grep_resp.status_code == 200:
                data = grep_resp.json()
                assert data.get("status") == "ok"

    def test_glob_with_extension_pattern(self, api_client):
        glob_resp = api_client.glob(pattern="*.md", uri="viking://resources/")
        assert glob_resp.status_code == 200, (
            f"glob *.md should return 200, got {glob_resp.status_code}"
        )
        matches = glob_resp.json().get("result", {}).get("matches", [])
        for match in matches:
            uri = match if isinstance(match, str) else match.get("uri", "")
            assert ".md" in uri, f"glob *.md should only match .md files, got {uri}"

    def test_glob_with_double_star_pattern(self, api_client):
        glob_resp = api_client.glob(pattern="**/*.md", uri="viking://resources/")
        assert glob_resp.status_code == 200, (
            f"glob **/*.md should return 200, got {glob_resp.status_code}"
        )
        data = glob_resp.json()
        assert data.get("status") == "ok"

    def test_find_score_range(self, api_client):
        find_resp = api_client.find(query="test programming", limit=10)
        assert find_resp.status_code == 200
        data = find_resp.json()

        all_items = []
        for item_type in ["resources", "memories", "skills"]:
            items = data.get("result", {}).get(item_type, [])
            all_items.extend(items)

        for item in all_items:
            score = item.get("score", -1)
            assert 0 <= score <= 1, (
                f"score should be between 0 and 1, got {score} for item {item.get('uri', 'unknown')}"
            )

    def test_find_with_very_specific_query(self, api_client):
        find_resp = api_client.find(query="xyznonexistent12345unique", limit=5)
        assert find_resp.status_code == 200, (
            f"find should return 200 even for nonexistent query, got {find_resp.status_code}: {find_resp.text[:200]}"
        )
        data = find_resp.json()
        assert "result" in data, "find response should have result field"

    def test_search_with_session_includes_session_context(self, api_client):

        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "I work on distributed systems")
            api_client.add_message(session_id, "assistant", "Distributed systems are complex")

            search_resp = api_client.search(
                query="distributed systems", session_id=session_id, limit=5
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

    def test_find_result_total_consistency(self, api_client):
        find_resp = api_client.find(query="test", limit=50)
        assert find_resp.status_code == 200
        data = find_resp.json()
        result = data.get("result", {})

        total = result.get("total", 0)
        resources = len(result.get("resources", []))
        memories = len(result.get("memories", []))
        skills = len(result.get("skills", []))

        returned_count = resources + memories + skills
        assert total >= returned_count, (
            f"total ({total}) should be >= returned items ({returned_count})"
        )

    def test_grep_result_count_field(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"count_{uuid.uuid4().hex[:8]}.txt")
            with open(test_file, "w") as f:
                f.write("count_test_line_1\ncount_test_line_2\ncount_test_line_3\nother line\n")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json().get("result", {}).get("root_uri", "")

            grep_resp = api_client.grep(uri=root_uri, pattern="count_test")
            if grep_resp.status_code == 200:
                result = grep_resp.json().get("result", {})
                assert "count" in result or "match_count" in result, (
                    f"grep result should contain count or match_count, got keys: {sorted(result.keys())}"
                )
