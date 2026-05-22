import os
import tempfile
import time
import uuid


class TestTasksFilterPaging:
    def test_list_tasks_default(self, api_client):
        resp = api_client.list_tasks()
        assert resp.status_code == 200
        tasks = resp.json().get("result", [])
        assert isinstance(tasks, list), "tasks should be list"

    def test_list_tasks_with_limit(self, api_client):
        resp = api_client.list_tasks(limit=3)
        assert resp.status_code == 200
        tasks = resp.json().get("result", [])
        assert isinstance(tasks, list)
        assert len(tasks) <= 3, f"should return at most 3 tasks, got {len(tasks)}"

    def test_list_tasks_filter_completed(self, api_client):
        resp = api_client.list_tasks(status="completed")
        assert resp.status_code == 200
        tasks = resp.json().get("result", [])
        assert isinstance(tasks, list)

    def test_list_tasks_filter_failed(self, api_client):
        resp = api_client.list_tasks(status="failed")
        assert resp.status_code == 200
        tasks = resp.json().get("result", [])
        assert isinstance(tasks, list)

    def test_list_tasks_filter_by_type_commit(self, api_client):
        resp = api_client.list_tasks(task_type="commit")
        assert resp.status_code == 200
        tasks = resp.json().get("result", [])
        assert isinstance(tasks, list)

    def test_list_tasks_filter_by_type_reindex(self, api_client):
        resp = api_client.list_tasks(task_type="reindex")
        assert resp.status_code == 200
        tasks = resp.json().get("result", [])
        assert isinstance(tasks, list)

    def test_get_task_after_commit(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Task tracking test")
            api_client.add_message(session_id, "assistant", "Acknowledged")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            task_id = commit_resp.json().get("result", {}).get("task_id")

            if task_id:
                task_resp = api_client.get_task(task_id)
                assert task_resp.status_code == 200
                result = task_resp.json().get("result", {})
                assert "status" in result, (
                    f"task result should contain status, got keys: {sorted(result.keys())}"
                )
                assert result["status"] in ["pending", "running", "completed", "failed"], (
                    f"task status should be valid, got {result['status']}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_get_task_after_reindex(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"reindex_task_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(f"# {kw}\n\nContent for reindex task test.")

            add_resp = api_client.add_resource(path=f1, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            reindex_resp = api_client.content_reindex(root_uri, regenerate=False, wait=False)
            if reindex_resp.status_code != 200:
                return

            task_id = reindex_resp.json().get("result", {}).get("task_id")
            if task_id:
                task_resp = api_client.get_task(task_id)
                assert task_resp.status_code == 200

    def test_task_result_has_required_fields(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Task fields test")
            api_client.add_message(session_id, "assistant", "Reply")

            commit_resp = api_client.session_commit(session_id)
            task_id = commit_resp.json().get("result", {}).get("task_id")

            if task_id:
                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        result = task_resp.json().get("result", {})
                        if result.get("status") in ["completed", "failed"]:
                            assert "status" in result
                            if "task_type" in result:
                                assert isinstance(result["task_type"], str)
                            if "created_at" in result:
                                assert isinstance(result["created_at"], (str, int, float))
                            break
                    time.sleep(2)
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_tasks_with_resource_id(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"task_res_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(f"# {kw}\n\nContent for task resource filter test.")

            add_resp = api_client.add_resource(path=f1, wait=False)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            resp = api_client.list_tasks(resource_id=root_uri)
            assert resp.status_code == 200
            tasks = resp.json().get("result", [])
            assert isinstance(tasks, list)
