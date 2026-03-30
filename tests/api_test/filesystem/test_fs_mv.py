import json
import uuid


class TestFsMv:
    def test_fs_mv(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        src_dir = f"/tmp/test-mv-src-{random_id}"
        dst_dir = f"/tmp/test-mv-dst-{random_id}"

        try:
            response = api_client.fs_mkdir(src_dir)
            print(f"\nCreate source directory API status code: {response.status_code}")
            assert response.status_code == 200, (
                f"Failed to create source directory: {response.status_code}"
            )

            response = api_client.fs_mv(src_dir, dst_dir)
            print(f"\nFS mv API status code: {response.status_code}")

            data = response.json()
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
