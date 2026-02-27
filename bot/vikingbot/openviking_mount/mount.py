"""
OpenViking Filesystem Mount Module - Core Implementation

这个模块将OpenViking的虚拟文件系统挂载到本地文件系统路径，
让用户可以像操作普通文件一样操作OpenViking上的数据。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from enum import Enum
import openviking as ov

from loguru import logger


class MountScope(Enum):
    """OpenViking挂载作用域"""

    RESOURCES = "resources"
    SESSION = "session"
    USER = "user"
    AGENT = "agent"
    ALL = "all"


@dataclass
class MountConfig:
    """挂载配置"""

    mount_point: Path  # 挂载点路径
    openviking_data_path: Path  # OpenViking数据存储路径
    session_id: Optional[str] = None  # 会话ID（如果是session作用域）
    scope: MountScope = MountScope.RESOURCES  # 挂载作用域
    auto_init: bool = True  # 是否自动初始化
    read_only: bool = False  # 是否只读模式
    async_add_resource: bool = False  # 是否异步执行add_resource


@dataclass
class FileInfo:
    """文件信息"""

    uri: str  # OpenViking URI
    name: str  # 文件名
    is_dir: bool  # 是否是目录
    size: int = 0  # 文件大小
    modified_at: float = 0.0  # 修改时间
    abstract: Optional[str] = None  # L0摘要（如果有）
    overview: Optional[str] = None  # L1概览（如果有）


class OpenVikingMount:
    """
    OpenViking文件系统挂载类

    将OpenViking的虚拟文件系统映射到本地文件系统操作
    """

    def __init__(self, config: MountConfig):
        """
        初始化OpenViking挂载

        Args:
            config: 挂载配置
        """
        self.config = config
        self._client: Optional[ov.SyncOpenViking] = None
        self._initialized = False
        self._mount_point_created = False

        # 确保挂载点存在
        self._ensure_mount_point()

    def _ensure_mount_point(self) -> None:
        """确保挂载点目录存在"""
        if not self.config.mount_point.exists():
            self.config.mount_point.mkdir(parents=True, exist_ok=True)
            self._mount_point_created = True
            logger.info(f"Created mount point: {self.config.mount_point}")

    def initialize(self) -> None:
        """初始化OpenViking客户端"""
        if self._initialized:
            return

        if ov is None:
            raise ImportError("openviking module is not available")

        logger.info(f"Initializing OpenViking at: {self.config.openviking_data_path}")

        # 初始化OpenViking客户端
        self._client = ov.SyncOpenViking(path=str(self.config.openviking_data_path))
        self._client.initialize()

        self._initialized = True
        logger.info("OpenViking initialized successfully")

    def _ensure_client(self) -> None:
        """确保客户端已初始化"""
        if not self._initialized:
            if self.config.auto_init:
                self.initialize()
            else:
                raise RuntimeError("OpenViking client not initialized. Call initialize() first.")

    @property
    def client(self) -> Optional[ov.SyncOpenViking]:
        """获取底层OpenViking客户端"""
        return self._client

    def _uri_to_path(self, uri: str) -> Path:
        """
        将OpenViking URI转换为本地文件路径

        Args:
            uri: OpenViking URI (e.g., viking://resources/path/to/file)

        Returns:
            本地文件路径
        """
        # 解析URI
        if uri.startswith("viking://"):
            uri = uri[len("viking://") :]

        # 处理作用域
        parts = uri.split("/", 1)
        if len(parts) == 2:
            scope, rest = parts
        else:
            scope, rest = parts[0], ""

        # 根据配置的作用域过滤
        if self.config.scope != MountScope.ALL:
            if scope != self.config.scope.value:
                # 如果不是目标作用域，可能需要调整路径
                pass

        # 构建本地路径
        return self.config.mount_point / scope / rest

    def _path_to_uri(self, path: Union[str, Path]) -> str:
        """
        将本地文件路径转换为OpenViking URI

        Args:
            path: 本地文件路径

        Returns:
            OpenViking URI
        """
        path = Path(path)

        # 获取相对于挂载点的路径
        try:
            rel_path = path.relative_to(self.config.mount_point)
        except ValueError:
            # 如果不在挂载点下，假设是相对于挂载点的路径
            rel_path = path

        # 构建URI
        return f"viking://{rel_path}"

    def _get_scope_root_uri(self) -> str:
        """获取当前作用域的根URI"""
        if self.config.scope == MountScope.ALL:
            return "viking://"
        return f"viking://{self.config.scope.value}"

    def list_dir(self, path: Union[str, Path]) -> List[FileInfo]:
        """
        列出目录内容

        Args:
            path: 本地目录路径

        Returns:
            文件信息列表
        """
        self._ensure_client()

        uri = self._path_to_uri(path)
        logger.debug(f"Listing directory: {uri}")

        try:
            items = self._client.ls(uri)
        except Exception as e:
            logger.warning(f"Failed to list {uri}: {e}")
            return []

        file_infos = []
        for item in items:
            # 解析ls返回的项目
            # 假设返回格式是字典或对象，需要根据实际API调整
            if isinstance(item, dict):
                name = item.get("name", "")
                is_dir = item.get("is_dir", False)
                item_uri = item.get("uri", "")
            else:
                # 简单处理
                name = str(item)
                is_dir = False
                item_uri = f"{uri.rstrip('/')}/{name}"

            file_info = FileInfo(uri=item_uri, name=name, is_dir=is_dir)
            file_infos.append(file_info)

        return file_infos

    def read_file(self, path: Union[str, Path]) -> str:
        """
        读取文件内容

        Args:
            path: 本地文件路径

        Returns:
            文件内容
        """
        self._ensure_client()

        uri = self._path_to_uri(path)
        logger.debug(f"Reading file: {uri}")

        try:
            return self._client.read(uri)
        except Exception as e:
            logger.error(f"Failed to read {uri}: {e}")
            raise

    def write_file(self, path: Union[str, Path], content: str) -> None:
        """
        写入文件内容

        Args:
            path: 本地文件路径
            content: 文件内容
        """
        if self.config.read_only:
            raise PermissionError("Mount is read-only")

        self._ensure_client()

        # 注意：OpenViking的add_resource主要用于添加外部资源
        # 对于直接写入，可能需要不同的方法
        # 这里我们先实现一个简化版本
        logger.warning("Direct file write is limited in OpenViking. Using add_resource approach.")

        uri = self._path_to_uri(path)
        logger.debug(f"Writing file: {uri}")

        # 这种情况下，我们可能需要先写入临时文件，然后add_resource
        # 或者使用其他方法
        raise NotImplementedError("Direct file write requires special handling in OpenViking")

    def mkdir(self, path: Union[str, Path]) -> None:
        """
        创建目录

        Args:
            path: 本地目录路径
        """
        if self.config.read_only:
            raise PermissionError("Mount is read-only")

        self._ensure_client()

        uri = self._path_to_uri(path)
        logger.debug(f"Creating directory: {uri}")

        try:
            self._client.mkdir(uri)
        except Exception as e:
            logger.error(f"Failed to create directory {uri}: {e}")
            raise

    def delete(self, path: Union[str, Path], recursive: bool = False) -> None:
        """
        删除文件或目录

        Args:
            path: 本地文件路径
            recursive: 是否递归删除
        """
        if self.config.read_only:
            raise PermissionError("Mount is read-only")

        self._ensure_client()

        uri = self._path_to_uri(path)
        logger.debug(f"Deleting: {uri} (recursive={recursive})")

        try:
            self._client.rm(uri, recursive=recursive)
        except Exception as e:
            logger.error(f"Failed to delete {uri}: {e}")
            raise

    def get_abstract(self, path: Union[str, Path]) -> Optional[str]:
        """
        获取文件/目录的L0摘要

        Args:
            path: 本地文件路径

        Returns:
            摘要内容
        """
        self._ensure_client()

        uri = self._path_to_uri(path)
        logger.debug(f"Getting abstract for: {uri}")

        try:
            return self._client.abstract(uri)
        except Exception as e:
            logger.warning(f"Failed to get abstract for {uri}: {e}")
            return None

    def get_overview(self, path: Union[str, Path]) -> Optional[str]:
        """
        获取文件/目录的L1概览

        Args:
            path: 本地文件路径

        Returns:
            概览内容
        """
        self._ensure_client()

        uri = self._path_to_uri(path)
        logger.debug(f"Getting overview for: {uri}")

        try:
            return self._client.overview(uri)
        except Exception as e:
            logger.warning(f"Failed to get overview for {uri}: {e}")
            return None

    def search(self, query: str, target_path: Optional[Union[str, Path]] = None) -> List[FileInfo]:
        """
        语义搜索

        Args:
            query: 搜索查询
            target_path: 搜索目标路径

        Returns:
            搜索结果文件信息列表
        """
        self._ensure_client()

        target_uri = self._get_scope_root_uri()
        if target_path:
            target_uri = self._path_to_uri(target_path)

        logger.debug(f"Searching: '{query}' in {target_uri}")

        try:
            results = self._client.find(query, target_uri=target_uri)

            file_infos = []
            for r in results.resources:
                file_info = FileInfo(
                    uri=r.uri,
                    name=Path(r.uri).name,
                    is_dir=False,  # 需要根据实际结果判断
                )
                if hasattr(r, "score"):
                    setattr(file_info, "score", r.score)
                file_infos.append(file_info)

            return file_infos
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def add_resource(
        self,
        source_path: Union[str, Path],
        target_path: Optional[Union[str, Path]] = None,
        wait: bool = True,
    ) -> str:
        """
        添加资源到OpenViking

        Args:
            source_path: 源文件/目录路径
            target_path: 目标路径（在OpenViking中）
            wait: 是否等待语义提取和向量化完成

        Returns:
            根URI
        """
        if self.config.read_only:
            raise PermissionError("Mount is read-only")

        self._ensure_client()

        target_uri = None
        if target_path:
            target_uri = self._path_to_uri(target_path)

        logger.debug(f"Adding resource: {source_path} -> {target_uri} (wait={wait})")

        try:
            result = self._client.add_resource(path=str(source_path), target=target_uri, wait=wait)
            return result.get("root_uri", "")
        except Exception as e:
            logger.error(f"Failed to add resource: {e}")
            raise

    def sync_to_disk(self, path: Optional[Union[str, Path]] = None) -> None:
        """
        将OpenViking内容同步到磁盘

        注意：这是一个简化的实现，用于演示目的
        实际生产环境可能需要更复杂的同步机制

        Args:
            path: 要同步的路径，None表示同步全部
        """
        self._ensure_client()

        root_uri = self._get_scope_root_uri()
        if path:
            root_uri = self._path_to_uri(path)

        logger.info(f"Syncing {root_uri} to disk...")

        # 这里实现一个简单的递归同步
        self._sync_recursive(root_uri, self.config.mount_point)

    def _sync_recursive(self, uri: str, local_path: Path) -> None:
        """递归同步"""
        try:
            # 列出目录内容
            items = self._client.ls(uri)

            # 确保本地目录存在
            local_path.mkdir(parents=True, exist_ok=True)

            for item in items:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    is_dir = item.get("is_dir", False)
                    item_uri = item.get("uri", f"{uri.rstrip('/')}/{name}")
                else:
                    name = str(item)
                    is_dir = False
                    item_uri = f"{uri.rstrip('/')}/{name}"

                item_local_path = local_path / name

                if is_dir:
                    # 递归处理子目录
                    self._sync_recursive(item_uri, item_local_path)
                else:
                    # 读取并写入文件
                    try:
                        content = self._client.read(item_uri)
                        item_local_path.write_text(content)
                        logger.debug(f"Synced: {item_uri} -> {item_local_path}")
                    except Exception as e:
                        logger.warning(f"Failed to sync {item_uri}: {e}")

        except Exception as e:
            logger.warning(f"Failed to sync {uri}: {e}")

    def close(self) -> None:
        """关闭挂载并释放资源"""
        if self._client and self._initialized:
            try:
                self._client.close()
                logger.info("OpenViking client closed")
            except Exception as e:
                logger.warning(f"Error closing client: {e}")

        self._initialized = False
        self._client = None

    def __enter__(self) -> "OpenVikingMount":
        """上下文管理器入口"""
        if self.config.auto_init:
            self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口"""
        self.close()
