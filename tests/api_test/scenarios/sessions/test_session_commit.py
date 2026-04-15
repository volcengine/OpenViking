import uuid


class TestSessionCommit:
    """TC-S01 对话持久化与 Commit"""

    def test_session_persistence_and_commit(self, api_client):
        """对话持久化与 Commit：创建会话 -> 添加消息 -> 提交 -> 验证"""
        random_id = str(uuid.uuid4())[:8]
        test_messages = [
            f"First test message for session commit {random_id}",
            f"Second test message for session commit {random_id}",
            f"Third test message for session commit {random_id}",
        ]

        # 1. 创建会话
        response = api_client.create_session()
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        assert "result" in data

        create_result = data["result"]
        assert "session_id" in create_result
        session_id = create_result["session_id"]
        assert session_id is not None
        assert len(session_id) > 0

        # 2. 添加多条消息
        added_message_count = 0
        for _i, test_message in enumerate(test_messages):
            response = api_client.add_message(session_id, "user", test_message)
            assert response.status_code == 200
            msg_data = response.json()
            assert msg_data.get("status") == "ok"
            added_message_count += 1

        # 3. 验证添加消息后 message_count 正确
        response = api_client.get_session(session_id)
        assert response.status_code == 200
        get_data = response.json()
        assert get_data.get("status") == "ok"
        get_result = get_data["result"]

        assert get_result["message_count"] == added_message_count, (
            f"Message count should be {added_message_count} after adding messages, got {get_result['message_count']}"
        )

        # 4. 标记会话已使用
        response = api_client.session_used(session_id)
        assert response.status_code == 200

        # 5. 提交会话
        response = api_client.session_commit(session_id)
        assert response.status_code == 200
        commit_data = response.json()
        assert commit_data.get("status") == "ok"

        # 验证 commit 返回结果
        commit_result = commit_data["result"]
        assert "archived" in commit_result, "Commit result should contain 'archived' field"
        assert commit_result["archived"], "Messages should be archived after commit"

        # 6. 等待异步任务完成
        task_id = commit_result.get("task_id")
        if task_id:
            task_result = api_client.wait_for_task(task_id, timeout=60.0)
            task_status = task_result.get("status")
            assert task_status == "completed", (
                f"Task should complete successfully, got status: {task_status}"
            )

        # 7. 获取会话信息验证持久化
        response = api_client.get_session(session_id)
        assert response.status_code == 200
        get_data = response.json()
        assert get_data.get("status") == "ok"
        assert "result" in get_data

        get_result = get_data["result"]

        # 8. 验证会话基本信息
        assert "session_id" in get_result
        assert get_result["session_id"] == session_id, "Session ID should match"

        assert "user" in get_result
        assert "account_id" in get_result["user"]
        assert "user_id" in get_result["user"]

        # 9. 验证 commit_count（任务完成后应该 >= 1）
        if "commit_count" in get_result:
            assert get_result["commit_count"] >= 1, (
                f"Commit count should be at least 1 after task completed, got {get_result['commit_count']}."
            )

        # 10. 验证 last_commit_at（任务完成后应该有值）
        if "last_commit_at" in get_result:
            assert get_result["last_commit_at"] != "", (
                "last_commit_at should have value after commit, got empty string."
            )

        # 11. 使用 get_session_context 验证消息是否正确归档
        response = api_client.get_session_context(session_id)
        assert response.status_code == 200
        context_data = response.json()
        assert context_data.get("status") == "ok"

        context_result = context_data["result"]

        # 消息归档后，messages 可能为空，消息内容在 latest_archive_overview 中
        # 注意：pre_archive_abstracts 已不再返回（仅保留 latest_archive_overview）
        assert "latest_archive_overview" in context_result, (
            "Session context should contain 'latest_archive_overview'"
        )

        # 验证归档摘要不为空（说明消息已被处理）
        archive_overview = context_result["latest_archive_overview"]

        # 归档概览不为空即说明归档成功
        assert archive_overview != "", (
            "Archive overview should not be empty after commit with messages"
        )

        # 验证归档内容包含测试消息的关键词
        archive_content = archive_overview.lower()
        found_in_archive = "test message" in archive_content or "session commit" in archive_content
        assert found_in_archive, (
            f"Archive overview should contain test message content. Got: {archive_overview[:200]}"
        )

        # 12. 验证会话状态（如果存在）
        if "status" in get_result:
            assert get_result["status"] in ["active", "committed", "used"], (
                f"Session status should be active/committed/used, got {get_result['status']}"
            )

        # 14. 验证memories_extracted（如果存在）
        if "memories_extracted" in get_result:
            memories = get_result["memories_extracted"]
            assert isinstance(memories, dict), "memories_extracted should be a dict"

        # 15. 业务逻辑验证：验证会话可以被再次使用
        response = api_client.add_message(session_id, "user", "Additional test message")
        assert response.status_code == 200, "Session should still be usable after commit"

        # 16. 业务逻辑验证：验证会话可以被再次提交
        response = api_client.session_commit(session_id)
        assert response.status_code == 200, "Session should be commitable multiple times"

        print("✓ Session persistence and commit test passed")
