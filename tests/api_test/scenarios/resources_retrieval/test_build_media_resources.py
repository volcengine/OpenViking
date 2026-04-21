import os
import shutil
import struct
import tempfile
import uuid
import wave

import pytest
from build_test_helpers import assert_root_uri_valid, assert_source_format


def _create_image_file(ext=".png"):
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow 未安装，跳过图片构建测试")

    random_id = str(uuid.uuid4())[:8]
    temp_dir = tempfile.mkdtemp()
    img_path = os.path.join(temp_dir, f"test_{random_id}{ext}")

    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    img.save(img_path)

    return img_path, temp_dir, random_id


def _create_audio_file(ext=".wav"):
    random_id = str(uuid.uuid4())[:8]
    temp_dir = tempfile.mkdtemp()
    audio_path = os.path.join(temp_dir, f"test_{random_id}{ext}")

    with wave.open(audio_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        frames = b""
        for _i in range(44100):
            value = int(32767 * 0.5)
            frames += struct.pack("<h", value)
        wf.writeframes(frames)

    return audio_path, temp_dir, random_id


class TestBuildMediaResources:
    """TC-B13(PNG), B14(WAV) 媒体类资源构建测试（快速用例，≤20s）"""

    def test_build_image_png(self, api_client):
        """TC-B13 图片构建(PNG)：验证 .png 文件添加后产物路径含 /images/ 且 source_format=image"""
        img_path, temp_dir, random_id = _create_image_file(".png")
        try:
            response = api_client.add_resource(path=img_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)
            assert "/images/" in root_uri, f"图片 root_uri 应含 /images/, 实际: {root_uri}"

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, "image")

            print(f"✓ TC-B13 图片构建(PNG)通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_audio_wav(self, api_client):
        """TC-B14 音频构建(WAV)：验证 .wav 文件添加后产物路径含 /audio/ 且 source_format=audio"""
        audio_path, temp_dir, random_id = _create_audio_file(".wav")
        try:
            response = api_client.add_resource(path=audio_path, wait=True)
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
