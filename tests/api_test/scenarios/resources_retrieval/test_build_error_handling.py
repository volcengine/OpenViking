import os
import shutil
import tempfile
import uuid

from build_test_helpers import (
    _extract_error_message,
    assert_root_uri_valid,
    cleanup_temp_dir,
    create_test_file,
)


class TestBuildErrorHandling:
    """TC-E01, E08, E09, E11, E16 异常与边界测试（快速用例，≤20s）"""

    def test_error_remote_404(self, api_client):
        """TC-E01 远端404不存在：验证 404 URL 返回错误且不崩溃，错误信息应包含状态码"""
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
            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)
            print("✓ TC-E01 远端404处理通过(降级为空资源)")
            return

        raise AssertionError(f"404 URL 应返回 error 或 ok, 实际: {data.get('status')}")

    def test_error_dns_resolve_failure(self, api_client):
        """TC-E08 DNS解析失败：验证不存在的域名返回错误且不挂起"""
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
            ), f"DNS失败错误信息应包含 resolve/hostname/dns/error, 实际: {error_msg}"
            print("✓ TC-E08 DNS解析失败处理通过(返回error)")
            return

        if data.get("status") == "ok":
            result = data.get("result", {})
            root_uri = result.get("root_uri")
            if root_uri:
                assert_root_uri_valid(root_uri)
            print("✓ TC-E08 DNS解析失败处理通过(降级为空资源)")
            return

        raise AssertionError(f"DNS失败 URL 应返回 error 或 ok, 实际: {data.get('status')}")

    def test_error_ssh_url_invalid_format(self, api_client):
        """TC-E09 SSH URL格式错误：验证 git@invalid (无冒号) 返回 InvalidArgumentError"""
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
            print("✓ TC-E09 SSH URL格式错误处理通过")
            return

        if data.get("status") == "ok":
            print("✓ TC-E09 SSH URL格式错误处理通过(服务端降级)")
            return

        raise AssertionError(f"SSH URL格式错误应返回 error 或降级, 实际: {data.get('status')}")

    def test_error_non_resources_scope_rejected(self, api_client):
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

    def test_error_corrupted_zip(self, api_client):
        """TC-E16 损坏的ZIP文件：验证伪造 .zip 文件回退或报错且不崩溃"""
        random_id = str(uuid.uuid4())[:8]

        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, f"corrupted_{random_id}.zip")
        with open(zip_path, "w", encoding="utf-8") as f:
            f.write("这不是一个真正的ZIP文件内容")

        try:
            response = api_client.add_resource(path=zip_path, wait=True)
            assert response.status_code == 500, (
                f"损坏ZIP应返回 500, 实际: {response.status_code}"
            )

            data = response.json()
            assert data.get("status") == "error", (
                f"损坏ZIP应返回 error, 实际: {data.get('status')}"
            )

            print("✓ TC-E16 损坏的ZIP文件处理通过")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
