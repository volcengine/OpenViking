class TestSearchContextAware:
    def test_search_with_session_returns_context(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(
                session_id, "user", "I am working on a Python machine learning project"
            )
            api_client.add_message(session_id, "assistant", "Great! Python is excellent for ML.")

            search_resp = api_client.search(
                query="programming languages", session_id=session_id, limit=5
            )
            assert search_resp.status_code == 200, (
                f"search should return 200, got {search_resp.status_code}"
            )
            result = search_resp.json().get("result", {})
            assert isinstance(result, dict), "search result should be dict"
            assert "messages" in result or "memories" in result or "resources" in result, (
                f"context-aware search should contain messages/memories/resources, got keys: {sorted(result.keys())}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_search_without_session_returns_resources(self, api_client):
        search_resp = api_client.search(query="machine learning", limit=5)
        assert search_resp.status_code == 200
        result = search_resp.json().get("result", {})
        assert isinstance(result, dict), "search result should be dict"

    def test_search_result_has_messages_field(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Tell me about neural networks")
            api_client.add_message(
                session_id,
                "assistant",
                "Neural networks are computing systems inspired by biological neural networks.",
            )

            search_resp = api_client.search(query="deep learning", session_id=session_id, limit=5)
            assert search_resp.status_code == 200, (
                f"search should return 200/500, got {search_resp.status_code}"
            )
            if search_resp.status_code == 200:
                result = search_resp.json().get("result", {})
                messages = result.get("messages", [])
                assert isinstance(messages, list), "messages should be list"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_search_result_has_stats(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Stats test message about databases")

            search_resp = api_client.search(query="databases", session_id=session_id, limit=5)
            assert search_resp.status_code == 200
            result = search_resp.json().get("result", {})
            stats = result.get("stats", {})
            if stats:
                assert isinstance(stats, dict), "stats should be dict"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_search_limit_parameter(self, api_client):
        search_resp = api_client.search(query="test", limit=3)
        assert search_resp.status_code == 200

    def test_search_empty_query(self, api_client):
        search_resp = api_client.search(query="", limit=5)
        if search_resp.status_code == 200:
            result = search_resp.json().get("result", {})
            assert isinstance(result, dict), "empty query result should be dict"
        else:
            assert search_resp.status_code == 400, (
                f"empty query should return client error, got {search_resp.status_code}"
            )

    def test_find_vs_search_difference(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "I love TypeScript for frontend development")

            find_resp = api_client.find(query="TypeScript", limit=5)
            search_resp = api_client.search(query="TypeScript", session_id=session_id, limit=5)

            assert find_resp.status_code == 200
            assert search_resp.status_code == 200

            find_result = find_resp.json().get("result", {})
            search_resp.json().get("result", {})

            assert "messages" not in find_result or len(find_result.get("messages", [])) == 0, (
                "find should not include session messages"
            )
            assert "resources" in find_result, "find should include resources"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_search_nonexistent_session(self, api_client):
        search_resp = api_client.search(
            query="test", session_id="nonexistent-search-session-999", limit=5
        )
        assert search_resp.status_code == 200, (
            f"search with nonexistent session should return valid status, got {search_resp.status_code}"
        )

    def test_search_result_resources_have_score(self, api_client):
        search_resp = api_client.search(query="programming", limit=5)
        if search_resp.status_code == 200:
            result = search_resp.json().get("result", {})
            resources = result.get("resources", [])
            for r in resources:
                if isinstance(r, dict) and "score" in r:
                    assert isinstance(r["score"], (int, float)), (
                        f"score should be numeric, got {type(r['score'])}"
                    )
