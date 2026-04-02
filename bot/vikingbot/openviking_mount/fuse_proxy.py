#!/usr/bin/env python3
from __future__ import annotations

import sys
import os
import stat
import errno
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict

from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from loguru import logger

from .mount import OpenVikingMount, MountConfig

try:
    from fuse import FUSE, FuseOSError, Operations

    FUSE_AVAILABLE = True
except (ImportError, OSError) as e:
    FUSE_AVAILABLE = False
    logger.warning(f"fusepy not available: {e}")
    Operations = object
    FUSE = None
    FuseOSError = Exception


if FUSE_AVAILABLE:

    class OpenVikingFUSE(Operations):
        def __init__(self, mount: OpenVikingMount):
            self.mount = mount
            logger.info("=" * 60)
            logger.info("OpenViking FUSE Proxy initialized")
            logger.info("=" * 60)

            if not mount._initialized and mount.config.auto_init:
                mount.initialize()

            self.original_files_dir = mount.config.openviking_data_path / ".original_files"
            self.original_files_dir.mkdir(exist_ok=True)

            self._pending_uploads: Dict[str, bytes] = {}

        def _should_ignore_file(self, path: str) -> bool:
            path_name = Path(path).name
            return path_name.startswith("._") or path_name == ".DS_Store"

        def _get_original_path(self, path: str) -> Path:
            if path == "/":
                return self.original_files_dir
            return self.original_files_dir / path.lstrip("/")

        def getattr(self, path: str, fh: int = None) -> Dict[str, Any]:
            logger.debug(f"[FUSE] getattr: {path}")
            print(f"path={path}", file=sys.stderr)
            orig_path = self._get_original_path(path)
            print(f"orig_path={orig_path}", file=sys.stderr)
            if orig_path.exists():
                stat_info = orig_path.stat()
                return {
                    "st_mode": stat_info.st_mode,
                    "st_nlink": stat_info.st_nlink,
                    "st_uid": stat_info.st_uid,
                    "st_gid": stat_info.st_gid,
                    "st_size": stat_info.st_size,
                    "st_atime": stat_info.st_atime,
                    "st_mtime": stat_info.st_mtime,
                    "st_ctime": stat_info.st_ctime,
                }
            print(f"2222222")
            if path in self._pending_uploads:
                now = datetime.now().timestamp()
                return {
                    "st_mode": stat.S_IFREG | 0o644,
                    "st_nlink": 1,
                    "st_uid": os.getuid(),
                    "st_gid": os.getgid(),
                    "st_size": len(self._pending_uploads[path]),
                    "st_atime": now,
                    "st_mtime": now,
                    "st_ctime": now,
                }

            raise FuseOSError(errno.ENOENT)

        def readdir(self, path: str, fh: int) -> list:
            logger.debug(f"[FUSE] readdir: {path}")

            orig_path = self._get_original_path(path)

            if not orig_path.is_dir():
                raise FuseOSError(errno.ENOENT)

            entries = [".", ".."]

            for item in orig_path.iterdir():
                if item.name and not item.name.startswith("."):
                    entries.append(item.name)

            return entries

        def open(self, path: str, flags: int) -> int:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring open: {path}")
                raise FuseOSError(errno.ENOENT)

            logger.debug(f"[FUSE] open: {path} (flags={flags})")
            return 0

        def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
            logger.debug(f"[FUSE] read: {path} (size={size}, offset={offset})")

            if path in self._pending_uploads:
                data = self._pending_uploads[path]
                return data[offset : offset + size]

            orig_path = self._get_original_path(path)

            if not orig_path.exists():
                raise FuseOSError(errno.ENOENT)

            with open(orig_path, "rb") as f:
                f.seek(offset)
                return f.read(size)

        def create(self, path: str, mode: int, device: int = None) -> int:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring create: {path}")
                raise FuseOSError(errno.ENOENT)

            logger.debug(f"[FUSE] create: {path} (mode={mode})")

            self._pending_uploads[path] = b""
            logger.debug(f"[FUSE] Created pending upload: {path}")
            return 0

        def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring write: {path}")
                return 0

            logger.debug(f"[FUSE] write: {path} (offset={offset}, size={len(data)})")

            if path not in self._pending_uploads:
                self._pending_uploads[path] = b""

            current_data = self._pending_uploads[path]
            if offset > len(current_data):
                current_data += b"\x00" * (offset - len(current_data))

            new_data = current_data[:offset] + data + current_data[offset + len(data) :]
            self._pending_uploads[path] = new_data

            logger.debug(f"[FUSE] write done, new size={len(new_data)}")
            return len(data)

        def truncate(self, path: str, length: int, fh: int = None) -> None:
            logger.debug(f"[FUSE] truncate: {path} (length={length})")

            if path in self._pending_uploads:
                current_data = self._pending_uploads[path]
                if length < len(current_data):
                    self._pending_uploads[path] = current_data[:length]
                elif length > len(current_data):
                    self._pending_uploads[path] = current_data + b"\x00" * (
                        length - len(current_data)
                    )

        def flush(self, path: str, fh: int) -> None:
            logger.debug(f"[FUSE] flush: {path}")

        def fsync(self, path: str, datasync: int, fh: int) -> None:
            logger.debug(f"[FUSE] fsync: {path} (datasync={datasync})")

        def release(self, path: str, fh: int) -> None:
            logger.debug(f"[FUSE] release: {path}")

            if path in self._pending_uploads:
                data = self._pending_uploads[path]
                del self._pending_uploads[path]

                path_name = Path(path).name

                self._handle_upload(path, data)

        def _handle_upload(self, path: str, data: bytes) -> None:
            logger.info(f"Processing PDF upload: {path}")

            try:
                dir_name = Path(path).stem.replace(" ", "_")

                with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as f:
                    temp_path = Path(f.name)
                    f.write(data)

                try:
                    orig_path = self._get_original_path(path)
                    orig_path.parent.mkdir(parents=True, exist_ok=True)

                    shutil.copy2(temp_path, orig_path)
                    logger.info(f"Saved to original files: {orig_path}")

                    wait = not self.mount.config.async_add_resource
                    self.mount.add_resource(temp_path, wait=wait)
                    logger.info(
                        f"Added to OpenViking: {path} (async={self.mount.config.async_add_resource})"
                    )

                finally:
                    temp_path.unlink(missing_ok=True)

            except Exception as e:
                logger.error(f"Failed to process PDF upload: {e}")
                import traceback

                traceback.print_exc()

        def unlink(self, path: str) -> None:
            logger.debug(f"[FUSE] unlink: {path}")

            orig_path = self._get_original_path(path)

            if orig_path.exists():
                orig_path.unlink()
                logger.info(f"Deleted from original files: {orig_path}")

                path_name = Path(path).name

                dir_name = Path(path).stem.replace(" ", "_")
                try:
                    uri = f"viking://resources/{dir_name}"
                    self.mount._client.rm(uri, recursive=True)
                    logger.info(f"Removed from OpenViking: {uri}")
                except Exception as e:
                    logger.warning(f"Failed to remove from OpenViking: {e}")
                return

            raise FuseOSError(errno.ENOENT)

        def mkdir(self, path: str, mode: int) -> None:
            logger.debug(f"[FUSE] mkdir: {path} (mode={mode})")
            orig_path = self._get_original_path(path)
            orig_path.mkdir(parents=True, exist_ok=True)

        def rmdir(self, path: str) -> None:
            logger.debug(f"[FUSE] rmdir: {path}")
            orig_path = self._get_original_path(path)
            orig_path.rmdir()

        def rename(self, old: str, new: str) -> None:
            logger.debug(f"[FUSE] rename: {old} -> {new}")
            orig_old = self._get_original_path(old)
            orig_new = self._get_original_path(new)
            orig_old.rename(orig_new)

        def chmod(self, path: str, mode: int) -> None:
            logger.debug(f"[FUSE] chmod: {path} (mode={mode})")
            orig_path = self._get_original_path(path)
            orig_path.chmod(mode)

        def chown(self, path: str, uid: int, gid: int) -> None:
            logger.debug(f"[FUSE] chown: {path} (uid={uid}, gid={gid})")
            pass

        def utimens(self, path: str, times: tuple = None) -> None:
            logger.debug(f"[FUSE] utimens: {path} (times={times})")
            orig_path = self._get_original_path(path)
            if times:
                os.utime(orig_path, times)
            else:
                orig_path.touch()

        def statfs(self, path: str) -> Dict[str, Any]:
            logger.debug(f"[FUSE] statfs: {path}")
            return {
                "f_bsize": 4096,
                "f_frsize": 4096,
                "f_blocks": 1000000,
                "f_bfree": 500000,
                "f_bavail": 500000,
                "f_files": 100000,
                "f_ffree": 50000,
                "f_favail": 50000,
                "f_flag": 0,
                "f_namemax": 255,
            }


def mount_fuse(config: MountConfig, foreground: bool = True) -> None:
    if not FUSE_AVAILABLE:
        raise RuntimeError("fusepy is not available. Cannot mount FUSE filesystem.")

    mount = OpenVikingMount(config)

    logger.info(f"Mounting OpenViking FUSE Proxy at: {config.mount_point}")
    logger.info(f"  Proxy to: {config.openviking_data_path / '.original_files'}")
    logger.info(f"  Press Ctrl+C to unmount")

    try:
        FUSE(
            OpenVikingFUSE(mount),
            str(config.mount_point),
            foreground=foreground,
            nothreads=True,
            allow_other=True,
            allow_root=False,
            default_permissions=True,
            debug=True,
        )
    except KeyboardInterrupt:
        logger.info("Unmounted")
    except Exception as e:
        logger.error(f"FUSE mount failed: {e}")
        raise


class FUSEMountManager:
    def __init__(self):
        self._mounts: Dict[str, Any] = {}

    def mount(self, config: MountConfig) -> str:
        raise NotImplementedError("FUSEMountManager is for future use")

    def unmount(self, mount_point: Path) -> None:
        raise NotImplementedError("FUSEMountManager is for future use")
