import json
import uuid

import pytest
import requests


class TestFsMkdir:
    def test_fs_mkdir(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        test_dir_path = f"viking://user/test-dir-{random_id}/"

        try:
            response = api_client.fs_mkdir(test_dir_path)
            print(f"\nFS mkdir API status code: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print("\n" + "=" * 80)
                print("FS mkdir API Response:")
                print("=" * 80)
                print(json.dumps(data, indent=2, ensure_ascii=False))
                print("=" * 80 + "\n")

                assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
                assert data.get("error") is None, (
                    f"Expected error to be null, got {data.get('error')}"
                )
                assert "result" in data, "'result' field should exist"

            response = api_client.fs_rm(test_dir_path, recursive=True)

        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")
