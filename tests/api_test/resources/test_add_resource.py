import json
import os
import tempfile
import uuid
import zipfile


class TestAddResource:
    def test_add_resource_simple(self, api_client):
        try:
            print("\n" + "=" * 80)
            print("测试步骤 1: 创建临时测试文件")
            print("=" * 80)

            with tempfile.TemporaryDirectory() as temp_dir:
                test_file_path = os.path.join(temp_dir, "test_file.txt")
                test_content = "这是一个测试文件的内容，用于验证 add_resource API。\n"
                test_content += "包含一些中文内容，测试场景化验证。"

                with open(test_file_path, "w", encoding="utf-8") as f:
                    f.write(test_content)

                print(f"✓ 临时文件创建成功: {test_file_path}")
                print(f"✓ 文件内容: {test_content}")

                print("\n" + "=" * 80)
                print("测试步骤 2: 调用 add_resource API 导入本地文件")
                print("=" * 80)

                response = api_client.add_resource(
                    path=test_file_path, reason="测试 add_resource API 功能", wait=True
                )
                print(f"\nAdd resource API status code: {response.status_code}")

                data = response.json()
                print("\n" + "=" * 80)
                print("Add Resource API Response:")
                print("=" * 80)
                print(json.dumps(data, indent=2, ensure_ascii=False))
                print("=" * 80 + "\n")

                assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
                assert data.get("error") is None, (
                    f"Expected error to be null, got {data.get('error')}"
                )
                assert "result" in data, "'result' field should exist"

                result = data.get("result", {})
                assert "root_uri" in result, "Result should contain 'root_uri' field"

                imported_uri = result.get("root_uri")
                assert imported_uri is not None, "Imported URI should not be None"
                assert imported_uri.startswith("viking://"), (
                    f"URI should start with 'viking://', got {imported_uri}"
                )

                print(f"✓ 文件成功导入，URI: {imported_uri}")

                print("\n" + "=" * 80)
                print("测试步骤 3: 验证导入的文件")
                print("=" * 80)

                response = api_client.fs_ls(imported_uri)
                print(f"\nList imported file API status code: {response.status_code}")

                data = response.json()
                assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"

                print("✓ 导入的文件验证成功！")
                print("\n" + "=" * 80)
                print("所有测试步骤完成 ✓")
                print("=" * 80)

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_add_resource_to_dir_semantics_rule1_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file_path = os.path.join(temp_dir, "a.txt")
            with open(test_file_path, "w", encoding="utf-8") as f:
                f.write("rule1")

            to_dir = f"viking://resources/test-add-resource-{uuid.uuid4().hex}/"
            response = api_client.add_resource(path=test_file_path, to=to_dir, wait=True)
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "ok"
            root_uri = data["result"]["root_uri"]
            assert root_uri == f"{to_dir}a.txt"

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200
            stat = stat_resp.json()
            assert stat.get("status") == "ok"

    def test_add_resource_to_file_semantics_rule2_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file_path = os.path.join(temp_dir, "a.txt")
            with open(test_file_path, "w", encoding="utf-8") as f:
                f.write("rule2")

            to_file = f"viking://resources/test-add-resource-{uuid.uuid4().hex}/b.txt"
            response = api_client.add_resource(path=test_file_path, to=to_file, wait=True)
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "ok"
            root_uri = data["result"]["root_uri"]
            assert root_uri == to_file

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200
            stat = stat_resp.json()
            assert stat.get("status") == "ok"

    def test_add_resource_to_dir_semantics_rule3_directory(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            myproj = os.path.join(temp_dir, "myproj")
            os.makedirs(myproj, exist_ok=True)
            with open(os.path.join(myproj, "README.md"), "w", encoding="utf-8") as f:
                f.write("# myproj\n")

            to_dir = f"viking://resources/test-add-resource-{uuid.uuid4().hex}/"
            response = api_client.add_resource(path=myproj, to=to_dir, wait=True)
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "ok"
            root_uri = data["result"]["root_uri"]
            assert root_uri == f"{to_dir}myproj/"

            tree_resp = api_client.fs_tree(root_uri)
            assert tree_resp.status_code == 200
            tree = tree_resp.json()
            assert tree.get("status") == "ok"

    def test_add_resource_to_no_trailing_slash_rule4_directory(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            myproj = os.path.join(temp_dir, "myproj")
            os.makedirs(myproj, exist_ok=True)
            with open(os.path.join(myproj, "README.md"), "w", encoding="utf-8") as f:
                f.write("# myproj\n")

            to_dir = f"viking://resources/test-add-resource-{uuid.uuid4().hex}"
            response = api_client.add_resource(path=myproj, to=to_dir, wait=True)
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "ok"
            root_uri = data["result"]["root_uri"]
            assert root_uri == to_dir

            tree_resp = api_client.fs_tree(root_uri)
            assert tree_resp.status_code == 200
            tree = tree_resp.json()
            assert tree.get("status") == "ok"

    def test_add_resource_zip_then_apply_rules_rule5(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            proj = os.path.join(temp_dir, "myproj")
            os.makedirs(proj, exist_ok=True)
            with open(os.path.join(proj, "README.md"), "w", encoding="utf-8") as f:
                f.write("# zip\n")

            zip_path = os.path.join(temp_dir, "myproj.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(os.path.join(proj, "README.md"), arcname="myproj/README.md")

            to_dir = f"viking://resources/test-add-resource-{uuid.uuid4().hex}/"
            response = api_client.add_resource(path=zip_path, to=to_dir, wait=True)
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "ok"
            root_uri = data["result"]["root_uri"]
            assert root_uri == f"{to_dir}myproj/"

            tree_resp = api_client.fs_tree(root_uri)
            assert tree_resp.status_code == 200
            tree = tree_resp.json()
            assert tree.get("status") == "ok"

    def test_add_resource_protect_resources_root_rule6_file(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file_path = os.path.join(temp_dir, "a.txt")
            with open(test_file_path, "w", encoding="utf-8") as f:
                f.write("rule6-file")

            response = api_client.add_resource(path=test_file_path, to="viking://resources", wait=True)
            assert response.status_code == 400
            data = response.json()
            assert data.get("status") == "error"
            assert data.get("error", {}).get("code") == "INVALID_ARGUMENT"

    def test_add_resource_protect_resources_root_rule6_dir(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            myproj = os.path.join(temp_dir, "myproj")
            os.makedirs(myproj, exist_ok=True)
            with open(os.path.join(myproj, "README.md"), "w", encoding="utf-8") as f:
                f.write("# myproj\n")

            response = api_client.add_resource(path=myproj, to="viking://resources", wait=True)
            assert response.status_code == 400
            data = response.json()
            assert data.get("status") == "error"
            assert data.get("error", {}).get("code") == "INVALID_ARGUMENT"
