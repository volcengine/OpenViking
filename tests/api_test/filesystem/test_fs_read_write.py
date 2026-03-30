import json


class TestFsReadWrite:
    def test_fs_read(self, api_client):
        try:
            response = api_client.fs_ls("viking://")
            print(f"\nList root directory API status code: {response.status_code}")
            assert response.status_code == 200, (
                f"Failed to list root directory: {response.status_code}"
            )

            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            result = data.get("result", [])
            assert len(result) > 0, "No files found in root"

            test_file_path = result[0].get("uri")
            assert test_file_path is not None, "No suitable file found"

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
