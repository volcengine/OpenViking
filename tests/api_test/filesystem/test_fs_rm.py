import json
import time
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

            # mkdir schedules async directory-abstract generation that briefly
            # holds the new directory's path lock; an rm landing inside that
            # window returns a retryable 409 path_busy, so retry those instead
            # of flaking.
            response = api_client.fs_rm(test_dir, recursive=True)
            data = response.json()
            for attempt in range(5):
                if response.status_code == 200 and data.get("status") == "ok":
                    break
                error = data.get("error") or {}
                if response.status_code != 409 or not (
                    (error.get("details") or {}).get("retryable")
                ):
                    break
                print(f"path busy, retrying rm (attempt {attempt + 1}/5)")
                time.sleep(0.5 * (attempt + 1))
                response = api_client.fs_rm(test_dir, recursive=True)
                data = response.json()

            print(f"\nFS rm API status code: {response.status_code}")

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
