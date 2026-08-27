import time
import uuid

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
PUBLIC_TASK_FIELDS = {
    "task_id",
    "task_type",
    "status",
    "created_at",
    "updated_at",
    "created_at_iso",
    "updated_at_iso",
    "resource_id",
    "meta",
    "stage",
    "result",
    "error",
}


def test_session_commit_task_lifecycle(api_client):
    session_id = f"task-lifecycle-{uuid.uuid4().hex[:12]}"
    observed_statuses = []

    try:
        response = api_client.create_session(session_id=session_id)
        assert response.status_code == 200, response.text

        for role, content in (
            ("user", "Remember that the task lifecycle verification value is 7391."),
            ("assistant", "Recorded the verification value 7391."),
        ):
            response = api_client.add_message(session_id, role, content)
            assert response.status_code == 200, response.text

        response = api_client.session_commit(session_id)
        assert response.status_code == 200, response.text
        commit = response.json()["result"]
        assert commit["status"] == "accepted"
        task_id = commit["task_id"]
        assert task_id

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = api_client.get_task(task_id)
            assert response.status_code == 200, response.text
            task = response.json()["result"]
            if not observed_statuses or task["status"] != observed_statuses[-1]:
                observed_statuses.append(task["status"])
            if task["status"] in TERMINAL_STATUSES:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(f"task did not reach a terminal state: {observed_statuses}")

        assert task["status"] == "completed", task
        assert task["task_type"] == "session_commit"
        assert task["resource_id"] == session_id
        assert set(task) == PUBLIC_TASK_FIELDS
        assert "version" not in task
        assert "works" not in task

        response = api_client.list_tasks(
            task_type="session_commit",
            status="completed",
            resource_id=session_id,
            limit=10,
        )
        assert response.status_code == 200, response.text
        assert any(item["task_id"] == task_id for item in response.json()["result"])
    finally:
        api_client.delete_session(session_id)


def test_content_write_task_tracks_queue_work(api_client):
    suffix = uuid.uuid4().hex[:12]
    uri = f"viking://resources/task-lifecycle-{suffix}.md"

    try:
        response = api_client.fs_write(
            uri,
            "# Task lifecycle\n\nVerify durable Semantic and Embedding queue work.",
            mode="create",
            wait=False,
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["result"]["task_id"]

        task = api_client.wait_for_task(task_id, timeout=180, poll_interval=0.1)
        assert task["status"] == "completed", task
        assert task["task_type"] == "content_write"
        assert task["resource_id"] == uri

        queue_status = task["result"]["queue_status"]
        assert set(queue_status) == {"Semantic", "Embedding"}
        for status in queue_status.values():
            assert set(status) == {
                "processed",
                "requeue_count",
                "error_count",
                "errors",
            }
            assert status["error_count"] == 0, status

        response = api_client.fs_read(uri)
        assert response.status_code == 200, response.text
        assert "Verify durable Semantic and Embedding queue work." in response.json()["result"]
    finally:
        api_client.fs_rm(uri)
