"""
OpenViking Filesystem Mount Module

这个模块将OpenViking的虚拟文件系统挂载到本地文件系统路径，
让用户可以像操作普通文件一样操作OpenViking上的数据。
"""

from typing import TYPE_CHECKING

from .mount import OpenVikingMount, MountScope, MountConfig, FileInfo
from .manager import OpenVikingMountManager, MountPoint, get_mount_manager
from .session_integration import SessionOpenVikingManager, get_session_ov_manager

__all__ = [
    "OpenVikingMount",
    "MountScope",
    "MountConfig",
    "FileInfo",
    "OpenVikingMountManager",
    "MountPoint",
    "get_mount_manager",
    "OpenVikingFUSE",
    "mount_fuse",
    "FUSEMountManager",
    "FUSE_AVAILABLE",
    "SessionOpenVikingManager",
    "get_session_ov_manager",
]

if TYPE_CHECKING:
    from .viking_fuse import OpenVikingFUSE, mount_fuse, FUSEMountManager, FUSE_AVAILABLE


def __getattr__(name: str):
    if name in ("OpenVikingFUSE", "mount_fuse", "FUSEMountManager", "FUSE_AVAILABLE"):
        from .viking_fuse import OpenVikingFUSE, mount_fuse, FUSEMountManager, FUSE_AVAILABLE

        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
