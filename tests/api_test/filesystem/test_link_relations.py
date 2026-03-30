import json


class TestLinkRelations:
    def test_link_relations_unlink(self, api_client):
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
            assert len(result) >= 2, (
                f"Not enough files in root directory to test link/relations/unlink, found {len(result)} files"
            )

            file1 = result[0].get("uri")
            file2 = result[1].get("uri")

            assert file1 is not None, "First file URI should not be None"
            assert file2 is not None, "Second file URI should not be None"

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
