import uuid

from build_test_helpers import (
    _extract_error_message,
    assert_root_uri_valid,
    cleanup_temp_dir,
    create_test_file,
)


class TestBuildUriParams:
    """TC-E11(参数部分), TC-E18 URI 参数与边界测试（快速用例，≤20s）"""

    def test_build_with_parent_param(self, api_client):
        """TC-E18 指定parent参数构建：验证 root_uri 以 parent 为前缀"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"parent_keyword_{random_id}"
        parent_uri = f"viking://resources/parent_test_{random_id}"

        mkdir_resp = api_client.fs_mkdir(parent_uri)
        assert mkdir_resp.status_code == 200, (
            f"无法创建parent目录, fs_mkdir返回: {mkdir_resp.status_code}, body: {mkdir_resp.text}"
        )

        content = f"parent参数测试 {random_id}\n包含唯一关键词：{unique_keyword}"
        test_file_path, temp_dir = create_test_file(content=content, suffix=".txt")
        try:
            response = api_client.add_resource(path=test_file_path, parent=parent_uri, wait=True)
            assert response.status_code == 200

            data = response.json()
            if data.get("status") == "error":
                error_msg = _extract_error_message(data)
                raise AssertionError(f"parent参数构建失败, 服务端不支持parent参数: {error_msg}")

            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors)
                raise AssertionError(f"parent参数构建失败(内层错误): {inner_msg}")

            root_uri = result.get("root_uri") if isinstance(result, dict) else None
            assert_root_uri_valid(root_uri)
            assert root_uri.startswith(parent_uri), (
                f"指定parent时 root_uri 应以 parent 为前缀, parent: {parent_uri}, 实际: {root_uri}"
            )

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            print(f"✓ TC-E18 指定parent参数构建通过, root_uri: {root_uri}")
        finally:
            cleanup_temp_dir(temp_dir)

    def test_build_non_resources_scope_rejected(self, api_client):
        """TC-E11 非resources scope拒绝：验证 to=viking://sessions/xxx 返回错误"""
        random_id = str(uuid.uuid4())[:8]
        test_content = f"scope测试内容 {random_id}"
        test_file_path, temp_dir = create_test_file(content=test_content, suffix=".txt")
        try:
            response = api_client.add_resource(
                path=test_file_path,
                to="viking://sessions/test_session",
                wait=True,
            )

            data = response.json()
            if data.get("status") == "error":
                error_msg = _extract_error_message(data).lower()
                assert (
                    "scope" in error_msg
                    or "resources" in error_msg
                    or "invalid" in error_msg
                    or "permission" in error_msg
                    or "internal" in error_msg
                ), f"scope拒绝应包含 scope/resources/invalid/permission/internal, 实际: {error_msg}"
                print("✓ TC-E11 非resources scope拒绝通过")
                return

            if data.get("status") == "ok":
                result = data.get("result", {})
                root_uri = result.get("root_uri", "")
                assert "sessions" not in root_uri, (
                    f"非resources scope不应成功写入sessions, root_uri: {root_uri}"
                )
                print("✓ TC-E11 非resources scope处理通过(服务端重定向)")
                return
        finally:
            cleanup_temp_dir(temp_dir)
