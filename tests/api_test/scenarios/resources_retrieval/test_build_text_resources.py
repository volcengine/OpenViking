import uuid

from build_test_helpers import (
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
    assert_tree_has_child_nodes,
    cleanup_temp_dir,
    create_test_file,
)


class TestBuildTextResources:
    """TC-B02, B15 文本类资源构建测试（快速用例，≤20s）"""

    def test_build_markdown_file(self, api_client):
        """TC-B02 Markdown文件构建：验证 .md 文件添加后 heading 结构保留为子节点"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"md_keyword_{random_id}"
        content = (
            f"# Markdown测试标题 {random_id}\n\n"
            f"包含唯一关键词：{unique_keyword}\n\n"
            f"## 第一节\n\n第一段内容。\n\n"
            f"## 第二节\n\n第二段内容。\n\n"
            f"### 子节\n\n子节内容。\n"
        )

        test_file_path, temp_dir = create_test_file(content=content, suffix=".md")
        try:
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, "markdown")

            assert_tree_has_child_nodes(api_client, root_uri, min_nodes=1)

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B02 Markdown文件构建通过, root_uri: {root_uri}")
        finally:
            cleanup_temp_dir(temp_dir)

    def test_build_empty_file(self, api_client):
        """TC-B15 空文件构建：验证空 .txt 文件添加不崩溃并返回合理结果"""
        test_file_path, temp_dir = create_test_file(content="", suffix=".txt")
        try:
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") in ("ok", "error"), (
                f"空文件构建应返回 ok 或 error, 实际: {data.get('status')}"
            )

            if data.get("status") == "ok":
                result = data.get("result", {})
                root_uri = result.get("root_uri")
                if root_uri:
                    assert_root_uri_valid(root_uri)

            print("✓ TC-B15 空文件构建通过")
        finally:
            cleanup_temp_dir(temp_dir)
