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
            logger.info("OpenVikingFUSE initialized")
            logger.info("=" * 60)

            if not mount._initialized and mount.config.auto_init:
                mount.initialize()

            self.original_files_dir = mount.config.openviking_data_path / ".original_files"
            self.original_files_dir.mkdir(exist_ok=True)
            self._temp_files: Dict[str, bytes] = {}
            self._file_handles: Dict[int, Dict[str, Any]] = {}
            self._next_handle = 1

        def _should_ignore_file(self, path: str) -> bool:
            path_name = Path(path).name
            return path_name.startswith("._") or path_name == ".DS_Store"

        def _log_call(self, method: str, *args, **kwargs):
            logger.debug(f"[FUSE] {method}(args={args}, kwargs={kwargs})")

        def _path_to_uri(self, path: str) -> str:
            if path == "/":
                return self.mount._get_scope_root_uri()

            path = path.lstrip("/")
            scope_root = self.mount._get_scope_root_uri()
            return f"{scope_root}/{path}"

        def _is_pdf_resource(self, name: str) -> bool:
            return name.endswith(".pdf")

        def _get_pdf_dir_name(self, pdf_name: str) -> str:
            if pdf_name.endswith(".pdf"):
                return pdf_name[:-4]
            return pdf_name

        def _has_original_pdf(self, pdf_dir_name: str) -> Path | None:
            pdf_path = self.original_files_dir / f"{pdf_dir_name}.pdf"
            if pdf_path.exists():
                return pdf_path
            return None

        def getattr(self, path: str, fh: int = None) -> Dict[str, Any]:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring file: {path}")
                raise FuseOSError(errno.ENOENT)
            self._log_call("getattr", path, fh=fh)

            now = datetime.now().timestamp()

            if path == "/":
                result = {
                    "st_mode": stat.S_IFDIR | 0o755,
                    "st_nlink": 2,
                    "st_uid": os.getuid(),
                    "st_gid": os.getgid(),
                    "st_size": 4096,
                    "st_atime": now,
                    "st_mtime": now,
                    "st_ctime": now,
                }
                logger.debug(f"[FUSE] getattr result (root): {result}")
                return result

            path_name = Path(path).name

            if path in self._temp_files:
                result = {
                    "st_mode": stat.S_IFREG | 0o644,
                    "st_nlink": 1,
                    "st_uid": os.getuid(),
                    "st_gid": os.getgid(),
                    "st_size": len(self._temp_files[path]),
                    "st_atime": now,
                    "st_mtime": now,
                    "st_ctime": now,
                }
                logger.debug(f"[FUSE] getattr result (temp file): {result}")
                return result

            if self._is_pdf_resource(path_name):
                pdf_dir_name = self._get_pdf_dir_name(path_name)
                original_pdf = self._has_original_pdf(pdf_dir_name)
                if original_pdf:
                    stat_info = original_pdf.stat()
                    result = {
                        "st_mode": stat.S_IFREG | 0o644,
                        "st_nlink": 1,
                        "st_uid": os.getuid(),
                        "st_gid": os.getgid(),
                        "st_size": stat_info.st_size,
                        "st_atime": stat_info.st_atime,
                        "st_mtime": stat_info.st_mtime,
                        "st_ctime": stat_info.st_ctime,
                    }
                    logger.debug(f"[FUSE] getattr result (PDF): {result}")
                    return result

            try:
                parent_path = str(Path(path).parent) if Path(path).parent != Path(".") else "/"
                parent_uri = self._path_to_uri(parent_path)
                name = Path(path).name

                items = self.mount._client.ls(parent_uri)

                for item in items:
                    if isinstance(item, dict):
                        item_name = item.get("name", "")
                        is_dir = item.get("isDir", False)
                        size = item.get("size", 0)
                    else:
                        item_name = str(item)
                        is_dir = False
                        size = 0

                    if item_name == name:
                        mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644
                        result = {
                            "st_mode": mode,
                            "st_nlink": 1,
                            "st_uid": os.getuid(),
                            "st_gid": os.getgid(),
                            "st_size": size,
                            "st_atime": now,
                            "st_mtime": now,
                            "st_ctime": now,
                        }
                        logger.debug(f"[FUSE] getattr result (OpenViking): {result}")
                        return result
            except Exception as e:
                logger.warning(f"getattr error for {path}: {e}")

            logger.debug(f"[FUSE] getattr failed: ENOENT")
            raise FuseOSError(errno.ENOENT)

        def readdir(self, path: str, fh: int) -> list:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring file in readdir: {path}")
                pass
            self._log_call("readdir", path, fh)

            try:
                uri = self._path_to_uri(path)
                logger.debug(f"[FUSE] Listing directory URI: {uri}")

                items = self.mount._client.ls(uri)
                entries = [".", ".."]

                for item in items:
                    if isinstance(item, dict):
                        name = item.get("name", "")
                        is_dir = item.get("isDir", False)
                    else:
                        name = str(item)
                        is_dir = False

                    if name and not name.startswith("."):
                        if is_dir:
                            original_pdf = self._has_original_pdf(name)
                            if original_pdf:
                                entries.append(f"{name}.pdf")
                            else:
                                entries.append(name)
                        else:
                            entries.append(name)

                logger.debug(f"[FUSE] readdir result: {entries}")
                return entries
            except Exception as e:
                logger.warning(f"readdir error: {e}")
                raise FuseOSError(errno.EIO)

        def open(self, path: str, flags: int) -> int:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring open: {path}")
                raise FuseOSError(errno.ENOENT)
            self._log_call("open", path, flags)

            if (flags & os.O_WRONLY or flags & os.O_RDWR) and self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            fh = self._next_handle
            self._next_handle += 1
            self._file_handles[fh] = {"path": path, "flags": flags}

            logger.debug(f"[FUSE] open returned fh={fh}")
            return fh

        def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
            self._log_call("read", path, size, offset, fh)

            if path in self._temp_files:
                data = self._temp_files[path]
                result = data[offset : offset + size]
                logger.debug(f"[FUSE] read (temp) returned {len(result)} bytes")
                return result

            path_name = Path(path).name

            if self._is_pdf_resource(path_name):
                pdf_dir_name = self._get_pdf_dir_name(path_name)
                original_pdf = self._has_original_pdf(pdf_dir_name)
                if original_pdf:
                    try:
                        with open(original_pdf, "rb") as f:
                            f.seek(offset)
                            result = f.read(size)
                            logger.debug(f"[FUSE] read (PDF) returned {len(result)} bytes")
                            return result
                    except Exception as e:
                        logger.error(f"read original PDF error: {e}")
                        raise FuseOSError(errno.EIO)

            try:
                uri = self._path_to_uri(path)
                logger.debug(f"[FUSE] Reading file URI: {uri}")
                content = self.mount._client.read(uri)
                content_bytes = content.encode("utf-8")
                result = content_bytes[offset : offset + size]
                logger.debug(f"[FUSE] read (OpenViking) returned {len(result)} bytes")
                return result
            except Exception as e:
                logger.error(f"read error: {e}")
                raise FuseOSError(errno.EIO)

        def create(self, path: str, mode: int, device: int = None) -> int:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring create: {path}")
                raise FuseOSError(errno.ENOENT)
            self._log_call("create", path, mode, device)

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            self._temp_files[path] = b""
            logger.debug(f"[FUSE] Created temp file: {path}")

            fh = self._next_handle
            self._next_handle += 1
            self._file_handles[fh] = {"path": path, "flags": os.O_WRONLY}

            logger.debug(f"[FUSE] create returned fh={fh}")
            return fh

        def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
            if self._should_ignore_file(path):
                logger.debug(f"[FUSE] Ignoring write: {path}")
                return 0
            self._log_call("write", path, f"[{len(data)} bytes]", offset, fh)

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            if path not in self._temp_files:
                self._temp_files[path] = b""

            current_data = self._temp_files[path]
            if offset > len(current_data):
                current_data += b"\x00" * (offset - len(current_data))

            new_data = current_data[:offset] + data + current_data[offset + len(data) :]
            self._temp_files[path] = new_data

            logger.debug(f"[FUSE] write done, new size={len(new_data)}")
            return len(data)

        def truncate(self, path: str, length: int, fh: int = None) -> None:
            self._log_call("truncate", path, length, fh)

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            if path in self._temp_files:
                current_data = self._temp_files[path]
                if length < len(current_data):
                    self._temp_files[path] = current_data[:length]
                elif length > len(current_data):
                    self._temp_files[path] = current_data + b"\x00" * (length - len(current_data))
                logger.debug(f"[FUSE] truncate done, size={len(self._temp_files[path])}")

        def flush(self, path: str, fh: int) -> None:
            self._log_call("flush", path, fh)

        def fsync(self, path: str, datasync: int, fh: int) -> None:
            self._log_call("fsync", path, datasync, fh)

        def release(self, path: str, fh: int) -> None:
            self._log_call("release", path, fh)

            if fh in self._file_handles:
                del self._file_handles[fh]

            if path in self._temp_files:
                data = self._temp_files[path]
                del self._temp_files[path]

                path_name = Path(path).name
                if path_name.lower().endswith(".pdf") and len(data) > 0:
                    logger.info(f"[FUSE] Processing PDF upload: {path} ({len(data)} bytes)")
                    self._handle_pdf_upload(path, data)

        def _handle_pdf_upload(self, path: str, data: bytes) -> None:
            logger.info(f"Processing PDF upload: {path}")

            try:
                pdf_dir_name = Path(path).stem.replace(" ", "_")

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    temp_path = Path(f.name)
                    f.write(data)

                try:
                    original_pdf_storage = (
                        self.mount.config.openviking_data_path / ".original_files"
                    )
                    original_pdf_storage.mkdir(exist_ok=True)
                    original_pdf_path = original_pdf_storage / f"{pdf_dir_name}.pdf"

                    shutil.copy2(temp_path, original_pdf_path)
                    logger.info(f"Saved original PDF to: {original_pdf_path}")

                    self.mount.add_resource(temp_path)
                    logger.info(f"Added PDF to OpenViking: {path}")

                finally:
                    temp_path.unlink(missing_ok=True)

            except Exception as e:
                logger.error(f"Failed to process PDF upload: {e}")
                import traceback

                traceback.print_exc()

        def unlink(self, path: str) -> None:
            self._log_call("unlink", path)

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            path_name = Path(path).name

            if self._is_pdf_resource(path_name):
                pdf_dir_name = self._get_pdf_dir_name(path_name)
                original_pdf = self._has_original_pdf(pdf_dir_name)
                if original_pdf:
                    try:
                        original_pdf.unlink()
                        logger.info(f"Deleted original PDF: {original_pdf}")

                        uri = self._path_to_uri(f"/{pdf_dir_name}")
                        self.mount._client.rm(uri, recursive=True)
                        logger.info(f"Removed from OpenViking: {uri}")
                        return
                    except Exception as e:
                        logger.error(f"Failed to delete: {e}")

            raise FuseOSError(errno.ENOENT)

        def mkdir(self, path: str, mode: int) -> None:
            self._log_call("mkdir", path, mode)
            raise FuseOSError(errno.EROFS)

        def rmdir(self, path: str) -> None:
            self._log_call("rmdir", path)
            raise FuseOSError(errno.EROFS)

        def rename(self, old: str, new: str) -> None:
            self._log_call("rename", old, new)
            raise FuseOSError(errno.EROFS)

        def chmod(self, path: str, mode: int) -> None:
            self._log_call("chmod", path, mode)

        def chown(self, path: str, uid: int, gid: int) -> None:
            self._log_call("chown", path, uid, gid)

        def utimens(self, path: str, times: tuple = None) -> None:
            self._log_call("utimens", path, times)

        def statfs(self, path: str) -> Dict[str, Any]:
            self._log_call("statfs", path)
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

    logger.info(f"Mounting OpenViking FUSE at: {config.mount_point}")
    logger.info(f"  Scope: {config.scope.value}")
    logger.info(f"  Read-only: {config.read_only}")
    logger.info(f"  Press Ctrl+C to unmount")

    try:
        FUSE(
            OpenVikingFUSE(mount),
            str(config.mount_point),
            foreground=foreground,
            nothreads=True,
            allow_other=False,
            allow_root=False,
            default_permissions=True,
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
