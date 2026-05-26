import uuid


class TestBoundaryConditions:
    def test_uri_with_special_characters(self, api_client):
        uri = f"viking://resources/special-chars_{uuid.uuid4().hex[:8]}"
        mkdir_resp = api_client.fs_mkdir(uri)
        assert mkdir_resp.status_code == 200, (
            f"mkdir with hyphenated URI should return valid status, got {mkdir_resp.status_code}"
        )
        if mkdir_resp.status_code == 200:
            stat_resp = api_client.fs_stat(uri)
            assert stat_resp.status_code == 200

    def test_uri_with_very_long_name(self, api_client):
        long_name = "a" * 200
        uri = f"viking://resources/{long_name}"
        mkdir_resp = api_client.fs_mkdir(uri)
        assert mkdir_resp.status_code == 200, (
            f"mkdir with very long URI should return valid status, got {mkdir_resp.status_code}"
        )

    def test_uri_with_unicode_characters(self, api_client):
        uri = f"viking://resources/中文目录_{uuid.uuid4().hex[:8]}"
        mkdir_resp = api_client.fs_mkdir(uri)
        assert mkdir_resp.status_code == 200, (
            f"mkdir with unicode URI should return valid status, got {mkdir_resp.status_code}: {mkdir_resp.text[:200]}"
        )

    def test_uri_with_spaces(self, api_client):
        uri = f"viking://resources/space name_{uuid.uuid4().hex[:8]}"
        mkdir_resp = api_client.fs_mkdir(uri)
        assert mkdir_resp.status_code == 200, (
            f"mkdir with space in URI should return valid status, got {mkdir_resp.status_code}"
        )

    def test_find_with_very_long_query(self, api_client):
        long_query = "test " * 1000
        find_resp = api_client.find(query=long_query, limit=1)
        assert find_resp.status_code == 200, (
            f"find with very long query should return valid status, got {find_resp.status_code}"
        )

    def test_find_with_special_regex_chars(self, api_client):
        find_resp = api_client.find(query="test.*+?[](){}|^$", limit=1)
        assert find_resp.status_code == 200, (
            f"find with regex chars should return valid status, got {find_resp.status_code}"
        )

    def test_grep_with_special_regex_chars(self, api_client):
        grep_resp = api_client.grep(uri="viking://resources/", pattern=r"\d+\.\d+")
        assert grep_resp.status_code == 200, (
            f"grep with regex should return valid status, got {grep_resp.status_code}"
        )

    def test_session_id_with_special_characters(self, api_client):
        special_id = f"test-session.special_{uuid.uuid4().hex[:6]}"
        create_resp = api_client.create_session(session_id=special_id)
        assert create_resp.status_code == 200, (
            f"session with special chars in ID should return valid status, got {create_resp.status_code}"
        )

    def test_add_message_with_empty_content(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            msg_resp = api_client.add_message(session_id, "user", "")
            assert msg_resp.status_code == 200, (
                f"empty content message should return 200/400/422, got {msg_resp.status_code}: {msg_resp.text[:200]}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_add_message_with_whitespace_content(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            msg_resp = api_client.add_message(session_id, "user", "   \n\t   ")
            assert msg_resp.status_code == 200, (
                f"whitespace content should return 200/400/422, got {msg_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_fs_mkdir_deeply_nested(self, api_client):
        base = f"viking://resources/deep_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(base)
            deep_uri = base + "/level1/level2/level3/level4/level5"
            mkdir_resp = api_client.fs_mkdir(deep_uri)
            assert mkdir_resp.status_code == 200, (
                f"deeply nested mkdir should return valid status, got {mkdir_resp.status_code}"
            )
        finally:
            try:
                api_client.fs_rm(base, recursive=True)
            except Exception:
                pass

    def test_find_with_limit_one(self, api_client):
        find_resp = api_client.find(query="test", limit=1)
        assert find_resp.status_code == 200
        data = find_resp.json()
        resources = data.get("result", {}).get("resources", [])
        memories = data.get("result", {}).get("memories", [])
        skills = data.get("result", {}).get("skills", [])
        total_items = len(resources) + len(memories) + len(skills)
        assert total_items <= 3, (
            f"limit=1 should return at most 1 item per category, got {total_items} total"
        )

    def test_find_with_large_limit(self, api_client):
        find_resp = api_client.find(query="test", limit=1000)
        assert find_resp.status_code == 200
        data = find_resp.json()
        assert isinstance(data.get("result", {}).get("resources"), list)

    def test_context_with_zero_budget(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Zero budget test")

            ctx_resp = api_client.get_session_context(session_id, token_budget=0)
            assert ctx_resp.status_code == 200, (
                f"zero budget context should return valid status, got {ctx_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_tasks_with_zero_limit(self, api_client):
        resp = api_client.list_tasks(limit=0)
        assert resp.status_code == 200, (
            f"list_tasks with limit=0 should return valid status, got {resp.status_code}"
        )

    def test_list_tasks_with_large_limit(self, api_client):
        resp = api_client.list_tasks(limit=10000)
        assert resp.status_code == 400, f"large limit should return 400, got {resp.status_code}"

    def test_viking_uri_non_resources_scope(self, api_client):
        uri = f"viking://user/test_user_{uuid.uuid4().hex[:8]}"
        stat_resp = api_client.fs_stat(uri)
        assert stat_resp.status_code in (403, 404), (
            f"non-resources scope URI should return 403 or 404, got {stat_resp.status_code}"
        )

    def test_viking_uri_temp_scope(self, api_client):
        uri = f"viking://temp/test_{uuid.uuid4().hex[:8]}"
        stat_resp = api_client.fs_stat(uri)
        assert stat_resp.status_code == 400, (
            f"temp scope URI should return 400, got {stat_resp.status_code}"
        )
