import json


class TestGlob:
    def test_glob_basic(self, api_client):
        try:
            response = api_client.glob("**/*", "viking://user/")
            print(f"\nGlob API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Glob API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"] is not None, "'result' should not be null"
            assert "matches" in data["result"], "'matches' field should exist"
            assert isinstance(data["result"]["matches"], list), "'matches' should be a list"

        except Exception as e:
            print(f"Error: {e}")
            raise
