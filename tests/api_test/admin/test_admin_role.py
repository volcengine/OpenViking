import json
import uuid


class TestAdminRole:
    def test_admin_set_role(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        test_user_id = f"test-user-{random_id}"

        try:
            response = api_client.admin_register_user("default", test_user_id, "user")
            print(f"\nAdmin register user API status code: {response.status_code}")

            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.admin_set_role("default", test_user_id, "admin")
            print(f"\nAdmin set role API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin Set Role API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

            response = api_client.admin_remove_user("default", test_user_id)

        except Exception as e:
            print(f"Error: {e}")
            raise
