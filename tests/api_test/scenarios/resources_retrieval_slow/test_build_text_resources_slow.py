import uuid

from build_test_helpers import (
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
    cleanup_temp_dir,
    create_test_file,
)


class TestBuildTextResourcesSlow:
    """TC-B01, B02, B14, B15 文本类资源构建测试"""

    def test_build_txt_file(self, api_client):
        """TC-B01 纯文本文件构建：验证 .txt 文件添加后 source_format=text 且内容可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"txt_keyword_{random_id}"
        content = f"纯文本测试文件 {random_id}\n包含唯一关键词：{unique_keyword}\n用于验证txt文件构建产物。"

        test_file_path, temp_dir = create_test_file(content=content, suffix=".txt")
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

            assert_source_format(api_client, root_uri, ["text", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B01 纯文本文件构建通过, root_uri: {root_uri}")
        finally:
            cleanup_temp_dir(temp_dir)

    def test_build_markdown_file(self, api_client):
        """TC-B02 Markdown文件构建：验证 .md 文件添加后 heading 结构保留为子节点"""
        from build_test_helpers import assert_tree_has_child_nodes

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
        """TC-B15 空文件构建：验证空 .txt 文件添加后 root_uri 有效、fs_stat 可查、source_format 合理"""
        from build_test_helpers import assert_source_format

        test_file_path, temp_dir = create_test_file(content="", suffix=".txt")
        try:
            response = api_client.add_resource(path=test_file_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            if data.get("status") == "error":
                error_msg = str(data.get("error", ""))
                assert "empty" in error_msg.lower() or "error" in error_msg.lower(), (
                    f"空文件错误信息应包含 empty/error, 实际: {error_msg}"
                )
                print(f"✓ TC-B15 空文件构建通过(服务端拒绝空文件): {error_msg[:80]}")
                return

            assert data.get("status") == "ok"

            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors)
                assert (
                    "empty" in inner_msg.lower()
                    or "error" in inner_msg.lower()
                    or "parse" in inner_msg.lower()
                ), f"空文件内层错误应包含 empty/error/parse, 实际: {inner_msg}"
                print(f"✓ TC-B15 空文件构建通过(内层解析错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200, f"空文件 fs_stat 应返回200, root_uri: {root_uri}"

            assert_source_format(api_client, root_uri, ["text", "markdown", ""])

            print(f"✓ TC-B15 空文件构建通过, root_uri: {root_uri}")
        finally:
            cleanup_temp_dir(temp_dir)

    def test_build_raw_content(self, api_client):
        """TC-B14 原始内容字符串构建：验证纯文本内容写入文件后添加可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"raw_keyword_{random_id}"
        raw_content = (
            f"原始内容测试 {random_id}\n包含唯一关键词：{unique_keyword}\n用于验证字符串输入构建。"
        )

        test_file_path, temp_dir = create_test_file(content=raw_content, suffix=".txt")
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

            assert_source_format(api_client, root_uri, ["text", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B14 原始内容字符串构建通过, root_uri: {root_uri}")
        finally:
            cleanup_temp_dir(temp_dir)
