from build_test_helpers import assert_resource_indexed, assert_root_uri_valid, assert_source_format


class TestBuildPlatformGeneralWeb:
    """TC-P10 通用网页 URL 构建测试"""

    def test_build_general_webpage(self, api_client):
        """TC-P10 通用网页构建：验证任意 HTTP URL 走 WEBPAGE 路由且 source_format=html、内容可检索"""
        web_url = "https://httpbin.org/html"

        response = api_client.add_resource(path=web_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)

        meta = result.get("meta", {})
        assert meta.get("url_type") in ("webpage", None), (
            f"meta.url_type 应为 webpage, 实际: {meta.get('url_type')}"
        )

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        assert_source_format(api_client, root_uri, ["html", "markdown"])

        assert_resource_indexed(api_client, root_uri, "httpbin")

        print(f"✓ TC-P10 通用网页构建通过, root_uri: {root_uri}")
