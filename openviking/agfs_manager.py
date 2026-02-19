# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""AGFS Process Manager - Responsible for starting and stopping the AGFS server."""

import atexit
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking_cli.utils.config.agfs_config import AGFSConfig

logger = get_logger(__name__)


class AGFSManager:
    """
    Manages the lifecycle of the AGFS server process.

    Examples:
        # 1. Local backend
        from openviking_cli.utils.config.agfs_config import AGFSConfig

        config = AGFSConfig(
            path="./data",
            port=1833,
            backend="local",
            log_level="info"
        )
        manager = AGFSManager(config=config)
        manager.start()

        # 2. S3 backend
        from openviking_cli.utils.config.agfs_config import AGFSConfig, S3Config

        config = AGFSConfig(
            path="./data",
            port=1833,
            backend="s3",
            s3=S3Config(
                bucket="my-bucket",
                region="us-east-1",
                access_key="your-access-key",
                secret_key="your-secret-key",
                endpoint="https://s3.amazonaws.com"
            ),
            log_level="debug"
        )
        manager = AGFSManager(config=config)
        manager.start()

        # 3. Using with context manager (auto cleanup)
        with AGFSManager(config=config):
            # AGFS server is running
            pass
        # Server automatically stopped
    """

    def __init__(
        self,
        config: "AGFSConfig",
    ):
        """
        Initialize AGFS Manager.

        Args:
            config: AGFS configuration object containing settings like port, path, backend, etc.
        """
        self.data_path = Path(config.path).resolve()  # Convert to absolute path
        self.config = config
        self.port = config.port
        self.log_level = config.log_level
        self.backend = config.backend
        self.s3_config = config.s3

        self.process: Optional[subprocess.Popen] = None
        self.config_file: Optional[Path] = None

        atexit.register(self.stop)

    @property
    def vikingfs_path(self) -> Path:
        """AGFS LocalFS data directory."""
        return self.data_path / "viking"

    @property
    def binary_path(self) -> Path:
        """AGFS binary file path."""
        package_dir = Path(__file__).parent
        binary_name = "agfs-server"
        if platform.system() == "Windows":
            binary_name += ".exe"
        return package_dir / "bin" / binary_name

    @property
    def url(self) -> str:
        """AGFS service URL."""
        return f"http://localhost:{self.port}"

    def _check_port_available(self) -> None:
        """Check if the port is available."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
        except OSError as e:
            raise RuntimeError(
                f"AGFS port {self.port} is already in use, cannot start service. "
                f"Please check if another AGFS process is running, or use a different port."
            ) from e
        finally:
            sock.close()

    def _generate_config(self) -> Path:
        """Dynamically generate AGFS configuration file based on backend type."""
        config = {
            "server": {
                "address": f":{self.port}",
                "log_level": self.log_level,
            },
            "plugins": {
                "serverinfofs": {
                    "enabled": True,
                    "path": "/serverinfo",
                    "config": {
                        "version": "1.0.0",
                    },
                },
                "queuefs": {
                    "enabled": True,
                    "path": "/queue",
                },
            },
        }

        if self.backend == "local":
            config["plugins"]["localfs"] = {
                "enabled": True,
                "path": "/local",
                "config": {
                    "local_dir": str(self.vikingfs_path),
                },
            }
        elif self.backend == "s3":
            # AGFS S3 backend configuration (s3fs plugin)
            # This enables AGFS to mount an S3 bucket as a local filesystem.
            # Implementation details: third_party/agfs/agfs-server/pkg/plugins/s3fs/s3fs.go
            config["plugins"]["s3fs"] = {
                "enabled": True,
                "path": "/local",
                "config": {
                    "bucket": self.s3_config.bucket,
                    "region": self.s3_config.region,
                    "access_key_id": self.s3_config.access_key,
                    "secret_access_key": self.s3_config.secret_key,
                    "endpoint": self.s3_config.endpoint,
                    "prefix": self.s3_config.prefix,
                    "disable_ssl": not self.s3_config.use_ssl,
                },
            }
        elif self.backend == "memory":
            config["plugins"]["memfs"] = {
                "enabled": True,
                "path": "/local",
            }

        config_dir = self.data_path / ".agfs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"

        with open(config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        self.config_file = config_file
        return config_file

    def start(self) -> None:
        """Start the AGFS server."""
        if self.process is not None and self.process.poll() is None:
            logger.info("[AGFSManager] AGFS already running")
            return

        # Check if port is available
        self._check_port_available()

        self.vikingfs_path.mkdir(parents=True, exist_ok=True)
        # Create temp directory for Parser use
        (self.vikingfs_path / "temp").mkdir(exist_ok=True)
        config_file = self._generate_config()

        if not self.binary_path.exists():
            raise FileNotFoundError(
                f"AGFS binary not found at {self.binary_path}. "
                "Please build AGFS first: cd third_party/agfs/agfs-server && make build && cp build/agfs-server ../bin/"
            )

        logger.info(f"[AGFSManager] Starting AGFS on port {self.port} with backend {self.backend}")
        self.process = subprocess.Popen(
            [str(self.binary_path), "-c", str(config_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self._wait_for_ready()
        logger.info(f"[AGFSManager] AGFS started at {self.url}")

    def _wait_for_ready(self, timeout: float = 5.0) -> None:
        """Wait for AGFS service to be ready."""
        import requests

        logger.info(f"[AGFSManager] Waiting for AGFS to be ready at {self.url}/api/v1/health")
        logger.info(f"[AGFSManager] Config file: {self.config_file}")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                resp = requests.get(f"{self.url}/api/v1/health", timeout=0.5)
                if resp.status_code == 200:
                    logger.info("[AGFSManager] AGFS is ready")
                    return
            except requests.RequestException as e:
                logger.debug(f"[AGFSManager] Health check failed: {e}")

            time.sleep(0.1)

        # Timeout, try reading output
        logger.error(
            f"[AGFSManager] Timeout after {timeout}s, process still running: {self.process.poll() is None}"
        )
        raise TimeoutError(f"AGFS failed to start within {timeout}s")

    def stop(self) -> None:
        """Stop the AGFS server."""
        if self.process is None:
            return

        if self.process.poll() is None:
            logger.info("[AGFSManager] Stopping AGFS")
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logger.warning("[AGFSManager] AGFS not responding, killing")
                self.process.kill()
                self.process.wait()

        # Close pipes to prevent ResourceWarning
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()

        self.process = None

    def is_running(self) -> bool:
        """Check if AGFS is running."""
        return self.process is not None and self.process.poll() is None
