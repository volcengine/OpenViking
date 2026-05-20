import threading
import time
import uuid


class TestConcurrencyRace:
    def test_sequential_add_messages_same_session(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            num_messages = 5
            for i in range(num_messages):
                resp = api_client.add_message(session_id, "user", f"Sequential message {i}")
                assert resp.status_code == 200, (
                    f"add_message {i} should return 200, got {resp.status_code}"
                )

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            msg_count = get_resp.json().get("result", {}).get("message_count", 0)
            assert msg_count == num_messages, (
                f"session should have {num_messages} messages after sequential adds, got {msg_count}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_concurrent_mkdir_same_uri_idempotent(self, api_client):
        dir_uri = f"viking://resources/race_mkdir_{uuid.uuid4().hex[:8]}"
        try:
            results = []

            def do_mkdir():
                try:
                    resp = api_client.fs_mkdir(dir_uri)
                    results.append(resp.status_code)
                except Exception:
                    pass

            threads = [threading.Thread(target=do_mkdir) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200, (
                f"directory should exist after concurrent mkdir, got {stat_resp.status_code}"
            )
            assert stat_resp.json().get("result", {}).get("isDir") is True
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_concurrent_read_same_file_no_corruption(self, api_client):
        file_uri = f"viking://resources/race_read_{uuid.uuid4().hex[:8]}.md"
        original = "Stable content for concurrent read test"
        try:
            write_resp = api_client.fs_write(file_uri, original, mode="create", wait=True)
            if write_resp.status_code != 200:
                return

            results = []
            errors = []

            def read_file():
                try:
                    resp = api_client.fs_read(file_uri)
                    if resp.status_code == 200:
                        results.append(resp.json().get("result", ""))
                    else:
                        errors.append(f"status={resp.status_code}")
                except Exception as e:
                    errors.append(str(e))

            threads = [threading.Thread(target=read_file) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert len(errors) == 0, f"errors during concurrent read: {errors}"
            for content in results:
                assert content == original, (
                    f"concurrent reads should all return same content, got different: {content[:100]}"
                )
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass

    def test_commit_while_adding_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Pre-commit message for race test")
            api_client.add_message(session_id, "assistant", "Pre-commit response")

            commit_result = [None]
            add_result = [None]

            def do_commit():
                try:
                    resp = api_client.session_commit(session_id)
                    commit_result[0] = resp.status_code
                except Exception:
                    pass

            def do_add():
                try:
                    resp = api_client.add_message(
                        session_id, "user", "Concurrent add during commit"
                    )
                    add_result[0] = resp.status_code
                except Exception:
                    pass

            t1 = threading.Thread(target=do_commit)
            t2 = threading.Thread(target=do_add)
            t1.start()
            t2.start()
            t1.join(timeout=60)
            t2.join(timeout=30)

            assert commit_result[0] is not None, "commit should have completed"
            assert add_result[0] is not None, "add_message should have completed"

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_concurrent_find_queries_no_interference(self, api_client):
        results = []
        errors = []

        def do_find(query):
            try:
                resp = api_client.find(query=query, limit=5)
                if resp.status_code == 200:
                    results.append((query, resp.json().get("result", {}).get("total", -1)))
                else:
                    errors.append(f"query={query} status={resp.status_code}")
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=do_find, args=("machine learning",)),
            threading.Thread(target=do_find, args=("database",)),
            threading.Thread(target=do_find, args=("programming",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"errors during concurrent find: {errors}"
        assert len(results) == 3, f"all 3 find queries should return results, got {len(results)}"

    def test_delete_session_while_reading_context(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Message before delete race")

            context_result = [None]
            delete_result = [None]

            def do_read_context():
                try:
                    resp = api_client.get_session_context(session_id, token_budget=128000)
                    context_result[0] = resp.status_code
                except Exception:
                    pass

            def do_delete():
                try:
                    time.sleep(0.1)
                    resp = api_client.delete_session(session_id)
                    delete_result[0] = resp.status_code
                except Exception:
                    pass

            t1 = threading.Thread(target=do_read_context)
            t2 = threading.Thread(target=do_delete)
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)

            assert context_result[0] is not None, "context read should have completed"
            assert delete_result[0] is not None, "delete should have completed"
        finally:
            if session_id:
                try:
                    api_client.delete_session(session_id)
                except Exception:
                    pass

    def test_concurrent_rm_and_write_same_uri(self, api_client):
        file_uri = f"viking://resources/race_rm_write_{uuid.uuid4().hex[:8]}.md"
        try:
            api_client.fs_write(file_uri, "Original content", mode="create", wait=True)

            rm_result = [None]
            write_result = [None]

            def do_rm():
                try:
                    resp = api_client.fs_rm(file_uri)
                    rm_result[0] = resp.status_code
                except Exception:
                    pass

            def do_write():
                try:
                    time.sleep(0.05)
                    resp = api_client.fs_write(
                        file_uri, "New content after rm", mode="create", wait=True
                    )
                    write_result[0] = resp.status_code
                except Exception:
                    pass

            t1 = threading.Thread(target=do_rm)
            t2 = threading.Thread(target=do_write)
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)

            stat_resp = api_client.fs_stat(file_uri)
            if stat_resp.status_code == 200:
                read_resp = api_client.fs_read(file_uri)
                if read_resp.status_code == 200:
                    content = read_resp.json().get("result", "")
                    assert len(content) > 0, "file should have content if it exists"
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass
