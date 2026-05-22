import os
import tempfile
import uuid

import pytest


class TestResponseDataTypes:
    def test_fs_stat_response_types(self, api_client):
        dir_uri = f"viking://resources/type_check_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})

            assert isinstance(result.get("name"), str), (
                f"name should be str, got {type(result.get('name'))}"
            )
            assert isinstance(result.get("isDir"), bool), (
                f"isDir should be bool, got {type(result.get('isDir'))}"
            )
            assert isinstance(result.get("size"), int), (
                f"size should be int, got {type(result.get('size'))}"
            )
            assert isinstance(result.get("modTime"), str), (
                f"modTime should be str, got {type(result.get('modTime'))}"
            )
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_fs_ls_response_types(self, api_client):
        dir_uri = f"viking://resources/ls_type_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            ls_resp = api_client.fs_ls(dir_uri)
            assert ls_resp.status_code == 200
            result = ls_resp.json().get("result", {})
            assert isinstance(result, list), f"ls result should be list, got {type(result)}"

            if len(result) > 0:
                item = result[0]
                assert isinstance(item.get("uri"), str), "ls item uri should be str"
                assert isinstance(item.get("isDir"), bool), "ls item isDir should be bool"
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_find_response_types(self, api_client):
        find_resp = api_client.find(query="test", limit=5)
        if find_resp.status_code == 401:
            pytest.skip(
                "Find API returned 401: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert find_resp.status_code == 200
        data = find_resp.json()
        assert isinstance(data.get("status"), str), "status should be str"
        result = data.get("result", {})

        assert isinstance(result.get("resources"), list), "resources should be list"
        assert isinstance(result.get("memories"), list), "memories should be list"
        assert isinstance(result.get("skills"), list), "skills should be list"
        assert isinstance(result.get("total"), int), (
            f"total should be int, got {type(result.get('total'))}"
        )

        for item in result.get("resources", []):
            assert isinstance(item.get("uri"), str), (
                f"resource uri should be str, got {type(item.get('uri'))}"
            )
            assert isinstance(item.get("score"), (int, float)), (
                f"resource score should be numeric, got {type(item.get('score'))}"
            )
            assert isinstance(item.get("context_type"), str), "resource context_type should be str"

    def test_add_resource_response_types(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"type_check_{uuid.uuid4().hex[:8]}.txt")
            with open(test_file, "w") as f:
                f.write("Type check content")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            data = add_resp.json()
            assert isinstance(data.get("status"), str), "status should be str"
            result = data.get("result", {})
            assert isinstance(result.get("root_uri"), str), (
                f"root_uri should be str, got {type(result.get('root_uri'))}"
            )
            assert isinstance(result.get("status"), str), (
                f"result status should be str, got {type(result.get('status'))}"
            )

    def test_task_response_types(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Task type check")
            api_client.add_message(session_id, "assistant", "Task type check response")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            task_id = commit_resp.json().get("result", {}).get("task_id")
            if not task_id:
                return

            task_resp = api_client.get_task(task_id)
            assert task_resp.status_code == 200
            task_data = task_resp.json().get("result", {})

            assert isinstance(task_data.get("task_id"), str), "task_id should be str"
            assert isinstance(task_data.get("task_type"), str), "task_type should be str"
            assert isinstance(task_data.get("status"), str), "status should be str"
            assert isinstance(task_data.get("created_at"), (int, float)), (
                "created_at should be numeric"
            )
            assert isinstance(task_data.get("updated_at"), (int, float)), (
                "updated_at should be numeric"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_error_response_has_consistent_structure(self, api_client):
        resp = api_client.fs_stat("viking://resources/nonexistent_type_check_xyz")
        if resp.status_code >= 400:
            data = resp.json()
            if "error" in data:
                error = data["error"]
                assert isinstance(error.get("code"), str), (
                    f"error code should be str, got {type(error.get('code'))}"
                )
                assert isinstance(error.get("message"), str), (
                    f"error message should be str, got {type(error.get('message'))}"
                )
                assert len(error["code"]) > 0, "error code should not be empty"
                assert len(error["message"]) > 0, "error message should not be empty"
