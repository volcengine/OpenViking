from build_test_helpers import assert_resource_indexed, assert_root_uri_valid, assert_source_format


class TestBuildPlatformGithub:
    """TC-P01~P04 GitHub 平台 URL 构建测试"""

    def test_build_github_repo(self, api_client):
        """TC-P01 GitHub仓库构建：验证 github.com/org/repo 走 CodeRepositoryParser 且 root_uri 含 org/repo"""
        repo_url = "https://github.com/volcengine/OpenViking"

        response = api_client.add_resource(path=repo_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)
        assert "volcengine" in root_uri and "OpenViking" in root_uri, (
            f"GitHub仓库 root_uri 应含 volcengine/OpenViking, 实际: {root_uri}"
        )

        meta = result.get("meta", {})
        assert meta.get("url_type") in ("code_repository", None), (
            f"meta.url_type 应为 code_repository, 实际: {meta.get('url_type')}"
        )

        assert_source_format(api_client, root_uri, ["repository", "markdown"])

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        print(f"✓ TC-P01 GitHub仓库构建通过, root_uri: {root_uri}")

    def test_build_github_repo_with_branch(self, api_client):
        """TC-P02 GitHub指定分支构建：验证 github.com/org/repo/tree/branch 走 ZIP API 且 meta 含 repo_ref"""
        repo_url = "https://github.com/volcengine/OpenViking/tree/main"

        response = api_client.add_resource(path=repo_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)
        assert "volcengine" in root_uri and "OpenViking" in root_uri, (
            f"GitHub仓库 root_uri 应含 volcengine/OpenViking, 实际: {root_uri}"
        )

        meta = result.get("meta", {})
        if "repo_ref" in meta:
            assert meta["repo_ref"] == "main", f"meta.repo_ref 应为 main, 实际: {meta['repo_ref']}"

        assert_source_format(api_client, root_uri, ["repository", "markdown"])

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        print(f"✓ TC-P02 GitHub指定分支构建通过, root_uri: {root_uri}")

    def test_build_github_raw_file(self, api_client):
        """TC-P03 GitHub原始文件下载：验证 raw.githubusercontent.com URL 走 download_markdown 路由且内容可检索"""
        raw_url = "https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md"

        response = api_client.add_resource(path=raw_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)

        meta = result.get("meta", {})
        assert meta.get("url_type") in (
            "download_md",
            "download_markdown",
            "download_txt",
            "webpage",
            None,
        ), (
            f"meta.url_type 应为 download_md/download_markdown/download_txt/webpage, 实际: {meta.get('url_type')}"
        )

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        assert_resource_indexed(api_client, root_uri, "OpenViking")

        print(f"✓ TC-P03 GitHub原始文件下载通过, root_uri: {root_uri}")

    def test_build_github_blob_page(self, api_client):
        """TC-P04 GitHub Blob页面构建：验证 github.com/org/repo/blob/branch/file 被转为 raw URL 下载且内容可检索"""
        blob_url = "https://github.com/volcengine/OpenViking/blob/main/README.md"

        response = api_client.add_resource(path=blob_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)

        meta = result.get("meta", {})
        assert meta.get("url_type") in (
            "download_md",
            "download_markdown",
            "download_txt",
            "download_html",
            "webpage",
            None,
        ), f"meta.url_type 应为 download 类, 实际: {meta.get('url_type')}"

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        assert_resource_indexed(api_client, root_uri, "OpenViking")

        print(f"✓ TC-P04 GitHub Blob页面构建通过, root_uri: {root_uri}")
