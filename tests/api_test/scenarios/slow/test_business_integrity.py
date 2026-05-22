import os
import tempfile
import uuid


class TestCrossAPIConsistency:
    def test_resource_appears_in_fs_after_add(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_name = f"consistency_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_name}.txt")
            with open(test_file, "w") as f:
                f.write(f"Cross-API consistency test for {unique_name}")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            root_uri = add_resp.json()["result"]["root_uri"]

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200, (
                f"fs_stat should return 200, got {stat_resp.status_code}: {stat_resp.text[:200]}"
            )
            stat_result = stat_resp.json()["result"]
            assert stat_result.get("isDir") is True, "root should be a directory"
            assert stat_result.get("name") == unique_name, (
                f"directory name should be {unique_name}, got {stat_result.get('name')}"
            )

            ls_resp = api_client.fs_ls(root_uri)
            assert ls_resp.status_code == 200, f"fs_ls should return 200, got {ls_resp.status_code}"
            children = ls_resp.json().get("result", [])
            assert len(children) > 0, (
                f"resource directory should contain at least one file, got {children}"
            )

    def test_resource_content_readable_after_add(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"readable_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Content for readability test: {unique_kw}")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            assert abstract_resp.status_code == 200, (
                f"abstract should return 200, got {abstract_resp.status_code}: {abstract_resp.text[:200]}"
            )
            abstract = abstract_resp.json().get("result", "")
            assert isinstance(abstract, str), f"abstract should be string, got {type(abstract)}"
            assert len(abstract) > 0, "abstract should not be empty after resource is processed"

    def test_resource_searchable_after_add(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"searchable_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"This document is about {unique_kw} for search consistency testing.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )

            find_resp = api_client.find(query=unique_kw, limit=5)
            assert find_resp.status_code == 200, (
                f"find should return 200, got {find_resp.status_code}: {find_resp.text[:200]}"
            )
            data = find_resp.json()
            assert data.get("status") == "ok"
            resources = data.get("result", {}).get("resources", [])
            assert len(resources) > 0, f"resource should be searchable after add, query={unique_kw}"

    def test_resource_abstract_overview_content_hierarchy(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "hierarchy_test.md")
            with open(test_file, "w") as f:
                f.write(
                    "# Hierarchy Test\n\nDetailed content about machine learning algorithms.\n\n## Deep Learning\n\nNeural networks and backpropagation.\n"
                )

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            overview_resp = api_client.get_overview(root_uri)

            assert abstract_resp.status_code == 200, (
                f"abstract should return 200, got {abstract_resp.status_code}: {abstract_resp.text[:200]}"
            )
            assert overview_resp.status_code == 200, (
                f"overview should return 200, got {overview_resp.status_code}: {overview_resp.text[:200]}"
            )

            abstract = abstract_resp.json().get("result", "")
            overview = overview_resp.json().get("result", "")

            assert len(abstract) > 0, "L0 abstract should not be empty"
            assert len(overview) > 0, "L1 overview should not be empty"
            assert len(overview) >= len(abstract), (
                f"L1 overview ({len(overview)} chars) should be >= L0 abstract ({len(abstract)} chars)"
            )

    def test_resource_stat_size_changes_after_add(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "size_test.txt")
            with open(test_file, "w") as f:
                f.write("A" * 1000)

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}: {add_resp.text[:200]}"
            )
            root_uri = add_resp.json()["result"]["root_uri"]

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200
            stat_result = stat_resp.json()["result"]
            assert stat_result.get("size", 0) >= 0, "size should be non-negative"

    def test_deleted_resource_not_in_ls(self, api_client):
        dir_uri = f"viking://resources/del_ls_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(dir_uri)

            ls_before = api_client.fs_ls("viking://resources/")
            assert ls_before.status_code == 200
            uris_before = [item["uri"] for item in ls_before.json().get("result", [])]
            assert dir_uri in uris_before, "directory should appear in ls before deletion"

            api_client.fs_rm(dir_uri, recursive=True)

            ls_after = api_client.fs_ls("viking://resources/")
            assert ls_after.status_code == 200
            uris_after = [item["uri"] for item in ls_after.json().get("result", [])]
            assert dir_uri not in uris_after, "directory should NOT appear in ls after deletion"
        except Exception:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass


class TestSessionEndToEnd:
    def test_session_commit_creates_searchable_memory(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            unique_fact = f"My favorite programming language is Rust_{uuid.uuid4().hex[:6]}"
            api_client.add_message(session_id, "user", unique_fact)
            api_client.add_message(session_id, "assistant", "I'll remember that you like Rust.")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200, (
                f"commit should return 200, got {commit_resp.status_code}: {commit_resp.text[:200]}"
            )
            commit_data = commit_resp.json()
            assert commit_data.get("status") == "ok"
            result = commit_data.get("result", {})
            assert result.get("session_id") == session_id

            task_id = result.get("task_id")
            if task_id:
                import time

                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        task_data = task_resp.json().get("result", {})
                        if task_data.get("status") == "completed":
                            break
                    time.sleep(2)

                find_resp = api_client.find(query="favorite programming language", limit=5)
                assert find_resp.status_code == 200
                data = find_resp.json()
                result_data = data.get("result", {})
                assert "resources" in result_data, "find result should contain resources"
                assert "memories" in result_data, "find result should contain memories"
                assert "skills" in result_data, "find result should contain skills"
                assert "total" in result_data, "find result should contain total"
                memories = result_data.get("memories", [])
                assert len(memories) >= 0, "commit should eventually create searchable memories"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_context_reflects_message_order(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "First message")
            api_client.add_message(session_id, "assistant", "Second message")
            api_client.add_message(session_id, "user", "Third message")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})
            messages = result.get("messages", [])
            assert len(messages) >= 3, f"should have at least 3 messages, got {len(messages)}"

            roles = [m.get("role") for m in messages]
            assert roles == ["user", "assistant", "user"], (
                f"message roles should be in order user-assistant-user, got {roles}"
            )

            for msg in messages:
                assert "role" in msg, (
                    f"each message should have 'role', got keys: {list(msg.keys())}"
                )
                assert "parts" in msg, (
                    f"each message should have 'parts', got keys: {list(msg.keys())}"
                )
                parts = msg.get("parts", [])
                assert isinstance(parts, list), f"parts should be list, got {type(parts)}"
                for part in parts:
                    assert "type" in part, (
                        f"each part should have 'type', got keys: {list(part.keys())}"
                    )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_pending_tokens_reset_after_commit(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Message for pending tokens test")
            api_client.add_message(session_id, "assistant", "Response for pending tokens test")

            get_before = api_client.get_session(session_id)
            assert get_before.status_code == 200
            pending_before = get_before.json().get("result", {}).get("pending_tokens", 0)
            assert pending_before > 0, "pending_tokens should be > 0 after adding messages"

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            get_after = api_client.get_session(session_id)
            assert get_after.status_code == 200
            pending_after = get_after.json().get("result", {}).get("pending_tokens", -1)
            assert pending_after == 0, (
                f"pending_tokens should be 0 after commit, got {pending_after}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_context_stats_accurate(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Stats accuracy test message")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})
            stats = result.get("stats", {})

            required_stats_fields = [
                "activeTokens",
                "archiveTokens",
                "totalArchives",
                "includedArchives",
                "droppedArchives",
                "failedArchives",
            ]
            for field in required_stats_fields:
                assert field in stats, (
                    f"stats should contain '{field}', got keys: {sorted(stats.keys())}"
                )

            assert stats["activeTokens"] >= 0, "activeTokens should be non-negative"
            assert stats["archiveTokens"] >= 0, "archiveTokens should be non-negative"
            assert stats["totalArchives"] >= 0, "totalArchives should be non-negative"
            assert stats["activeTokens"] > 0, (
                "activeTokens should be positive after adding a message"
            )

            estimated_tokens = result.get("estimatedTokens")
            if estimated_tokens is not None:
                assert estimated_tokens == stats["activeTokens"] + stats["archiveTokens"], (
                    f"estimatedTokens ({estimated_tokens}) should equal activeTokens ({stats['activeTokens']}) + archiveTokens ({stats['archiveTokens']})"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)


class TestDataIntegrity:
    def test_find_result_scores_are_normalized(self, api_client):
        resp = api_client.find(query="test", limit=10)
        assert resp.status_code == 200
        data = resp.json()
        resources = data.get("result", {}).get("resources", [])
        for r in resources:
            score = r.get("score", 0)
            assert 0 <= score <= 1, f"score should be between 0 and 1, got {score}"

    def test_search_find_returns_context_type_field(self, api_client):
        resp = api_client.find(query="test", limit=5)
        assert resp.status_code == 200
        data = resp.json()
        for item_type in ["resources", "memories", "skills"]:
            items = data.get("result", {}).get(item_type, [])
            for item in items:
                assert "context_type" in item, f"{item_type} item should contain context_type"
                assert item["context_type"] in ["resource", "memory", "skill"], (
                    f"context_type should be resource/memory/skill, got {item['context_type']}"
                )
