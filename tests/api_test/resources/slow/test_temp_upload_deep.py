import os
import tempfile
import uuid


class TestTempUploadDeep:
    def test_upload_md_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"upload_md_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("# Upload Test\n\nMarkdown content for temp upload.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"upload md via temp_upload should succeed, got {add_resp.status_code}"
            )
            result = add_resp.json().get("result", {})
            assert "root_uri" in result, f"result should contain root_uri, got {result}"

    def test_upload_txt_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"upload_txt_{uuid.uuid4().hex[:8]}.txt")
            with open(test_file, "w") as f:
                f.write("Plain text content for temp upload test.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"upload txt should succeed, got {add_resp.status_code}"
            )

    def test_upload_py_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"upload_py_{uuid.uuid4().hex[:8]}.py")
            with open(test_file, "w") as f:
                f.write("def hello():\n    print('Hello from temp upload')\n")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"upload py should succeed, got {add_resp.status_code}"
            )

    def test_upload_json_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"upload_json_{uuid.uuid4().hex[:8]}.json")
            with open(test_file, "w") as f:
                f.write('{"name": "test", "value": 42}')
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"upload json should succeed, got {add_resp.status_code}"
            )

    def test_upload_unicode_filename(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"上传测试_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("# 中文文件名测试\n\nUnicode filename upload test.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"upload unicode filename should succeed, got {add_resp.status_code}"
            )

    def test_upload_empty_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"empty_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w"):
                pass
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"upload empty file should return 200, got {add_resp.status_code}"
            )

    def test_upload_result_has_queue_status(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, f"queue_{uuid.uuid4().hex[:8]}.md")
            with open(test_file, "w") as f:
                f.write("# Queue status test\n\nContent for queue status check.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200
            result = add_resp.json().get("result", {})
            assert "queue_status" in result, (
                f"result should contain queue_status, got keys: {list(result.keys())}"
            )
            qs = result["queue_status"]
            assert isinstance(qs, dict), f"queue_status should be dict, got {type(qs).__name__}"
