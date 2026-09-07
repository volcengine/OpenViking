import json
import time
import uuid


class TestFsMv:
    def test_fs_mv(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        src_dir = f"viking://resources/test-mv-src-{random_id}"
        dst_dir = f"viking://resources/test-mv-dst-{random_id}"

        try:
            response = api_client.fs_mkdir(src_dir)
            print(f"\nCreate source directory API status code: {response.status_code}")
            assert response.status_code == 200, (
                f"Failed to create source directory: {response.status_code}"
            )

            # mkdir schedules async directory-abstract generation that briefly
            # holds the new directory's path lock; an mv landing inside that
            # window returns a retryable 409 path_busy, so retry those instead
            # of flaking.
            response = api_client.fs_mv(src_dir, dst_dir)
            data = response.json()
            for attempt in range(5):
                error = data.get("error") or {}
                if response.status_code != 409 or not (
                    (error.get("details") or {}).get("retryable")
                ):
                    break
                print(f"path busy, retrying mv (attempt {attempt + 1}/5)")
                time.sleep(0.5 * (attempt + 1))
                response = api_client.fs_mv(src_dir, dst_dir)
                data = response.json()
            print(f"\nFS mv API status code: {response.status_code}")
            print("\n" + "=" * 80)
            print("FS Mv API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            try:
                api_client.fs_rm(dst_dir, recursive=True)
                api_client.fs_rm(src_dir, recursive=True)
            except Exception:
                pass
