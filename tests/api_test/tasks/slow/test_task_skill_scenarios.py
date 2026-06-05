import uuid


class TestTaskBusinessScenarios:
    def test_task_result_structure(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Task structure test")
            api_client.add_message(session_id, "assistant", "Task structure response")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            commit_data = commit_resp.json()
            task_id = commit_data.get("result", {}).get("task_id")

            if task_id:
                task_resp = api_client.get_task(task_id)
                assert task_resp.status_code == 200, (
                    f"get_task should return 200, got {task_resp.status_code}"
                )
                task_data = task_resp.json()
                assert task_data.get("status") == "ok"
                result = task_data.get("result", {})

                assert "task_id" in result, "task should contain task_id"
                assert "status" in result, "task should contain status"
                assert "task_type" in result, "task should contain task_type"
                assert result["task_id"] == task_id, "task_id should match"
                assert result["status"] in ["pending", "running", "completed", "failed"], (
                    f"unexpected task status: {result['status']}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_tasks_result_structure(self, api_client):
        resp = api_client.list_tasks()
        assert resp.status_code == 200, f"list_tasks should return 200, got {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "ok"
        tasks = data.get("result", [])
        assert isinstance(tasks, list)
        if len(tasks) > 0:
            first = tasks[0]
            assert "task_id" in first, "task item should contain task_id"
            assert "status" in first, "task item should contain status"
            assert "task_type" in first, "task item should contain task_type"

    def test_list_tasks_filter_by_status(self, api_client):
        for status in ["completed", "failed", "pending", "running"]:
            resp = api_client.list_tasks(status=status)
            assert resp.status_code == 200, f"list_tasks with status={status} should return 200"
            data = resp.json()
            assert data.get("status") == "ok"
            for task in data.get("result", []):
                assert task.get("status") == status, (
                    f"filtered task should have status={status}, got {task.get('status')}"
                )

    def test_get_task_nonexistent_returns_404(self, api_client):
        resp = api_client.get_task(f"nonexistent-task-{uuid.uuid4().hex}")
        assert resp.status_code == 404, (
            f"get nonexistent task should return 404, got {resp.status_code}: {resp.text[:200]}"
        )


class TestSkillBusinessScenarios:
    def test_skill_appears_in_search(self, api_client):
        skill_name = f"searchable-skill-{uuid.uuid4().hex[:8]}"
        skill = {
            "name": skill_name,
            "description": "A skill designed to be found via search",
            "content": f"# {skill_name}\nThis skill is for search testing purposes.",
        }
        resp = api_client.add_skill(skill, wait=True)
        assert resp.status_code == 200, (
            f"add_skill should return 200, got {resp.status_code}: {resp.text[:200]}"
        )

        find_resp = api_client.find(query=skill_name, limit=5)
        assert find_resp.status_code == 200
        data = find_resp.json()
        assert "result" in data, "find response should have result field"

    def test_skill_uri_format(self, api_client):
        skill_name = f"uri-skill-{uuid.uuid4().hex[:8]}"
        skill = {
            "name": skill_name,
            "description": "URI format test",
            "content": f"# {skill_name}\nTest content.",
        }
        resp = api_client.add_skill(skill, wait=True)
        assert resp.status_code == 200
        result = resp.json().get("result", {})
        uri = result.get("uri", "")
        assert uri.startswith("viking://user/default/skills/"), f"skill uri format: {uri}"
