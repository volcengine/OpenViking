import json


class TestFind:
    def test_find_basic(self, api_client):
        try:
            response = api_client.find("how to authenticate", limit=10)
            print(f"\nFind API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Find API Response (basic find):")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"] is not None, "'result' should not be null"

            result = data["result"]
            assert "memories" in result, "'memories' field should exist"
            assert isinstance(result["memories"], list), "'memories' should be a list"
            assert "resources" in result, "'resources' field should exist"
            assert isinstance(result["resources"], list), "'resources' should be a list"
            assert "skills" in result, "'skills' field should exist"
            assert isinstance(result["skills"], list), "'skills' should be a list"
            assert "total" in result, "'total' field should exist"
            assert isinstance(result["total"], int), "'total' should be an integer"

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_find_with_different_query(self, api_client):
        try:
            response = api_client.find("what is OpenViking", limit=5)
            print(f"\nFind API (different query) status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Find API Response (different query):")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"] is not None, "'result' should not be null"

        except Exception as e:
            print(f"Error: {e}")
            raise
