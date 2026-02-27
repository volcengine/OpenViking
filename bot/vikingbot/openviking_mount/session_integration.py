"""
OpenViking FUSE 会话集成

提供与会话管理器的集成，自动在 .vikingbot/workspace/{session}/ 挂载 OpenViking
每个session直接在自己的workspace下管理内容
"""

from __future__ import annotations

import sys
import asyncio
import shutil
from pathlib import Path
from typing import Dict, Optional, Any

from loguru import logger

# 相对导入同一包内的模块
from .mount import OpenVikingMount, MountConfig, MountScope
from .viking_fuse import mount_fuse, FUSEMountManager, FUSE_AVAILABLE


class SessionOpenVikingManager:
    """
    会话 OpenViking 管理器

    管理每个会话的 OpenViking 挂载，每个session直接在自己的workspace下管理
    """

    def __init__(self, base_workspace: Optional[Path] = None):
        """
        初始化管理器

        Args:
            base_workspace: 基础工作区路径
        """
        if base_workspace is None:
            base_workspace = Path.home() / ".vikingbot" / "workspace"

        self.base_workspace = base_workspace
        self.base_workspace.mkdir(parents=True, exist_ok=True)

        # 跟踪每个会话的挂载
        self._session_mounts: Dict[str, Dict[str, Any]] = {}

        # FUSE 挂载管理器（如果可用）
        self._fuse_manager = FUSEMountManager() if FUSE_AVAILABLE else None

        logger.info(f"SessionOpenVikingManager initialized")
        logger.info(f"  Base workspace: {self.base_workspace}")
        logger.info(f"  FUSE available: {FUSE_AVAILABLE}")

    def get_session_workspace(self, session_key: str) -> Path:
        """
        获取会话的工作区路径

        Args:
            session_key: 会话键

        Returns:
            工作区路径: .vikingbot/workspace/{session}/
        """
        safe_session_key = session_key.replace(":", "__")
        return self.base_workspace / safe_session_key

    def get_session_ov_data_path(self, session_key: str) -> Path:
        """
        获取会话的 OpenViking 数据存储路径（在workspace内部）

        Args:
            session_key: 会话键

        Returns:
            数据存储路径: .vikingbot/workspace/{session}/.ov_data/
        """
        return self.get_session_workspace(session_key) / ".ov_data"

    def mount_for_session(
        self, session_key: str, use_fuse: bool = True, background: bool = True
    ) -> bool:
        """
        为会话挂载 OpenViking

        Args:
            session_key: 会话键
            use_fuse: 是否使用 FUSE（如果可用）
            background: FUSE 是否在后台运行

        Returns:
            是否成功
        """
        if session_key in self._session_mounts:
            logger.debug(f"Session {session_key} already mounted")
            return True

        session_workspace = self.get_session_workspace(session_key)
        ov_data_path = self.get_session_ov_data_path(session_key)

        # 确保目录存在 - workspace本身就是挂载点
        session_workspace.mkdir(parents=True, exist_ok=True)
        ov_data_path.mkdir(parents=True, exist_ok=True)

        mount_info = {
            "session_key": session_key,
            "session_workspace": session_workspace,
            "ov_data_path": ov_data_path,
            "use_fuse": use_fuse and FUSE_AVAILABLE,
            "fuse_mount_id": None,
            "api_mount": None,
        }

        try:
            if use_fuse and FUSE_AVAILABLE and self._fuse_manager:
                # 使用 FUSE 挂载
                logger.info(f"Mounting OpenViking via FUSE for session {session_key}")
                logger.info(f"  Mount path: {session_workspace}")

                config = MountConfig(
                    mount_point=session_workspace,
                    openviking_data_path=ov_data_path,
                    session_id=session_key,
                    scope=MountScope.SESSION,
                    auto_init=True,
                    read_only=False,
                )

                # 为 FUSE 生成唯一的挂载 ID
                fuse_mount_id = f"session_{session_key.replace(':', '_')}"
                mount_info["fuse_mount_id"] = fuse_mount_id

                if background:
                    self._fuse_manager.mount(fuse_mount_id, config, background=True)
                else:
                    # 前台模式需要单独处理
                    mount_fuse(config, foreground=True)

                logger.info(f"✓ FUSE mounted for session {session_key}")

            else:
                # 使用 API 层挂载 - mount_point就是workspace本身
                logger.info(f"Mounting OpenViking via API for session {session_key}")
                logger.info(f"  Session workspace: {session_workspace}")

                config = MountConfig(
                    mount_point=session_workspace,
                    openviking_data_path=ov_data_path,
                    session_id=session_key,
                    scope=MountScope.SESSION,
                    auto_init=True,
                    read_only=False,
                )

                api_mount = OpenVikingMount(config)
                api_mount.initialize()
                mount_info["api_mount"] = api_mount

                logger.info(f"✓ API mounted for session {session_key}")

            self._session_mounts[session_key] = mount_info
            return True

        except Exception as e:
            logger.error(f"Failed to mount for session {session_key}: {e}")
            import traceback

            traceback.print_exc()
            return False

    def delete_session_workspace(self, session_key: str) -> bool:
        """
        删除会话的workspace，同时清理挂载

        Args:
            session_key: 会话键

        Returns:
            是否成功
        """
        logger.info(f"Deleting session workspace and cleaning up mount: {session_key}")

        # 先卸载挂载
        unmount_success = self.unmount_for_session(session_key)

        # 删除workspace目录
        session_workspace = self.get_session_workspace(session_key)
        if session_workspace.exists():
            try:
                shutil.rmtree(session_workspace)
                logger.info(f"✓ Deleted session workspace: {session_workspace}")
                return unmount_success
            except Exception as e:
                logger.error(f"Failed to delete session workspace: {e}")
                return False

        return unmount_success

    def unmount_for_session(self, session_key: str) -> bool:
        """
        为会话卸载 OpenViking

        Args:
            session_key: 会话键

        Returns:
            是否成功
        """
        if session_key not in self._session_mounts:
            return True

        mount_info = self._session_mounts.pop(session_key)

        try:
            if mount_info.get("fuse_mount_id") and self._fuse_manager:
                logger.info(f"Unmounting FUSE for session {session_key}")
                self._fuse_manager.unmount(mount_info["fuse_mount_id"])

            if mount_info.get("api_mount"):
                logger.info(f"Closing API mount for session {session_key}")
                mount_info["api_mount"].close()

            logger.info(f"✓ Unmounted for session {session_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to unmount for session {session_key}: {e}")
            return False

    def is_mounted(self, session_key: str) -> bool:
        """检查会话是否已挂载"""
        return session_key in self._session_mounts

    def is_workspace_exists(self, session_key: str) -> bool:
        """
        检查会话的workspace是否还存在（防止系统外手动删除）

        Args:
            session_key: 会话键

        Returns:
            workspace是否存在
        """
        workspace = self.get_session_workspace(session_key)
        return workspace.exists()

    def cleanup_orphaned_mounts(self) -> int:
        """
        清理孤立的挂载（workspace已被系统外删除，但挂载还在内存中）

        Returns:
            清理的挂载数量
        """
        cleaned_count = 0
        session_keys = list(self._session_mounts.keys())

        for session_key in session_keys:
            if not self.is_workspace_exists(session_key):
                logger.warning(
                    f"Found orphaned mount for {session_key} - workspace deleted externally"
                )
                self.unmount_for_session(session_key)
                cleaned_count += 1

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} orphaned mounts")

        return cleaned_count

    def get_api_mount(self, session_key: str) -> Optional[OpenVikingMount]:
        """
        获取会话的 API 挂载对象（带workspace存在性检查）

        Args:
            session_key: 会话键

        Returns:
            OpenVikingMount 实例
        """
        if session_key not in self._session_mounts:
            return None

        # 检查workspace是否还存在，不存在则清理
        if not self.is_workspace_exists(session_key):
            logger.warning(f"Workspace for {session_key} not found, cleaning up mount")
            self.unmount_for_session(session_key)
            return None

        mount_info = self._session_mounts[session_key]

        if mount_info.get("api_mount"):
            return mount_info["api_mount"]

        # 如果只有 FUSE，创建一个临时的 API 挂载
        session_workspace = mount_info["session_workspace"]
        ov_data_path = mount_info["ov_data_path"]

        config = MountConfig(
            mount_point=session_workspace,
            openviking_data_path=ov_data_path,
            session_id=session_key,
            scope=MountScope.SESSION,
            auto_init=True,
            read_only=False,
        )

        api_mount = OpenVikingMount(config)
        api_mount.initialize()
        mount_info["api_mount"] = api_mount

        return api_mount

    def unmount_all(self) -> None:
        """卸载所有会话"""
        session_keys = list(self._session_mounts.keys())
        for session_key in session_keys:
            self.unmount_for_session(session_key)

    async def cleanup(self) -> None:
        """清理资源（包括孤立挂载）"""
        self.cleanup_orphaned_mounts()
        self.unmount_all()


# 全局单例
_global_ov_session_manager: Optional[SessionOpenVikingManager] = None


def get_session_ov_manager(base_workspace: Optional[Path] = None) -> SessionOpenVikingManager:
    """
    获取全局会话 OpenViking 管理器

    Args:
        base_workspace: 基础工作区路径（仅首次调用有效）

    Returns:
        SessionOpenVikingManager 单例
    """
    global _global_ov_session_manager
    if _global_ov_session_manager is None:
        _global_ov_session_manager = SessionOpenVikingManager(base_workspace=base_workspace)
    return _global_ov_session_manager
