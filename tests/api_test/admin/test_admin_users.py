import json
import uuid


class TestAdminUsers:
    def test_admin_list_users(self, api_client):
        try:
            response = api_client.admin_list_users("default")
            print(f"\nAdmin list users API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin List Users API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert isinstance(data["result"], list), "'result' should be a list"

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_admin_register_remove_user(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        test_user_id = f"test-user-{random_id}"

        try:
            response = api_client.admin_register_user("default", test_user_id, "user")
            print(f"\nAdmin register user API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin Register User API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"]["user_id"] == test_user_id, "User ID mismatch"

            response = api_client.admin_remove_user("default", test_user_id)
            print(f"\nAdmin remove user API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin Remove User API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
