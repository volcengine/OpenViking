"""
OpenViking Active Daemon package.
Monitors AI tool logs and automatically extracts knowledge into viking:// storage.
"""
from openviking.daemon.service import DaemonService

__all__ = ["DaemonService"]
