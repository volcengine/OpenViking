import os
import shutil
import tempfile
import uuid

import pytest
from build_test_helpers import (
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
)


class TestBuildMediaResourcesSlow:
    """TC-B13~B15 媒体类资源构建测试"""

    def test_build_image_svg(self, api_client):
        """TC-B13 图片构建(SVG)：验证 .svg 文件解析失败时错误信息合理，成功时 source_format=image 且可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"svg_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()
        svg_path = os.path.join(temp_dir, f"test_{random_id}.svg")

        svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/><text x="10" y="50">SVG test {unique_keyword}</text></svg>'
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_content)

        try:
            response = api_client.add_resource(path=svg_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") in ("ok", "error"), (
                f"SVG文件应返回 ok 或 error, 实际: {data.get('status')}"
            )

            if data.get("status") == "error":
                error_msg = str(data.get("error", "")).lower()
                assert (
                    "image" in error_msg
                    or "svg" in error_msg
                    or "parse" in error_msg
                    or "error" in error_msg
                ), f"SVG外层错误应包含 image/svg/parse/error, 实际: {error_msg}"
                print("✓ TC-B13 图片构建(SVG)通过(服务端返回error，SVG格式不被支持)")
                return

            result = data.get("result", {})
            if isinstance(result, dict) and result.get("status") == "error":
                inner_errors = result.get("errors", [])
                inner_msg = " ".join(str(e) for e in inner_errors)
                assert (
                    "image" in inner_msg.lower()
                    or "parse" in inner_msg.lower()
                    or "identify" in inner_msg.lower()
                    or "svg" in inner_msg.lower()
                ), f"SVG解析错误信息应包含 image/parse/identify/svg, 实际: {inner_msg}"
                print(
                    f"✓ TC-B13 图片构建(SVG)通过(服务端内层解析错误，SVG格式不被支持): {inner_msg[:80]}"
                )
                return

            root_uri = result.get("root_uri") if isinstance(result, dict) else None
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["image", "html", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B13 图片构建(SVG)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_audio_mp3(self, api_client):
        """TC-B14 音频构建(MP3)：验证 .mp3 文件添加后产物路径含 /audio/ 且 source_format=audio"""
        import subprocess
        import uuid

        random_id = str(uuid.uuid4())[:8]
        temp_dir = tempfile.mkdtemp()
        mp3_path = os.path.join(temp_dir, f"test_{random_id}.mp3")

        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-frames:a",
                    "44100",
                    "-y",
                    mp3_path,
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                pytest.skip("ffmpeg 不可用，跳过MP3音频构建测试")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("ffmpeg 不可用，跳过MP3音频构建测试")

        try:
            response = api_client.add_resource(path=mp3_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/audio/" in root_uri, f"音频 root_uri 应含 /audio/, 实际: {root_uri}"

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, "audio")

            print(f"✓ TC-B14 音频构建(MP3)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_audio_wav(self, api_client):
        """TC-B14 音频构建(WAV)：验证 .wav 文件添加后产物路径含 /audio/ 且 source_format=audio"""
        import subprocess

        random_id = str(uuid.uuid4())[:8]
        temp_dir = tempfile.mkdtemp()
        wav_path = os.path.join(temp_dir, f"test_{random_id}.wav")

        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:duration=1",
                    "-frames:a",
                    "44100",
                    "-y",
                    wav_path,
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                pytest.skip("ffmpeg 不可用，跳过WAV音频构建测试")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("ffmpeg 不可用，跳过WAV音频构建测试")

        try:
            response = api_client.add_resource(path=wav_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/audio/" in root_uri, f"音频 root_uri 应含 /audio/, 实际: {root_uri}"

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, "audio")

            print(f"✓ TC-B14 音频构建(WAV)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_image_jpg(self, api_client):
        """TC-B13 图片构建(JPG)：验证 .jpg 文件添加后产物路径含 /images/ 且 source_format=image"""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow 未安装，跳过JPG图片构建测试")

        random_id = str(uuid.uuid4())[:8]
        temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(temp_dir, f"test_{random_id}.jpg")

        img = Image.new("RGB", (100, 100), color=(73, 109, 137))
        img.save(img_path, format="JPEG")

        try:
            response = api_client.add_resource(path=img_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/images/" in root_uri, f"图片 root_uri 应含 /images/, 实际: {root_uri}"

            assert_source_format(api_client, root_uri, "image")

            print(f"✓ TC-B13 图片构建(JPG)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_image_png(self, api_client):
        """TC-B13 图片构建(PNG)：验证 .png 文件添加后产物路径含 /images/ 且 source_format=image"""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow 未安装，跳过PNG图片构建测试")

        random_id = str(uuid.uuid4())[:8]
        temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(temp_dir, f"test_{random_id}.png")

        img = Image.new("RGBA", (100, 100), color=(73, 109, 137, 255))
        img.save(img_path, format="PNG")

        try:
            response = api_client.add_resource(path=img_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/images/" in root_uri, f"图片 root_uri 应含 /images/, 实际: {root_uri}"

            assert_source_format(api_client, root_uri, "image")

            print(f"✓ TC-B13 图片构建(PNG)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_image_webp(self, api_client):
        """TC-B13 图片构建(WebP)：验证 .webp 文件添加后产物路径含 /images/ 且 source_format=image"""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow 未安装，跳过WebP图片构建测试")

        random_id = str(uuid.uuid4())[:8]
        temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(temp_dir, f"test_{random_id}.webp")

        img = Image.new("RGB", (100, 100), color=(73, 109, 137))
        img.save(img_path, format="WEBP")

        try:
            response = api_client.add_resource(path=img_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/images/" in root_uri, f"图片 root_uri 应含 /images/, 实际: {root_uri}"

            assert_source_format(api_client, root_uri, "image")

            print(f"✓ TC-B13 图片构建(WebP)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_video_mp4(self, api_client):
        """TC-B15 视频构建(MP4)：验证 .mp4 文件添加后产物路径含 /video/ 且 source_format=video"""
        random_id = str(uuid.uuid4())[:8]
        temp_dir = tempfile.mkdtemp()
        mp4_path = os.path.join(temp_dir, f"test_{random_id}.mp4")

        try:
            import subprocess

            result = subprocess.run(
                [
                    "ffmpeg",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=100x100:d=1",
                    "-frames:v",
                    "25",
                    "-y",
                    mp4_path,
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                pytest.skip("ffmpeg 不可用，跳过MP4视频构建测试")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("ffmpeg 不可用，跳过MP4视频构建测试")

        try:
            response = api_client.add_resource(path=mp4_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/video/" in root_uri, f"视频 root_uri 应含 /video/, 实际: {root_uri}"

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, "video")

            print(f"✓ TC-B15 视频构建(MP4)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
