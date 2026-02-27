"""
OpenViking FUSE 文件系统

实现真正的 FUSE 文件系统挂载，允许使用标准文件系统 API（os、pathlib 等）
直接操作 OpenViking 数据。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

# 添加OpenViking项目到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from loguru import logger

from .mount import OpenVikingMount, MountConfig

# 尝试导入fusepy
try:
    from fuse import FUSE, FuseOSError, Operations

    FUSE_AVAILABLE = True
except (ImportError, OSError) as e:
    FUSE_AVAILABLE = False
    logger.warning(f"fusepy not available: {e}")
    # 创建占位符
    Operations = object
    FUSE = None
    FuseOSError = Exception


# 只有当 FUSE 可用时才定义完整的实现
if FUSE_AVAILABLE:
    import os
    import stat
    import errno
    from datetime import datetime

    class OpenVikingFUSE(Operations):
        """
        OpenViking FUSE 操作类

        实现 FUSE 文件系统操作，将 OpenViking 的虚拟文件系统
        暴露为标准的 POSIX 文件系统。
        """

        def __init__(self, mount: OpenVikingMount):
            """
            初始化 FUSE 操作

            Args:
                mount: OpenVikingMount 实例
            """
            self.mount = mount
            self._fd = 0
            self._file_handles: Dict[int, str] = {}  # fd -> uri
            self._file_contents: Dict[str, str] = {}  # uri -> content (for write cache)

            if not mount._initialized and mount.config.auto_init:
                mount.initialize()

        def _path_to_uri(self, path: str) -> str:
            """
            将 FUSE 路径转换为 OpenViking URI

            Args:
                path: FUSE 路径 (如 /resources/foo)

            Returns:
                OpenViking URI
            """
            if path == "/":
                path = ""

            path = path.lstrip("/")

            if not path:
                return self.mount._get_scope_root_uri()

            return f"viking://{path}"

        def getattr(self, path: str, fh: int = None) -> Dict[str, Any]:
            """
            获取文件/目录属性

            Args:
                path: 文件路径
                fh: 文件描述符

            Returns:
                属性字典
            """
            logger.debug(f"getattr: {path}")

            now = datetime.now().timestamp()

            if path == "/":
                return {
                    "st_mode": stat.S_IFDIR | 0o755,
                    "st_nlink": 2,
                    "st_uid": os.getuid(),
                    "st_gid": os.getgid(),
                    "st_size": 4096,
                    "st_atime": now,
                    "st_mtime": now,
                    "st_ctime": now,
                }

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
                        return {
                            "st_mode": mode,
                            "st_nlink": 1,
                            "st_uid": os.getuid(),
                            "st_gid": os.getgid(),
                            "st_size": size,
                            "st_atime": now,
                            "st_mtime": now,
                            "st_ctime": now,
                        }
            except Exception:
                pass

            return {
                "st_mode": stat.S_IFDIR | 0o755,
                "st_nlink": 2,
                "st_uid": os.getuid(),
                "st_gid": os.getgid(),
                "st_size": 4096,
                "st_atime": now,
                "st_mtime": now,
                "st_ctime": now,
            }

        def readdir(self, path: str, fh: int) -> list:
            """
            读取目录内容

            Args:
                path: 目录路径
                fh: 文件描述符

            Returns:
                目录项列表
            """
            logger.debug(f"readdir: {path}")

            try:
                uri = self._path_to_uri(path)
                logger.debug(f"Listing directory URI: {uri}")

                items = self.mount._client.ls(uri)
                entries = [".", ".."]

                for item in items:
                    if isinstance(item, dict):
                        name = item.get("name", "")
                    else:
                        name = str(item)

                    if name:
                        entries.append(name)

                return entries
            except Exception as e:
                logger.warning(f"readdir error: {e}")
                return [".", ".."]

        def open(self, path: str, flags: int) -> int:
            """
            打开文件

            Args:
                path: 文件路径
                flags: 打开标志

            Returns:
                文件描述符
            """
            logger.debug(f"open: {path} (flags={flags})")

            if (flags & os.O_WRONLY or flags & os.O_RDWR) and self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            uri = self._path_to_uri(path)

            self._fd += 1
            fd = self._fd
            self._file_handles[fd] = uri

            if not (flags & os.O_WRONLY):
                try:
                    logger.debug(f"Reading file URI: {uri}")
                    content = self.mount._client.read(uri)
                    self._file_contents[uri] = content
                except Exception as e:
                    logger.warning(f"Failed to pre-read {path}: {e}")

            return fd

        def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
            """
            读取文件内容

            Args:
                path: 文件路径
                size: 读取大小
                offset: 偏移量
                fh: 文件描述符

            Returns:
                读取的字节
            """
            logger.debug(f"read: {path} (size={size}, offset={offset})")

            uri = self._file_handles.get(fh)
            if not uri:
                raise FuseOSError(errno.EBADF)

            if uri in self._file_contents:
                content = self._file_contents[uri]
            else:
                try:
                    logger.debug(f"Reading file URI: {uri}")
                    content = self.mount._client.read(uri)
                    self._file_contents[uri] = content
                except Exception as e:
                    logger.error(f"read error: {e}")
                    raise FuseOSError(errno.EIO)

            content_bytes = content.encode("utf-8")
            return content_bytes[offset : offset + size]

        def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
            """
            写入文件内容

            Args:
                path: 文件路径
                data: 要写入的数据
                offset: 偏移量
                fh: 文件描述符

            Returns:
                写入的字节数
            """
            logger.debug(f"write: {path} (size={len(data)}, offset={offset})")

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            uri = self._file_handles.get(fh)
            if not uri:
                raise FuseOSError(errno.EBADF)

            if uri not in self._file_contents:
                self._file_contents[uri] = ""

            current_content = self._file_contents[uri]
            current_bytes = current_content.encode("utf-8")

            new_bytes = current_bytes[:offset] + data + current_bytes[offset + len(data) :]
            self._file_contents[uri] = new_bytes.decode("utf-8")

            return len(data)

        def release(self, path: str, fh: int) -> None:
            """
            关闭文件

            Args:
                path: 文件路径
                fh: 文件描述符
            """
            logger.debug(f"release: {path}")

            uri = self._file_handles.pop(fh, None)

            if uri and uri in self._file_contents:
                logger.warning(f"File {path} was modified but OpenViking direct write is limited")

        def mkdir(self, path: str, mode: int) -> None:
            """
            创建目录

            Args:
                path: 目录路径
                mode: 权限模式
            """
            logger.debug(f"mkdir: {path}")

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            try:
                self.mount.mkdir(path)
            except Exception as e:
                logger.error(f"mkdir error: {e}")
                raise FuseOSError(errno.EIO)

        def rmdir(self, path: str) -> None:
            """
            删除目录

            Args:
                path: 目录路径
            """
            logger.debug(f"rmdir: {path}")

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            try:
                self.mount.delete(path, recursive=False)
            except Exception as e:
                logger.error(f"rmdir error: {e}")
                raise FuseOSError(errno.EIO)

        def unlink(self, path: str) -> None:
            """
            删除文件

            Args:
                path: 文件路径
            """
            logger.debug(f"unlink: {path}")

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            try:
                self.mount.delete(path, recursive=False)
            except Exception as e:
                logger.error(f"unlink error: {e}")
                raise FuseOSError(errno.EIO)

        def truncate(self, path: str, length: int, fh: int = None) -> None:
            """
            截断文件

            Args:
                path: 文件路径
                length: 截断长度
                fh: 文件描述符
            """
            logger.debug(f"truncate: {path} (length={length})")

            if self.mount.config.read_only:
                raise FuseOSError(errno.EROFS)

            uri = self._path_to_uri(path)

            if uri in self._file_contents:
                content = self._file_contents[uri]
                content_bytes = content.encode("utf-8")[:length]
                self._file_contents[uri] = content_bytes.decode("utf-8")

        def utimens(self, path: str, times: tuple = None) -> None:
            """
            更新文件时间戳

            Args:
                path: 文件路径
                times: (atime, mtime) 元组
            """
            logger.debug(f"utimens: {path}")

    def mount_fuse(
        config: MountConfig, foreground: bool = False, allow_other: bool = False
    ) -> None:
        """
        挂载 OpenViking FUSE 文件系统

        Args:
            config: 挂载配置
            foreground: 是否在前台运行
            allow_other: 是否允许其他用户访问
        """
        mount = OpenVikingMount(config)
        operations = OpenVikingFUSE(mount)

        fuse_opts = {}
        if allow_other:
            fuse_opts["allow_other"] = True

        logger.info(f"Mounting OpenViking FUSE at: {config.mount_point}")
        logger.info(f"  Scope: {config.scope.value}")
        logger.info(f"  Read-only: {config.read_only}")
        logger.info(f"  Press Ctrl+C to unmount")

        try:
            FUSE(
                operations,
                str(config.mount_point),
                foreground=foreground,
                nothreads=True,
                **fuse_opts,
            )
        except KeyboardInterrupt:
            logger.info("Unmounting...")
        finally:
            mount.close()
            logger.info("Unmounted")

    class FUSEMountManager:
        """
        FUSE 挂载管理器

        管理 FUSE 挂载进程的生命周期
        """

        def __init__(self):
            self._mounts: Dict[str, Any] = {}

        def mount(self, mount_id: str, config: MountConfig, background: bool = True) -> None:
            """
            挂载 FUSE 文件系统

            Args:
                mount_id: 挂载 ID
                config: 挂载配置
                background: 是否在后台运行
            """
            if background:
                import multiprocessing

                def _mount_worker():
                    mount_fuse(config, foreground=True)

                process = multiprocessing.Process(target=_mount_worker, daemon=True)
                process.start()
                self._mounts[mount_id] = process
                logger.info(f"Started FUSE mount {mount_id} in background (PID: {process.pid})")
            else:
                mount_fuse(config, foreground=True)

        def unmount(self, mount_id: str) -> None:
            """
            卸载 FUSE 文件系统

            Args:
                mount_id: 挂载 ID
            """
            if mount_id in self._mounts:
                process = self._mounts.pop(mount_id)
                process.terminate()
                process.join(timeout=5)
                logger.info(f"Unmounted {mount_id}")

        def unmount_all(self) -> None:
            """卸载所有 FUSE 文件系统"""
            for mount_id in list(self._mounts.keys()):
                self.unmount(mount_id)

else:
    # FUSE 不可用时的占位符
    OpenVikingFUSE = None

    def mount_fuse(*args, **kwargs):
        raise ImportError(
            "fusepy and libfuse are required. Install with: pip install fusepy and install libfuse system package"
        )

    class FUSEMountManager:
        """FUSE 挂载管理器（占位符）"""

        def __init__(self):
            self._mounts: Dict[str, Any] = {}

        def mount(self, *args, **kwargs):
            raise ImportError("fusepy and libfuse are required")

        def unmount(self, *args, **kwargs):
            pass

        def unmount_all(self):
            pass
