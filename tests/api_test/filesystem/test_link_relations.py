import json
import uuid


class TestLinkRelations:
    def test_link_relations_unlink(self, api_client):
        random_id = uuid.uuid4().hex[:8]
        file1 = f"viking://resources/test-link-src-{random_id}"
        file2 = f"viking://resources/test-link-dst-{random_id}"
        try:
            response = api_client.fs_mkdir(file1)
            print(f"\nCreate relation source API status code: {response.status_code}")
            assert response.status_code == 200, (
                f"Failed to create relation source: {response.status_code}"
            )
            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.fs_mkdir(file2)
            print(f"\nCreate relation target API status code: {response.status_code}")
            assert response.status_code == 200, (
                f"Failed to create relation target: {response.status_code}"
            )
            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.link(file1, [file2], "Test link")
            print(f"\nLink API status code: {response.status_code}")
            data = response.json()
            print("\n" + "=" * 80)
            print("Link API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.relations(file1)
            print(f"\nRelations API status code: {response.status_code}")
            data = response.json()
            print("\n" + "=" * 80)
            print("Relations API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.unlink(file1, file2)
            print(f"\nUnlink API status code: {response.status_code}")
            data = response.json()
            print("\n" + "=" * 80)
            print("Unlink API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            api_client.fs_rm(file1, recursive=True)
            api_client.fs_rm(file2, recursive=True)
