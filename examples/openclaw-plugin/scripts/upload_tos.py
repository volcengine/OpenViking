#!/usr/bin/env python3

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import tempfile
from typing import NamedTuple

import tos

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_INSTALL_SH = SCRIPT_DIR / "install.sh"
DEFAULT_TGZ = SCRIPT_DIR / "openviking.tgz"
DEFAULT_MANIFEST = SCRIPT_DIR / "manifest.json"


class BucketSpec(NamedTuple):
    bucket: str
    region: str


BUCKET_SPECS = [
    BucketSpec("arkclaw-ov-cn-beijing", "cn-beijing"),
    BucketSpec("arkclaw-ov-cn-guangzhou", "cn-guangzhou"),
    BucketSpec("arkclaw-ov-cn-shanghai", "cn-shanghai"),
    BucketSpec("arkclaw-ov", "cn-beijing"),
]


def default_release_dir(today: datetime.date | None = None) -> str:
    today = today or datetime.date.today()
    return f"{today.year}.{today.month}.{today.day}"


def object_key(release_path: str, file_name: str) -> str:
    return f"{release_path}/{file_name}"


def release_paths(release_dir: str, publish_latest: bool = False) -> list[str]:
    paths = [release_dir]
    if publish_latest:
        paths.append("latest")
    return paths


def endpoint(region: str) -> str:
    return f"https://tos-{region}.volces.com"


def public_url(
    spec: BucketSpec, file_name: str, release_path: str | None = None, internal: bool = True
) -> str:
    suffix = "ivolces.com" if internal else "volces.com"
    release_path = release_path or default_release_dir()
    return f"https://{spec.bucket}.tos-{spec.region}.{suffix}/{object_key(release_path, file_name)}"


def bucket_base_url(spec: BucketSpec, internal: bool = True) -> str:
    suffix = "ivolces.com" if internal else "volces.com"
    return f"https://{spec.bucket}.tos-{spec.region}.{suffix}"


def content_type(path: pathlib.Path) -> str:
    if path.name == "install.sh":
        return "text/x-shellscript; charset=utf-8"
    if path.name.endswith(".tgz"):
        return "application/gzip"
    if path.name == "manifest.json" or path.suffix == ".json":
        return "application/json; charset=utf-8"
    return "application/octet-stream"


def require_file(path: pathlib.Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"required file is missing: {path}")


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_client(spec: BucketSpec):
    ak = os.environ.get("TEAM_TEST_AK")
    sk = os.environ.get("TEAM_TEST_SK")
    if not ak or not sk:
        raise RuntimeError("TEAM_TEST_AK and TEAM_TEST_SK must be configured in the environment")
    return tos.TosClientV2(ak=ak, sk=sk, endpoint=endpoint(spec.region), region=spec.region)


def is_not_found_error(err: Exception) -> bool:
    status = (
        getattr(err, "status_code", None)
        or getattr(err, "status", None)
        or getattr(err, "code", None)
    )
    message = str(getattr(err, "message", "") or err).lower()
    code = str(getattr(err, "code", "")).lower()
    return status == 404 or "notfound" in code or "not found" in message or "nosuchbucket" in code


def ensure_bucket(client, spec: BucketSpec, dry_run: bool = False) -> None:
    if dry_run:
        print(f"DRY RUN ensure bucket tos://{spec.bucket} region={spec.region}")
        return

    try:
        client.head_bucket(spec.bucket)
        return
    except Exception as err:
        if not is_not_found_error(err):
            raise

    client.create_bucket(bucket=spec.bucket)


def upload_one(
    client, spec: BucketSpec, release_path: str, path: pathlib.Path, dry_run: bool = False
) -> None:
    key = object_key(release_path, path.name)
    if dry_run:
        print(f"DRY RUN upload {path} -> tos://{spec.bucket}/{key} overwrite=true acl=public-read")
        return

    client.put_object_from_file(
        bucket=spec.bucket,
        key=key,
        file_path=str(path),
        acl=tos.ACLType.ACL_Public_Read,
        content_type=content_type(path),
        forbid_overwrite=False,
    )
    client.put_object_acl(bucket=spec.bucket, key=key, acl=tos.ACLType.ACL_Public_Read)
    client.head_object(bucket=spec.bucket, key=key)


def stamp_bucket_installer(
    install_sh: pathlib.Path, spec: BucketSpec, release_path: str, output_dir: pathlib.Path
) -> pathlib.Path:
    text = install_sh.read_text(encoding="utf-8")
    text = re.sub(
        r'^DEFAULT_TOS_BASE_URL=".*"$',
        f'DEFAULT_TOS_BASE_URL="{bucket_base_url(spec)}"',
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r'^RELEASE_PATH="\$\{INSTALL_RELEASE_PATH:-.*\}"$',
        f'RELEASE_PATH="${{INSTALL_RELEASE_PATH:-{release_path}}}"',
        text,
        flags=re.MULTILINE,
    )
    stamped = output_dir / "install.sh"
    stamped.write_text(text, encoding="utf-8")
    stamped.chmod(0o755)
    return stamped


def stamp_bucket_manifest(
    manifest: pathlib.Path,
    spec: BucketSpec,
    release_path: str,
    stamped_install: pathlib.Path,
    output_dir: pathlib.Path,
) -> pathlib.Path:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["tos"] = {
        "bucket": spec.bucket,
        "region": spec.region,
        "endpoint": f"tos-{spec.region}.volces.com",
    }
    for artifact in data.get("artifacts", []):
        if artifact.get("name") == "install.sh":
            artifact["path"] = object_key(release_path, "install.sh")
            artifact["size"] = stamped_install.stat().st_size
            artifact["sha256"] = file_sha256(stamped_install)
        elif artifact.get("path"):
            artifact["path"] = object_key(
                release_path, pathlib.PurePosixPath(artifact["path"]).name
            )
    stamped = output_dir / "manifest.json"
    stamped.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stamped


def upload_bucket_installer(
    client,
    spec: BucketSpec,
    release_path: str,
    install_sh: pathlib.Path,
    output_dir: pathlib.Path,
    dry_run: bool = False,
) -> pathlib.Path:
    stamped = stamp_bucket_installer(install_sh, spec, release_path, output_dir)
    upload_one(client, spec, release_path, stamped, dry_run=dry_run)
    return stamped


def upload_files(
    client, spec: BucketSpec, artifacts: list[pathlib.Path], paths: list[str], dry_run: bool = False
) -> None:
    install_sh = next((artifact for artifact in artifacts if artifact.name == "install.sh"), None)
    manifest = next((artifact for artifact in artifacts if artifact.name == "manifest.json"), None)
    for release_path in paths:
        with tempfile.TemporaryDirectory(prefix="openviking-installer-") as tmp:
            tmp_dir = pathlib.Path(tmp)
            stamped_install = (
                upload_bucket_installer(
                    client, spec, release_path, install_sh, tmp_dir, dry_run=dry_run
                )
                if install_sh
                else None
            )
            stamped_manifest = (
                stamp_bucket_manifest(manifest, spec, release_path, stamped_install, tmp_dir)
                if manifest and stamped_install
                else None
            )
            for artifact in artifacts:
                if artifact.name == "install.sh":
                    continue
                if artifact.name == "manifest.json" and stamped_manifest:
                    upload_one(client, spec, release_path, stamped_manifest, dry_run=dry_run)
                else:
                    upload_one(client, spec, release_path, artifact, dry_run=dry_run)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Upload OpenViking install.sh, openviking.tgz, and manifest.json to all TOS buckets."
    )
    parser.add_argument("--install-sh", type=pathlib.Path, default=DEFAULT_INSTALL_SH)
    parser.add_argument("--tgz", type=pathlib.Path, default=DEFAULT_TGZ)
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--release-dir",
        default="",
        help="Date release directory, for example 2026.6.3. Defaults to today's yyyy.m.d.",
    )
    parser.add_argument(
        "--publish-latest",
        action="store_true",
        help="Also upload the same files to latest/. Use only after the dated release has been validated as stable.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    install_sh = args.install_sh.resolve()
    tgz = args.tgz.resolve()
    manifest = args.manifest.resolve()
    require_file(install_sh)
    require_file(tgz)
    require_file(manifest)

    artifacts = [install_sh, tgz, manifest]
    release_dir = args.release_dir or default_release_dir()
    paths = release_paths(release_dir, publish_latest=args.publish_latest)

    for spec in BUCKET_SPECS:
        print(f"Uploading to bucket={spec.bucket} region={spec.region}")
        client = None if args.dry_run else make_client(spec)
        ensure_bucket(client, spec, dry_run=args.dry_run)
        upload_files(client, spec, artifacts, paths, dry_run=args.dry_run)
        for release_path in paths:
            print(
                f"  {release_path}/install.sh:    {public_url(spec, 'install.sh', release_path=release_path)}"
            )
            print(
                f"  {release_path}/openviking.tgz: {public_url(spec, 'openviking.tgz', release_path=release_path)}"
            )
            print(
                f"  {release_path}/manifest.json:  {public_url(spec, 'manifest.json', release_path=release_path)}"
            )

    if not args.publish_latest:
        print(
            "latest/ was not updated. Re-run with --publish-latest only after the dated release is stable."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
