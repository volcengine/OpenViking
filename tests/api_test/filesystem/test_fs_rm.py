import json
import uuid


class TestFsRm:
    def test_fs_rm(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        test_dir = f"viking://resources/test-rm-{random_id}"

        try:
            response = api_client.fs_mkdir(test_dir)
            print(f"\nCreate test directory API status code: {response.status_code}")
            assert response.status_code == 200, (
                f"Failed to create test directory: {response.status_code}"
            )

            # Directory creation enqueues semantic refresh work. Let the
            # request-scoped background processing release the tree lock before
            # deleting the directory, otherwise rm can race with that work.
            settled = api_client.system_wait()
            assert settled.status_code == 200, settled.text

            response = api_client.fs_rm(test_dir, recursive=True)
            print(f"\nFS rm API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("FS Rm API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
