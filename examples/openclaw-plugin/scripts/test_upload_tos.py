#!/usr/bin/env python3

import importlib.util
import pathlib
import re

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MODULE_PATH = SCRIPT_DIR / "upload_tos.py"


def load_module():
    spec = importlib.util.spec_from_file_location("upload_tos", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bucket_specs_cover_all_regions():
    module = load_module()
    assert re.match(r"^\d{4}\.\d{1,2}\.\d{1,2}$", module.default_release_dir())
    assert [spec.bucket for spec in module.BUCKET_SPECS] == [
        "arkclaw-ov-cn-beijing",
        "arkclaw-ov-cn-guangzhou",
        "arkclaw-ov-cn-shanghai",
        "arkclaw-ov",
    ]
    assert [spec.region for spec in module.BUCKET_SPECS] == [
        "cn-beijing",
        "cn-guangzhou",
        "cn-shanghai",
        "cn-beijing",
    ]


def test_object_keys_and_public_urls():
    module = load_module()
    spec = module.BucketSpec("arkclaw-ov-cn-guangzhou", "cn-guangzhou")
    assert module.release_paths("2026.6.3", publish_latest=False) == ["2026.6.3"]
    assert module.release_paths("2026.6.3", publish_latest=True) == ["2026.6.3", "latest"]
    assert module.object_key("2026.6.3", "install.sh") == "2026.6.3/install.sh"
    assert module.object_key("latest", "openviking.tgz") == "latest/openviking.tgz"
    assert module.object_key("latest", "manifest.json") == "latest/manifest.json"
    assert re.match(
        r"^https://arkclaw-ov-cn-guangzhou\.tos-cn-guangzhou\.ivolces\.com/\d{4}\.\d{1,2}\.\d{1,2}/install\.sh$",
        module.public_url(spec, "install.sh"),
    )
    assert module.public_url(spec, "install.sh", release_path="2026.6.3") == (
        "https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.ivolces.com/2026.6.3/install.sh"
    )
    assert module.public_url(spec, "install.sh", release_path="latest", internal=False) == (
        "https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/latest/install.sh"
    )


def test_release_dir_can_be_specified_and_is_not_hardcoded():
    module = load_module()
    custom_release_dir = "2026.7.15"

    assert module.release_paths(custom_release_dir, publish_latest=False) == [custom_release_dir]
    assert module.release_paths(custom_release_dir, publish_latest=True) == [
        custom_release_dir,
        "latest",
    ]
    assert module.object_key(custom_release_dir, "manifest.json") == "2026.7.15/manifest.json"


def test_upload_sets_public_read_acl_and_content_types(tmp_path):
    module = load_module()
    install = tmp_path / "install.sh"
    tgz = tmp_path / "openviking.tgz"
    manifest = tmp_path / "manifest.json"
    install.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    tgz.write_bytes(b"tgz")
    manifest.write_text('{"schemaVersion":"1.0"}\n', encoding="utf-8")

    calls = []

    class FakeClient:
        def put_object_from_file(self, **kwargs):
            calls.append(kwargs)

        def put_object_acl(self, **kwargs):
            calls.append(
                {"acl_bucket": kwargs["bucket"], "acl_key": kwargs["key"], "acl": kwargs["acl"]}
            )

        def head_object(self, bucket, key):
            calls.append({"head_bucket": bucket, "head_key": key})

    module.upload_files(
        FakeClient(),
        module.BucketSpec("arkclaw-ov-cn-beijing", "cn-beijing"),
        [install, tgz, manifest],
        ["2026.6.3"],
        dry_run=False,
    )

    uploads = [call for call in calls if "file_path" in call]
    assert uploads[0]["bucket"] == "arkclaw-ov-cn-beijing"
    assert uploads[0]["key"] == "2026.6.3/install.sh"
    assert uploads[0]["acl"] == module.tos.ACLType.ACL_Public_Read
    assert uploads[0]["content_type"] == "text/x-shellscript; charset=utf-8"
    assert uploads[1]["key"] == "2026.6.3/openviking.tgz"
    assert uploads[1]["acl"] == module.tos.ACLType.ACL_Public_Read
    assert uploads[1]["content_type"] == "application/gzip"
    assert uploads[2]["key"] == "2026.6.3/manifest.json"
    assert uploads[2]["acl"] == module.tos.ACLType.ACL_Public_Read
    assert uploads[2]["content_type"] == "application/json; charset=utf-8"
    acl_calls = [call for call in calls if "acl_key" in call]
    assert [call["acl_key"] for call in acl_calls] == [
        "2026.6.3/install.sh",
        "2026.6.3/openviking.tgz",
        "2026.6.3/manifest.json",
    ]


def test_ensure_bucket_creates_missing_bucket():
    module = load_module()
    calls = []

    class MissingBucketError(Exception):
        status_code = 404

    class FakeClient:
        def head_bucket(self, bucket):
            calls.append(("head_bucket", bucket))
            raise MissingBucketError("missing bucket")

        def create_bucket(self, **kwargs):
            calls.append(("create_bucket", kwargs))

    spec = module.BucketSpec("arkclaw-ov-cn-guangzhou", "cn-guangzhou")
    module.ensure_bucket(FakeClient(), spec, dry_run=False)

    assert calls == [
        ("head_bucket", "arkclaw-ov-cn-guangzhou"),
        ("create_bucket", {"bucket": "arkclaw-ov-cn-guangzhou"}),
    ]


def test_ensure_bucket_dry_run_does_not_create_bucket():
    module = load_module()

    class FakeClient:
        def head_bucket(self, bucket):
            raise AssertionError("dry-run must not call TOS")

        def create_bucket(self, **kwargs):
            raise AssertionError("dry-run must not call TOS")

    module.ensure_bucket(FakeClient(), module.BucketSpec("arkclaw-ov", "cn-beijing"), dry_run=True)


def test_publish_latest_requires_explicit_flag(tmp_path):
    module = load_module()
    install = tmp_path / "install.sh"
    tgz = tmp_path / "openviking.tgz"
    manifest = tmp_path / "manifest.json"
    install.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    tgz.write_bytes(b"tgz")
    manifest.write_text('{"schemaVersion":"1.0"}\n', encoding="utf-8")

    calls = []

    class FakeClient:
        def put_object_from_file(self, **kwargs):
            calls.append(kwargs)

        def put_object_acl(self, **kwargs):
            calls.append({"acl_key": kwargs["key"]})

        def head_object(self, bucket, key):
            calls.append({"head_key": key})

    module.upload_files(
        FakeClient(),
        module.BucketSpec("arkclaw-ov-cn-beijing", "cn-beijing"),
        [install, tgz, manifest],
        module.release_paths("2026.6.3", publish_latest=True),
        dry_run=False,
    )

    uploads = [call for call in calls if "file_path" in call]
    assert [upload["key"] for upload in uploads] == [
        "2026.6.3/install.sh",
        "2026.6.3/openviking.tgz",
        "2026.6.3/manifest.json",
        "latest/install.sh",
        "latest/openviking.tgz",
        "latest/manifest.json",
    ]


def test_install_script_supports_manifest_url_and_regional_manifest_download():
    install_script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert "--manifest-url <url>" in install_script
    assert "manifest_url()" in install_script
    assert "download_manifest" in install_script
    assert "manifest_url=$(manifest_url)" in install_script
    assert "printf '%s/%s/manifest.json'" in install_script


if __name__ == "__main__":
    test_bucket_specs_cover_all_regions()
    test_object_keys_and_public_urls()
    test_release_dir_can_be_specified_and_is_not_hardcoded()
    test_ensure_bucket_creates_missing_bucket()
    test_ensure_bucket_dry_run_does_not_create_bucket()
    import tempfile

    class TmpPath:
        def __enter__(self):
            self._tmp = tempfile.TemporaryDirectory()
            return pathlib.Path(self._tmp.name)

        def __exit__(self, exc_type, exc, tb):
            self._tmp.cleanup()

    with TmpPath() as tmp_path:
        test_upload_sets_public_read_acl_and_content_types(tmp_path)
    with TmpPath() as tmp_path:
        test_publish_latest_requires_explicit_flag(tmp_path)
    test_install_script_supports_manifest_url_and_regional_manifest_download()
    print("upload_tos.py tests passed")
