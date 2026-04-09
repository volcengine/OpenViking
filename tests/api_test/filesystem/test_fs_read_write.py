import json


class TestFsReadWrite:
    def test_fs_read(self, api_client):
        session_id = None

        try:
            response = api_client.create_session()
            assert response.status_code == 200, "Create session failed"
            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            session_id = data["result"]["session_id"]

            response = api_client.add_message(session_id, "user", "Hello, file read test!")
            assert response.status_code == 200, "Add message failed"
            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            test_file_path = f"viking://session/default/{session_id}/messages.jsonl"

            response = api_client.fs_read(test_file_path)
            print(f"\nFS read API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("FS read API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            if session_id:
                api_client.delete_session(session_id)
