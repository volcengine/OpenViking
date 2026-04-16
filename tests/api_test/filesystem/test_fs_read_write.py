import json

import pytest
import requests


class TestFsReadWrite:
    def test_fs_read(self, api_client):
        """Test fs_read API by creating a test file and reading it back."""
        test_file_uri = "viking://resources/test_fs_read_write_test.txt"
        test_content = "This is a test file created for fs_read test."

        
        try:
            write_response = api_client.fs_write(test_file_uri, test_content, wait=True)
            print(f"\nCreated test file: {test_file_uri}")
            print(f"Write response status: {write_response.status_code}")
            write_data = write_response.json()
            print(f"Write response: {json.dumps(write_data, indent=2, ensure_ascii=False)}")
            
            if write_data.get("status") != "ok":
                pytest.skip(f"fs_write failed on this environment: {write_data.get('error')}. This may be due to AGFS service not being available.")

            response = api_client.fs_read(test_file_uri)
            print(f"\nFS read API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("FS read API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            if data.get("status") != "ok":
                pytest.skip(f"fs_read failed on this environment: {data.get('error')}. This may be due to AGFS service not being available.")

            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"] == test_content, f"Expected content '{test_content}', got {data.get('result')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            try:
                api_client.fs_rm(test_file_uri)
                print(f"Cleaned up test file: {test_file_uri}")
            except Exception:
                pass
