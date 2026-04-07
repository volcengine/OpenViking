import os
import uuid


class TestPack:
    def test_export_ovpack(self, api_client):
        random_id = str(uuid.uuid4())[:8]
        test_export_path = f"./exports/export-{random_id}.ovpack"

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
            assert len(result) > 0, "No files found in root"

            test_uri = result[0].get("uri")
            assert test_uri is not None, "No suitable file found"

            response = api_client.export_ovpack(uri=test_uri, to=test_export_path)
            print(f"\nExport ovpack API status code: {response.status_code}")

            # The response is now a file stream, not JSON
            assert response.status_code == 200, f"Expected status 200, got {response.status_code}"

            # Verify the file was created
            assert os.path.exists(test_export_path), (
                f"Export file not created at {test_export_path}"
            )
            assert os.path.getsize(test_export_path) > 0, "Export file is empty"

            print(f"\nSuccessfully exported to {test_export_path}")
            print(f"File size: {os.path.getsize(test_export_path)} bytes")

        except Exception as e:
            print(f"Error: {e}")
            raise
