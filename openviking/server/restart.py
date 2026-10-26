# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-local restart capability, installed by the single-worker CLI."""

import asyncio
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from openviking_cli.exceptions import FailedPreconditionError

_RECOVERY_ENV = "OPENVIKING_RESTART_RECOVERY"
_ROLLED_BACK_ENV = "OPENVIKING_RESTART_ROLLED_BACK"


class RestartRecovery:
    """Recover once from startup failures; disarm after lifespan and socket binding."""

    def __init__(self):
        self.pending = json.loads(os.environ.pop(_RECOVERY_ENV, "null"))
        self.rolled_back = os.environ.pop(_ROLLED_BACK_ENV, "") == "1"

    def prepare(self, revision: str) -> None:
        from openviking.config.config_file import _atomic_write, _config_file_lock
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

        path = OpenVikingConfigSingleton.get_config_file()
        content = OpenVikingConfigSingleton.get_config_file_content()
        if path is None or content is None:
            raise ValueError("Startup configuration is unavailable for restart recovery")
        with _config_file_lock(path):
            if hashlib.sha256(path.read_bytes()).hexdigest() != revision:
                raise ValueError("ov.conf changed; reload before restarting")
            fd, name = tempfile.mkstemp(
                prefix=f".{path.name}.studio.restart.", suffix=".bak", dir=path.parent
            )
            os.close(fd)
            backup = Path(name)
            try:
                _atomic_write(backup, content, 0o600)
                os.environ[_RECOVERY_ENV] = json.dumps(
                    {"path": str(path), "backup": str(backup), "revision": revision}
                )
            except BaseException:
                backup.unlink(missing_ok=True)
                raise

    def ready(self) -> None:
        pending, self.pending = self.pending, None
        if pending:
            try:
                Path(pending["backup"]).unlink(missing_ok=True)
            except OSError:
                print("Could not remove the private restart recovery backup", file=sys.stderr)

    def rollback(self) -> None:
        from openviking.config.config_file import _atomic_write, _config_file_lock

        pending, self.pending = (
            self.pending or json.loads(os.environ.pop(_RECOVERY_ENV, "null")),
            None,
        )
        if not pending:
            return
        path = Path(pending["path"])
        with _config_file_lock(path):
            if hashlib.sha256(path.read_bytes()).hexdigest() != pending["revision"]:
                raise RuntimeError(
                    "ov.conf changed after restart; refusing to overwrite external edits"
                )
            backup = Path(pending["backup"])
            _atomic_write(path, backup.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            backup.unlink()
        os.environ[_ROLLED_BACK_ENV] = "1"
        print(
            "Startup failed; restored the previous running configuration and retrying once",
            file=sys.stderr,
        )
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


class RestartController:
    def __init__(
        self, shutdown: Callable[[], None] | None = None, recovery: RestartRecovery | None = None
    ):
        self.instance_id = uuid4().hex
        self.shutdown = shutdown
        self.requested = False
        self.lock = asyncio.Lock()
        self.recovery = recovery

    def status(self) -> dict:
        return {
            "supported": self.shutdown is not None,
            "instance_id": self.instance_id,
            "restarting": self.requested,
            "rolled_back": bool(self.recovery and self.recovery.rolled_back),
        }

    def request(self) -> dict:
        if self.shutdown is None:
            raise FailedPreconditionError(
                "Remote restart requires the single-worker openviking-server CLI"
            )
        self.requested = True
        return self.status()

    def stop(self) -> None:
        if self.requested and self.shutdown is not None:
            self.shutdown()
