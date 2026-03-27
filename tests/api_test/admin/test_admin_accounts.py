import json
import uuid


class TestAdminAccounts:
    def test_admin_list_accounts(self, api_client):
        try:
            response = api_client.admin_list_accounts()
            print(f"\nAdmin list accounts API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin List Accounts API Response:")
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

    def test_admin_create_delete_account(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        test_account_id = f"test-account-{random_id}"
        test_admin_user_id = f"test-admin-{random_id}"

        try:
            response = api_client.admin_create_account(test_account_id, test_admin_user_id)
            print(f"\nAdmin create account API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin Create Account API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"]["account_id"] == test_account_id, "Account ID mismatch"
            assert data["result"]["admin_user_id"] == test_admin_user_id, "Admin user ID mismatch"

            response = api_client.admin_delete_account(test_account_id)
            print(f"\nAdmin delete account API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Admin Delete Account API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
