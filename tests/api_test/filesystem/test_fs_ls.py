import json

import pytest
import requests


class TestFsLs:
    def test_fs_ls_root(self, api_client):
        try:
            response = api_client.fs_ls("viking://")
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, f"FS ls failed with status {response.status_code}"

        if response.status_code == 200:
            data = response.json()
            print("\n" + "=" * 80)
            print("FS ls (root) API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert isinstance(data["result"], list), "'result' should be a list"
            assert len(data["result"]) > 0, "'result' list should not be empty"

    def test_fs_ls_time_window(self, api_client):
        """A time window narrows the listing to the entries inside it."""
        try:
            response = api_client.fs_ls("viking://", since="2099-01-01")
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, f"FS ls failed with status {response.status_code}"

        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert isinstance(data.get("result"), list), "'result' should be a list"
            # Every entry predates the window, so none of them may come back.
            assert data["result"] == [], "Entries outside the time window must be filtered out"

    def test_fs_ls_rejects_an_inverted_time_window(self, api_client):
        try:
            response = api_client.fs_ls("viking://", since="2099-01-01", until="2000-01-01")
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, f"FS ls failed with status {response.status_code}"
        assert response.status_code == 400, "An inverted time window must be rejected"
