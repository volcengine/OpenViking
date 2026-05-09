import os
import shutil
import tempfile
import uuid
import zipfile

from build_test_helpers import assert_resource_indexed, assert_root_uri_valid, assert_source_format


class TestBuildArchiveResources:
    """TC-B10~B12 压缩包与目录类资源构建测试"""

    def test_build_zip_file(self, api_client):
        """TC-B10 ZIP压缩包构建：验证 .zip 文件添加后解压并按目录结构处理"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"zip_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, f"test_{random_id}.zip")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("file_0.txt", f"压缩包内文件0\n包含唯一关键词：{unique_keyword}")
            zf.writestr("file_1.txt", f"压缩包内文件1\n测试数据 {random_id}")
            zf.writestr("subdir/nested.txt", "嵌套文件内容")

        try:
            response = api_client.add_resource(path=zip_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["zip", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B10 ZIP压缩包构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_directory(self, api_client):
        """TC-B11 目录递归构建：验证目录添加后子文件全部被索引"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"dir_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()

        for i in range(3):
            file_path = os.path.join(temp_dir, f"file_{i}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"目录内文件 {i}\n测试数据 {random_id}")

        subdir = os.path.join(temp_dir, "subdir")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "nested_file.txt"), "w", encoding="utf-8") as f:
            f.write(f"嵌套文件内容\n包含唯一关键词：{unique_keyword}")

        try:
            response = api_client.add_resource(path=temp_dir, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["directory", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B11 目录递归构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_code_repository_url(self, api_client):
        """TC-B12 代码仓库URL构建：验证 GitHub 仓库 URL 添加后 source_format=repository 且 URI 含 org/repo"""
        repo_url = "https://github.com/volcengine/OpenViking"

        response = api_client.add_resource(path=repo_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)
        assert "volcengine" in root_uri and "OpenViking" in root_uri, (
            f"代码仓库 root_uri 应含 org/repo, 实际: {root_uri}"
        )

        meta = result.get("meta", {})
        assert meta.get("url_type") in ("code_repository", None), (
            f"meta.url_type 应为 code_repository, 实际: {meta.get('url_type')}"
        )

        assert_source_format(api_client, root_uri, ["repository", "markdown"])

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        print(f"✓ TC-B12 代码仓库URL构建通过, root_uri: {root_uri}")
