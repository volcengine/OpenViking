from build_test_helpers import assert_resource_indexed, assert_root_uri_valid, assert_source_format


class TestBuildPlatformArxiv:
    """TC-P06~P07 arXiv 平台 URL 构建测试"""

    def test_build_arxiv_pdf(self, api_client):
        """TC-P06 arXiv PDF构建：验证 arxiv.org/pdf/ URL 走 DOWNLOAD_PDF 路由且 source_format=pdf"""
        arxiv_pdf_url = "https://arxiv.org/pdf/2301.00234"

        response = api_client.add_resource(path=arxiv_pdf_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)

        meta = result.get("meta", {})
        assert meta.get("url_type") in ("download_pdf", "webpage", None), (
            f"meta.url_type 应为 download_pdf, 实际: {meta.get('url_type')}"
        )

        assert_source_format(api_client, root_uri, ["pdf", "markdown"])

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        print(f"✓ TC-P06 arXiv PDF构建通过, root_uri: {root_uri}")

    def test_build_arxiv_abstract_page(self, api_client):
        """TC-P07 arXiv HTML页面构建：验证 arxiv.org/abs/ URL 走 WEBPAGE 路由且摘要可检索"""
        arxiv_abs_url = "https://arxiv.org/abs/2301.00234"

        response = api_client.add_resource(path=arxiv_abs_url, wait=True)
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

        assert_source_format(api_client, root_uri, ["html", "markdown"])

        stat_resp = api_client.fs_stat(root_uri)
        assert stat_resp.status_code == 200

        assert_resource_indexed(api_client, root_uri, "arxiv")

        print(f"✓ TC-P07 arXiv HTML页面构建通过, root_uri: {root_uri}")
