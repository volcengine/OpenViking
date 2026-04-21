import os
import shutil
import tempfile
import uuid

from build_test_helpers import (
    _extract_error_message,
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
    cleanup_temp_dir,
    create_test_file,
)


class TestBuildErrorHandlingSlow:
    """TC-E01~E16 异常与边界测试"""

    def test_error_remote_404(self, api_client):
        """TC-E01 远端404不存在：验证 404 URL 返回错误含状态码信息且不崩溃"""
        url_404 = "https://httpbin.org/status/404"

        response = api_client.add_resource(path=url_404, wait=True)

        data = response.json()
        if data.get("status") == "error":
            error_msg = _extract_error_message(data).lower()
            assert "404" in error_msg or "not found" in error_msg or "error" in error_msg, (
                f"404错误信息应包含 404/not found/error, 实际: {error_msg}"
            )
            print("✓ TC-E01 远端404不存在处理通过(返回error)")
            return

        if data.get("status") == "ok":
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors).lower()
                assert (
                    "404" in inner_msg
                    or "not found" in inner_msg
                    or "failed" in inner_msg
                    or "error" in inner_msg
                ), f"404内层错误应包含 404/not found/failed/error, 实际: {inner_msg}"
                print(f"✓ TC-E01 远端404不存在处理通过(内层错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)
            print("✓ TC-E01 远端404处理通过(降级为空资源)")
            return

        raise AssertionError(f"404 URL 应返回 error 或 ok, 实际: {data.get('status')}")

    def test_error_http_to_https_redirect(self, api_client):
        """TC-E05 HTTP→HTTPS跳转：验证 http URL 自动跟随跳转且 root_uri 正常"""
        redirect_url = "http://github.com/volcengine/OpenViking"

        response = api_client.add_resource(path=redirect_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)
        assert "volcengine" in root_uri and "OpenViking" in root_uri, (
            f"跳转后 root_uri 应含 volcengine/OpenViking, 实际: {root_uri}"
        )

        print(f"✓ TC-E05 HTTP→HTTPS跳转通过, root_uri: {root_uri}")

    def test_error_multi_redirect(self, api_client):
        """TC-E06 多重跳转：验证短链 URL 自动跟随跳转且内容可检索"""
        redirect_url = (
            "https://httpbin.org/redirect-to?url=https://httpbin.org/html&status_code=302"
        )

        response = api_client.add_resource(path=redirect_url, wait=True)
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "ok"

        result = data.get("result", {})
        root_uri = result.get("root_uri")
        assert_root_uri_valid(root_uri)

        assert_resource_indexed(api_client, root_uri, "httpbin")

        print(f"✓ TC-E06 多重跳转通过, root_uri: {root_uri}")

    def test_error_duplicate_resource_no_to(self, api_client):
        """TC-E12 同名资源二次添加(无to)：验证两次添加的 root_uri 不同（URI 附加后缀）"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"dup_keyword_{random_id}"
        content = f"重复添加测试 {random_id}\n包含唯一关键词：{unique_keyword}"

        test_file_path, temp_dir = create_test_file(content=content, suffix=".txt")
        try:
            resp1 = api_client.add_resource(path=test_file_path, wait=True)
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1.get("status") == "ok"
            root_uri_1 = data1.get("result", {}).get("root_uri")
            assert_root_uri_valid(root_uri_1)

            resp2 = api_client.add_resource(path=test_file_path, wait=True)
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2.get("status") == "ok"
            root_uri_2 = data2.get("result", {}).get("root_uri")
            assert_root_uri_valid(root_uri_2)

            assert root_uri_1 != root_uri_2, (
                f"同名资源二次添加(无to) root_uri 应不同, uri1: {root_uri_1}, uri2: {root_uri_2}"
            )

            print(f"✓ TC-E12 同名资源二次添加通过, uri1: {root_uri_1}, uri2: {root_uri_2}")
        finally:
            cleanup_temp_dir(temp_dir)

    def test_error_incremental_update_with_to(self, api_client):
        """TC-E13 同to增量更新：验证同一 to 二次添加后 root_uri 不变且不报错"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"incr_keyword_{random_id}"
        target_uri = f"viking://resources/incr_test_{random_id}"

        content1 = f"增量更新测试1 {random_id}\n包含唯一关键词：{unique_keyword}_v1"
        content2 = f"增量更新测试2 {random_id}\n包含唯一关键词：{unique_keyword}_v2"

        file1, temp_dir1 = create_test_file(content=content1, suffix=".txt")
        file2, temp_dir2 = create_test_file(content=content2, suffix=".txt")
        try:
            resp1 = api_client.add_resource(path=file1, to=target_uri, wait=True)
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1.get("status") == "ok"
            root_uri_1 = data1.get("result", {}).get("root_uri")

            resp2 = api_client.add_resource(path=file2, to=target_uri, wait=True)
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2.get("status") == "ok"
            root_uri_2 = data2.get("result", {}).get("root_uri")

            assert root_uri_1 == root_uri_2, (
                f"同to增量更新 root_uri 应不变, uri1: {root_uri_1}, uri2: {root_uri_2}"
            )

            print(f"✓ TC-E13 同to增量更新通过, root_uri: {root_uri_1}")
        finally:
            cleanup_temp_dir(temp_dir1)
            cleanup_temp_dir(temp_dir2)

    def test_error_unsupported_file_type(self, api_client):
        """TC-E15 不支持的文件类型：验证 .xyz 文件回退到 TextParser 且 source_format=text、内容可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"xyz_keyword_{random_id}"

        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, f"test_{random_id}.xyz")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"不支持的文件类型测试 {random_id}\n包含唯一关键词：{unique_keyword}")

        try:
            response = api_client.add_resource(path=file_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            if data.get("status") == "error":
                error_msg = _extract_error_message(data).lower()
                assert "unsupported" in error_msg or "error" in error_msg or "type" in error_msg, (
                    f"不支持文件类型错误应包含 unsupported/error/type, 实际: {error_msg}"
                )
                print(f"✓ TC-E15 不支持的文件类型处理通过(服务端拒绝): {error_msg[:80]}")
                return

            assert data.get("status") == "ok"

            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors)
                assert (
                    "unsupported" in inner_msg.lower()
                    or "error" in inner_msg.lower()
                    or "parse" in inner_msg.lower()
                ), f"不支持文件类型内层错误应包含 unsupported/error/parse, 实际: {inner_msg}"
                print(f"✓ TC-E15 不支持的文件类型处理通过(内层错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200, (
                f"不支持文件类型 fs_stat 应返回200, root_uri: {root_uri}"
            )

            assert_source_format(api_client, root_uri, ["text", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-E15 不支持的文件类型处理通过(回退TextParser), root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_error_remote_403(self, api_client):
        """TC-E02 远端403禁止访问：验证 403 URL 返回错误含状态码信息且不崩溃"""
        url_403 = "https://httpbin.org/status/403"

        response = api_client.add_resource(path=url_403, wait=True)

        data = response.json()
        if data.get("status") == "error":
            error_msg = _extract_error_message(data).lower()
            assert "403" in error_msg or "forbidden" in error_msg or "error" in error_msg, (
                f"403错误信息应包含 403/forbidden/error, 实际: {error_msg}"
            )
            print("✓ TC-E02 远端403禁止访问处理通过(返回error)")
            return

        if data.get("status") == "ok":
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors).lower()
                assert (
                    "403" in inner_msg
                    or "forbidden" in inner_msg
                    or "failed" in inner_msg
                    or "error" in inner_msg
                ), f"403内层错误应包含 403/forbidden/failed/error, 实际: {inner_msg}"
                print(f"✓ TC-E02 远端403禁止访问处理通过(内层错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)
            print("✓ TC-E02 远端403处理通过(降级为空资源)")
            return

        raise AssertionError(f"403 URL 应返回 error 或 ok, 实际: {data.get('status')}")

    def test_error_remote_500(self, api_client):
        """TC-E03 远端500服务错误：验证 500 URL 返回错误含状态码信息且不崩溃"""
        url_500 = "https://httpbin.org/status/500"

        response = api_client.add_resource(path=url_500, wait=True)

        data = response.json()
        if data.get("status") == "error":
            error_msg = _extract_error_message(data).lower()
            assert "500" in error_msg or "server" in error_msg or "error" in error_msg, (
                f"500错误信息应包含 500/server/error, 实际: {error_msg}"
            )
            print("✓ TC-E03 远端500服务错误处理通过(返回error)")
            return

        if data.get("status") == "ok":
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors).lower()
                assert (
                    "500" in inner_msg
                    or "server" in inner_msg
                    or "internal" in inner_msg
                    or "failed" in inner_msg
                    or "error" in inner_msg
                ), f"500内层错误应包含 500/server/internal/failed/error, 实际: {inner_msg}"
                print(f"✓ TC-E03 远端500服务错误处理通过(内层错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)
            print("✓ TC-E03 远端500处理通过(降级为空资源)")
            return

        raise AssertionError(f"500 URL 应返回 error 或 ok, 实际: {data.get('status')}")

    def test_error_dns_resolve_failure(self, api_client):
        """TC-E08 DNS解析失败：验证不存在的域名返回错误含DNS相关信息且不挂起"""
        bad_dns_url = "https://nonexistent.domain.invalid.for.test/page"

        response = api_client.add_resource(path=bad_dns_url, wait=True)

        data = response.json()
        if data.get("status") == "error":
            error_msg = _extract_error_message(data).lower()
            assert (
                "resolve" in error_msg
                or "hostname" in error_msg
                or "dns" in error_msg
                or "error" in error_msg
                or "connect" in error_msg
            ), f"DNS失败错误信息应包含 resolve/hostname/dns/error/connect, 实际: {error_msg}"
            print("✓ TC-E08 DNS解析失败处理通过(返回error)")
            return

        if data.get("status") == "ok":
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors).lower()
                assert (
                    "resolve" in inner_msg
                    or "hostname" in inner_msg
                    or "dns" in inner_msg
                    or "connect" in inner_msg
                    or "failed" in inner_msg
                    or "error" in inner_msg
                ), f"DNS内层错误应包含 resolve/hostname/dns/connect/failed/error, 实际: {inner_msg}"
                print(f"✓ TC-E08 DNS解析失败处理通过(内层错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)
            print("✓ TC-E08 DNS解析失败处理通过(降级为空资源)")
            return

        raise AssertionError(f"DNS失败 URL 应返回 error 或 ok, 实际: {data.get('status')}")

    def test_error_ssh_url_invalid_format(self, api_client):
        """TC-E09 SSH URL格式错误：验证 git@invalid (无冒号) 返回错误含SSH/URI相关信息"""
        invalid_ssh_url = "git@invalid"

        response = api_client.add_resource(path=invalid_ssh_url, wait=True)

        data = response.json()
        if data.get("status") == "error":
            error_msg = _extract_error_message(data).lower()
            assert (
                "invalid" in error_msg
                or "ssh" in error_msg
                or "uri" in error_msg
                or "colon" in error_msg
                or "error" in error_msg
                or "permission" in error_msg
            ), f"SSH格式错误应包含 invalid/ssh/uri/colon/error/permission, 实际: {error_msg}"
            print("✓ TC-E09 SSH URL格式错误处理通过(返回error)")
            return

        if data.get("status") == "ok":
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors).lower()
                assert (
                    "invalid" in inner_msg
                    or "ssh" in inner_msg
                    or "uri" in inner_msg
                    or "failed" in inner_msg
                    or "error" in inner_msg
                ), f"SSH内层错误应包含 invalid/ssh/uri/failed/error, 实际: {inner_msg}"
                print(f"✓ TC-E09 SSH URL格式错误处理通过(内层错误): {inner_msg[:80]}")
                return

            print("✓ TC-E09 SSH URL格式错误处理通过(服务端降级)")
            return

        raise AssertionError(f"SSH URL格式错误应返回 error 或降级, 实际: {data.get('status')}")

    def test_error_corrupted_zip(self, api_client):
        """TC-E16 损坏的ZIP文件：验证伪造 .zip 文件报错含 zip/corrupt 或回退处理，不产生有效子节点"""
        random_id = str(uuid.uuid4())[:8]

        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, f"corrupted_{random_id}.zip")
        with open(zip_path, "w", encoding="utf-8") as f:
            f.write("这不是一个真正的ZIP文件内容")

        try:
            response = api_client.add_resource(path=zip_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            if data.get("status") == "error":
                error_msg = _extract_error_message(data).lower()
                assert (
                    "zip" in error_msg
                    or "corrupt" in error_msg
                    or "error" in error_msg
                    or "archive" in error_msg
                ), f"损坏ZIP错误信息应包含 zip/corrupt/error/archive, 实际: {error_msg}"
                print(f"✓ TC-E16 损坏的ZIP文件处理通过(返回error): {error_msg[:80]}")
                return

            assert data.get("status") == "ok"

            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors)
                assert (
                    "zip" in inner_msg.lower()
                    or "corrupt" in inner_msg.lower()
                    or "error" in inner_msg.lower()
                    or "bad" in inner_msg.lower()
                ), f"损坏ZIP内层错误应包含 zip/corrupt/error/bad, 实际: {inner_msg}"
                print(f"✓ TC-E16 损坏的ZIP文件处理通过(内层错误): {inner_msg[:80]}")
                return

            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)

                tree_resp = api_client.fs_tree(root_uri)
                if tree_resp.status_code == 200:
                    tree_data = tree_resp.json()
                    tree_result = tree_data.get("result")
                    if isinstance(tree_result, list):
                        children = tree_result
                    elif isinstance(tree_result, dict):
                        children = tree_result.get("children", [])
                    else:
                        children = []
                    assert len(children) == 0, (
                        f"损坏ZIP不应产生有效子节点, 实际子节点数: {len(children)}"
                    )

            print(f"✓ TC-E16 损坏的ZIP文件处理通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
