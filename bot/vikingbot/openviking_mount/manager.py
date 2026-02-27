"""
OpenViking Mount Manager

管理多个OpenViking挂载点的生命周期
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from loguru import logger

from .mount import OpenVikingMount, MountConfig, MountScope


@dataclass
class MountPoint:
    """挂载点信息"""

    id: str
    config: MountConfig
    mount: OpenVikingMount
    active: bool = True


class OpenVikingMountManager:
    """
    OpenViking挂载管理器

    管理多个挂载点的创建、访问和销毁
    """

    def __init__(self, base_mount_dir: Optional[Path] = None):
        """
        初始化挂载管理器

        Args:
            base_mount_dir: 基础挂载目录，所有挂载点将在此目录下创建
        """
        if base_mount_dir is None:
            # 默认在用户目录下创建
            base_mount_dir = Path.home() / ".vikingbot" / "mounts"

        self.base_mount_dir = base_mount_dir
        self._mounts: Dict[str, MountPoint] = {}

        # 确保基础目录存在
        self.base_mount_dir.mkdir(parents=True, exist_ok=True)

    def create_mount(
        self,
        mount_id: str,
        openviking_data_path: Path,
        scope: MountScope = MountScope.RESOURCES,
        session_id: Optional[str] = None,
        read_only: bool = False,
    ) -> OpenVikingMount:
        """
        创建一个新的挂载点

        Args:
            mount_id: 挂载点唯一标识
            openviking_data_path: OpenViking数据存储路径
            scope: 挂载作用域
            session_id: 会话ID（session作用域时需要）
            read_only: 是否只读模式

        Returns:
            OpenVikingMount实例
        """
        if mount_id in self._mounts:
            raise ValueError(f"Mount with id '{mount_id}' already exists")

        # 创建挂载点路径
        mount_point = self.base_mount_dir / mount_id

        config = MountConfig(
            mount_point=mount_point,
            openviking_data_path=openviking_data_path,
            session_id=session_id,
            scope=scope,
            auto_init=True,
            read_only=read_only,
        )

        mount = OpenVikingMount(config)

        # 初始化
        mount.initialize()

        mount_point_info = MountPoint(id=mount_id, config=config, mount=mount, active=True)

        self._mounts[mount_id] = mount_point_info
        logger.info(f"Created mount: {mount_id} at {mount_point}")

        return mount

    def get_mount(self, mount_id: str) -> Optional[OpenVikingMount]:
        """
        获取挂载点

        Args:
            mount_id: 挂载点ID

        Returns:
            OpenVikingMount实例，如果不存在返回None
        """
        mount_point = self._mounts.get(mount_id)
        if mount_point and mount_point.active:
            return mount_point.mount
        return None

    def list_mounts(self) -> List[Dict]:
        """
        列出所有挂载点

        Returns:
            挂载点信息列表
        """
        mounts_info = []
        for mount_id, mount_point in self._mounts.items():
            mounts_info.append(
                {
                    "id": mount_id,
                    "mount_point": str(mount_point.config.mount_point),
                    "openviking_path": str(mount_point.config.openviking_data_path),
                    "scope": mount_point.config.scope.value,
                    "session_id": mount_point.config.session_id,
                    "active": mount_point.active,
                    "read_only": mount_point.config.read_only,
                }
            )
        return mounts_info

    def remove_mount(self, mount_id: str, cleanup: bool = False) -> None:
        """
        移除挂载点

        Args:
            mount_id: 挂载点ID
            cleanup: 是否清理挂载点目录
        """
        mount_point = self._mounts.pop(mount_id, None)
        if mount_point:
            # 关闭挂载
            try:
                mount_point.mount.close()
            except Exception as e:
                logger.warning(f"Error closing mount {mount_id}: {e}")

            mount_point.active = False

            # 清理挂载点目录
            if cleanup and mount_point.config.mount_point.exists():
                try:
                    import shutil

                    shutil.rmtree(mount_point.config.mount_point)
                    logger.info(f"Cleaned up mount point: {mount_point.config.mount_point}")
                except Exception as e:
                    logger.warning(f"Error cleaning up mount point: {e}")

            logger.info(f"Removed mount: {mount_id}")

    def remove_all(self, cleanup: bool = False) -> None:
        """
        移除所有挂载点

        Args:
            cleanup: 是否清理挂载点目录
        """
        mount_ids = list(self._mounts.keys())
        for mount_id in mount_ids:
            self.remove_mount(mount_id, cleanup=cleanup)

    def create_session_mount(
        self, session_id: str, openviking_data_path: Path, read_only: bool = False
    ) -> OpenVikingMount:
        """
        为特定会话创建挂载点

        Args:
            session_id: 会话ID
            openviking_data_path: OpenViking数据路径
            read_only: 是否只读

        Returns:
            OpenVikingMount实例
        """
        mount_id = f"session_{session_id}"
        return self.create_mount(
            mount_id=mount_id,
            openviking_data_path=openviking_data_path,
            scope=MountScope.SESSION,
            session_id=session_id,
            read_only=read_only,
        )

    def create_resources_mount(
        self,
        mount_id: str = "resources",
        openviking_data_path: Optional[Path] = None,
        read_only: bool = False,
    ) -> OpenVikingMount:
        """
        创建资源挂载点

        Args:
            mount_id: 挂载点ID
            openviking_data_path: OpenViking数据路径
            read_only: 是否只读

        Returns:
            OpenVikingMount实例
        """
        if openviking_data_path is None:
            # 默认使用vikingbot的openviking数据目录
            openviking_data_path = Path.home() / ".vikingbot" / "openviking_data"

        return self.create_mount(
            mount_id=mount_id,
            openviking_data_path=openviking_data_path,
            scope=MountScope.RESOURCES,
            read_only=read_only,
        )

    def __enter__(self) -> "OpenVikingMountManager":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口"""
        self.remove_all(cleanup=False)


# 全局管理器实例（单例）
_global_manager: Optional[OpenVikingMountManager] = None


def get_mount_manager(base_mount_dir: Optional[Path] = None) -> OpenVikingMountManager:
    """
    获取全局挂载管理器实例

    Args:
        base_mount_dir: 基础挂载目录（仅在首次调用时有效）

    Returns:
        OpenVikingMountManager单例
    """
    global _global_manager
    if _global_manager is None:
        _global_manager = OpenVikingMountManager(base_mount_dir=base_mount_dir)
    return _global_manager
