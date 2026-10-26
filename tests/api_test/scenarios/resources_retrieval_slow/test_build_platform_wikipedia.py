import pytest
from build_test_helpers import (
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
)


class TestBuildPlatformWikipedia:
    """TC-P05 Wikipedia 平台 URL 构建测试"""

    WIKI_URLS = [
        "https://en.wikipedia.org/api/rest_v1/page/summary/Software_testing",
        "https://en.wikipedia.org/wiki/Software_testing",
    ]

    def test_build_wikipedia_page(self, api_client):
        """TC-P05 Wikipedia页面构建：验证 wikipedia.org URL 走 WEBPAGE 路由且内容可检索"""
        for wiki_url in self.WIKI_URLS:
            response = api_client.add_resource(path=wiki_url, wait=True)
            data = response.json()
            if response.status_code == 403:
                assert data["status"] == "error"
                error = data["error"]
                assert error["code"] == "PERMISSION_DENIED"
                assert error["details"]["upstream_status_code"] == 403
                assert error["details"]["source"] == wiki_url
                continue
            if response.status_code == 412:
                assert data["status"] == "error"
                assert data["error"]["code"] == "FAILED_PRECONDITION"
                assert "site disallows crawlers" in data["error"]["message"]
                continue

            assert response.status_code == 200, response.text
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert root_uri, "Wikipedia页面构建应返回root_uri, 实际为空"
            assert_root_uri_valid(root_uri)

            meta = result.get("meta", {})
            assert meta.get("url_type") in ("webpage", "download_text", "download_html", None), (
                f"meta.url_type 应为 webpage/download_text/download_html, 实际: {meta.get('url_type')}"
            )

            assert_source_format(api_client, root_uri, ["html", "markdown"])

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_resource_indexed(api_client, root_uri, "software testing")

            print(f"✓ TC-P05 Wikipedia页面构建通过, root_uri: {root_uri}")
            return

        pytest.skip("所有 Wikipedia URL 均拒绝访问或禁止爬虫，未验证内容构建")
