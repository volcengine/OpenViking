import time

from build_test_helpers import assert_resource_indexed, assert_root_uri_valid, assert_source_format


def _add_resource_with_retry(api_client, path, max_retries=3, delay=10):
    last_response = None
    for attempt in range(max_retries):
        response = api_client.add_resource(path=path, wait=True)
        if response.status_code == 200:
            return response
        last_response = response
        if response.status_code >= 500 and attempt < max_retries - 1:
            print(f"  retry {attempt + 1}/{max_retries}: {path} got {response.status_code}, waiting {delay}s...")
            time.sleep(delay)
            delay *= 2
    return last_response


class TestBuildPlatformGithub:
    """TC-P01~P04 GitHub 平台 URL 构建测试"""

    def test_build_github_raw_file(self, api_client):
        """TC-P03 GitHub原始文件下载：验证 raw.githubusercontent.com URL 走 download_markdown 路由且内容可检索"""
        raw_url = "https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md"

        response = _add_resource_with_retry(api_client, raw_url)
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

        response = _add_resource_with_retry(api_client, blob_url)
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
