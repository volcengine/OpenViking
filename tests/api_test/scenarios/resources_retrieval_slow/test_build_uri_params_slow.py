import uuid

from build_test_helpers import (
    cleanup_temp_dir,
    create_test_file,
)


class TestBuildUriParamsSlow:
    """TC-E17 指定to参数构建"""

    def test_build_with_to_param(self, api_client):
        """TC-E17 指定to参数构建：验证 root_uri == to"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"to_keyword_{random_id}"
        target_uri = f"viking://resources/to_test_{random_id}"

        content = f"to参数测试 {random_id}\n包含唯一关键词：{unique_keyword}"
        test_file_path, temp_dir = create_test_file(content=content, suffix=".txt")
        try:
            response = api_client.add_resource(path=test_file_path, to=target_uri, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert root_uri == target_uri, (
                f"指定to时 root_uri 应等于 to, 期望: {target_uri}, 实际: {root_uri}"
            )

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            print(f"✓ TC-E17 指定to参数构建通过, root_uri: {root_uri}")
        finally:
            cleanup_temp_dir(temp_dir)
