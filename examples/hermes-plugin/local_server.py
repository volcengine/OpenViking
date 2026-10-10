"""Process ownership and lifecycle for one profile's Quick Local server."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psutil
from utils import atomic_json_write


@dataclass(frozen=True)
class StartedServer:
    endpoint: str
    process: object | None
    reused: bool
    created: float | None = None


class _RecordedProcess:
    def __init__(self, process):
        self.process = process
        self.created = process.create_time()
        self.pid = process.pid

    def poll(self):
        try:
            if self.process.create_time() != self.created:
                return -1
            return (
                None
                if self.process.is_running() and self.process.status() != psutil.STATUS_ZOMBIE
                else -1
            )
        except psutil.NoSuchProcess:
            return -1


def _wait_stopped(processes, timeout):
    # A separate CLI cannot reap Hermes's children. A zombie has already
    # stopped; wait_procs() can otherwise keep reporting it as alive.
    deadline = time.monotonic() + timeout
    while True:
        alive = []
        for process in processes:
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    alive.append(process)
            except psutil.NoSuchProcess:
                pass
        if not alive or time.monotonic() >= deadline:
            return alive
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        processes = alive


def stop_process_tree(process, *, timeout=5):
    """Stop an owned parent and its existing children, including Windows launchers."""
    from .quick_local import QuickLocalSetupError

    try:
        processes = [*process.children(recursive=True), process]
        for child in processes:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        alive = _wait_stopped(processes, timeout)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        alive = _wait_stopped(alive, timeout)
        if alive:
            raise QuickLocalSetupError(
                "Quick Local could not stop its server. Review the server log."
            )
    except psutil.NoSuchProcess:
        pass


class LocalServer:
    """Never stop a process unless its identity and profile config match."""

    def __init__(self, hermes_home: Path):
        from . import quick_local as ql

        self.ql = ql
        self.paths = ql.managed_paths(hermes_home)
        self.record_path = self.paths.root / "server-process.json"

    @contextmanager
    def locked(self, timeout=10.0):
        ql = self.ql
        ql.repair_private_paths(self.paths)
        path = self.paths.root / "server.lock"
        if path.is_symlink():
            raise ql.QuickLocalSetupError("Quick Local lock must belong to this profile.")
        with path.open("a+b") as file:
            if os.name != "nt":
                path.chmod(0o600)
            file.seek(0, 2)
            if not file.tell():
                file.write(b"\0")
                file.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    file.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ql.QuickLocalSetupError(
                            "Quick Local is busy in another process. Retry shortly."
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    def _config(self):
        self.ql.connection_config({}, self.paths.root.parent)
        return json.loads(self.paths.server_config.read_text(encoding="utf-8"))

    def _fingerprint(self, config):
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def _read_record(self):
        try:
            value = json.loads(self.record_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _restart_stamp(self):
        try:
            return self.paths.restart_required_marker.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _matches_desired(self, record, config):
        return (
            record.get("fingerprint") == self._fingerprint(config)
            and self.ql._paths_equivalent(record.get("command"), self.paths.server_command)
            and (
                self._restart_stamp() is None
                or record.get("restart_stamp") == self._restart_stamp()
            )
        )

    def _verified_process(self, record):
        try:
            process = psutil.Process(record["pid"])
            if (
                process.create_time() != record["created"]
                or process.status() == psutil.STATUS_ZOMBIE
            ):
                return None
            args = process.cmdline()
            config = args[args.index("--config") + 1]
            command = Path(record["command"]).absolute()
            # PM generations can change during an upgrade. The old command must
            # still be inside this profile's private runtime, never another one.
            command.relative_to(self.paths.runtime.absolute())
            if not self.ql._paths_equivalent(config, self.paths.server_config):
                return None
            if not any(self.ql._paths_equivalent(arg, command) for arg in args[:2]):
                return None
            if args[args.index("--port") + 1] != str(record["port"]):
                return None
            return process
        except (psutil.Error, KeyError, ValueError, IndexError, TypeError, OSError):
            return None

    def _adopt(self, config, endpoint):
        # Older Quick Local builds did not save a process record. Authenticated
        # ownership plus an exact executable/config match permits adoption.
        if not self.ql.server_belongs_to_profile(self.paths, endpoint):
            return None
        for process in psutil.process_iter(["pid", "create_time", "cmdline"]):
            try:
                args = process.info["cmdline"] or []
                # A runtime upgrade may select a new PM generation before
                # setup recovers the OLD server's lost record. Adopt only an
                # OpenViking entrypoint inside this profile's private runtime.
                command = next(
                    (
                        arg
                        for arg in args[:2]
                        if Path(arg).name
                        in {"openviking-server", "openviking-server.exe", "openviking-server.cmd"}
                        and Path(arg).absolute().is_relative_to(self.paths.runtime.absolute())
                    ),
                    None,
                )
                if command is None:
                    continue
                record = {
                    "pid": process.pid,
                    "created": process.info["create_time"],
                    "command": command,
                    "port": self.ql._endpoint_port(endpoint),
                    "fingerprint": self._fingerprint(config),
                }
                if "--config" in args and self._verified_process(record):
                    atomic_json_write(self.record_path, record, mode=0o600)
                    return process
            except psutil.Error:
                continue
        return None

    def _stop(self, process):
        stop_process_tree(process)

    def configure(self, config, *, runtime_changed=False):
        with self.locked():
            changed = not self.ql._stored_server_config_matches(self.paths, config)
            if (changed or runtime_changed) and self.paths.server_config.is_file():
                # Authenticate/adopt against the OLD config. Publishing a new
                # key first makes a lost process record impossible to recover.
                # Setup can repair an incomplete ovcli file; only the old
                # server config/key is needed for authenticated adoption.
                old_config = json.loads(self.paths.server_config.read_text(encoding="utf-8"))
                endpoint = self.ql._configured_endpoint(self.paths)
                process = self._verified_process(self._read_record())
                if process is None:
                    process = self._adopt(old_config, endpoint)
                if process is None and self.ql.server_belongs_to_profile(self.paths, endpoint):
                    raise self.ql.QuickLocalSetupError(
                        "Quick Local cannot verify the server process. Existing settings were retained. "
                        "Stop that server manually, then retry."
                    )
            if changed or runtime_changed:
                self.ql._mark_server_restart_required(self.paths)
            atomic_json_write(self.paths.server_config, config, mode=0o600)
            self.ql._write_ovcli_profile(
                self.paths.ovcli_config, f"http://127.0.0.1:{config['server']['port']}", config
            )

    def is_running(self, endpoint):
        """Cheap owned-process check for a retained provider's cached client."""
        record = self._read_record()
        if record.get("port") == self.ql._endpoint_port(endpoint) and self._verified_process(
            record
        ):
            return True
        # A server started outside the controller may have no record. Keep
        # serving from it while its key still proves profile ownership.
        return self.ql.server_belongs_to_profile(self.paths, endpoint)

    def start(self, *, restart=False):
        ql = self.ql
        with self.locked():
            config = self._config()
            endpoint = ql._configured_endpoint(self.paths)
            record = self._read_record()
            process = self._verified_process(record)
            if process is None:
                process = self._adopt(config, endpoint)
                record = self._read_record()
            changed = (
                not self._matches_desired(record, config)
                if process is not None
                else self.paths.restart_required_marker.is_file()
            )
            if process is not None:
                if not restart and not changed:
                    return StartedServer(
                        endpoint, _RecordedProcess(process), True, process.create_time()
                    )
                self._stop(process)
            elif ql.server_belongs_to_profile(self.paths, endpoint):
                if changed or restart:
                    raise ql.QuickLocalSetupError(
                        "Quick Local cannot verify the server process. Stop that server manually, then retry."
                    )
                return StartedServer(endpoint, None, True)

            self.record_path.unlink(missing_ok=True)
            port = ql.find_available_port(preferred_endpoint=endpoint)
            if port is None:
                raise ql.QuickLocalSetupError("No free Quick Local port was found (1934-1953).")
            endpoint = f"http://127.0.0.1:{port}"
            config["server"]["port"] = port
            atomic_json_write(self.paths.server_config, config, mode=0o600)
            ql._write_ovcli_profile(self.paths.ovcli_config, endpoint, config)
            process = ql._start_validation_server(
                endpoint,
                self.paths.server_config,
                self.paths.root.parent,
                self.paths.server_command,
            )
            try:
                created = psutil.Process(process.pid).create_time()
                atomic_json_write(
                    self.record_path,
                    {
                        "pid": process.pid,
                        "created": created,
                        "command": str(self.paths.server_command),
                        "port": port,
                        "restart_stamp": self._restart_stamp(),
                        "fingerprint": self._fingerprint(config),
                    },
                    mode=0o600,
                )
            except BaseException:
                ql._stop_process(process)
                raise
            return StartedServer(endpoint, process, False, created)

    def wait_ready(self, started, health_check, *, should_stop=lambda: False, timeout=None):
        """Retry a port race after an early exit, without stopping another server."""
        ql = self.ql
        deadline = time.monotonic() + (ql._HEALTH_TIMEOUT_SECONDS if timeout is None else timeout)
        for attempt in range(3):
            while time.monotonic() < deadline and not should_stop():
                if started.process is not None and started.process.poll() is not None:
                    break
                healthy, _message = health_check(started.endpoint)
                if healthy and ql.server_belongs_to_profile(self.paths, started.endpoint):
                    with self.locked():
                        # Do not clear a restart requested by a concurrent setup.
                        record = self._read_record()
                        current = self._matches_desired(record, self._config())
                        if started.process is not None:
                            current = (
                                current
                                and record.get("pid") == started.process.pid
                                and record.get("created") == started.created
                            )
                        if current:
                            ql.clear_server_restart_required(self.paths.server_config)
                        elif (
                            not self.paths.restart_required_marker.is_file()
                            and started.process is None
                        ):
                            current = True
                    if current:
                        return started
                    # Setup changed the saved config during this health probe.
                    # Reconcile it before publishing a stale running server.
                    started = self.start()
                    continue
                time.sleep(ql._HEALTH_POLL_INTERVAL_SECONDS)
            if should_stop():
                return None
            host, port = ql._endpoint_bind(started.endpoint)
            if (
                attempt == 2
                or ql._can_bind_local_port(host, port)
                or ql.server_belongs_to_profile(self.paths, started.endpoint)
            ):
                break
            started = self.start()
        # A failed start must not leave a hung child behind. Recheck the record
        # under the lock so a newer start by another caller is never stopped.
        with self.locked():
            record = self._read_record()
            if (
                started.process is not None
                and record.get("pid") == started.process.pid
                and record.get("created") == started.created
            ):
                process = self._verified_process(record)
                if process is not None:
                    self._stop(process)
                self.record_path.unlink(missing_ok=True)
        raise ql.QuickLocalSetupError(
            f"Quick Local did not become ready. Review {ql._server_log_path(self.paths.root.parent)} and retry."
        )

    def stop(self):
        with self.locked():
            config = self._config()
            endpoint = self.ql._configured_endpoint(self.paths)
            process = self._verified_process(self._read_record()) or self._adopt(config, endpoint)
            if process is None:
                if self.ql.server_belongs_to_profile(self.paths, endpoint):
                    raise self.ql.QuickLocalSetupError(
                        "Quick Local cannot verify the server process; it was not stopped."
                    )
                return False
            self._stop(process)
            self.record_path.unlink(missing_ok=True)
            return True

    def status(self):
        with self.locked():
            config = self._config()
            endpoint = self.ql._configured_endpoint(self.paths)
            process = self._verified_process(self._read_record()) or self._adopt(config, endpoint)
            ready = self.ql.server_belongs_to_profile(self.paths, endpoint)
            return {
                "endpoint": endpoint,
                "pid": process.pid if process else None,
                "state": "ready" if ready else "starting" if process else "stopped",
                "restart_required": self.paths.restart_required_marker.is_file(),
            }
