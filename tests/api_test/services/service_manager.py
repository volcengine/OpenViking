import subprocess
import threading
import time
from typing import Optional

import psutil
import requests
from config import Config


class OpenVikingServiceManager:
    def __init__(self):
        self.server_process: Optional[subprocess.Popen] = None
        self.server_output: list = []
        self._server_was_started_by_us: bool = False

    def _log_output(self, process, output_list, prefix):
        for line in iter(process.stdout.readline, ""):
            if line:
                line = line.rstrip()
                output_list.append(line)
                print(f"[{prefix}] {line}")
        for line in iter(process.stderr.readline, ""):
            if line:
                line = line.rstrip()
                output_list.append(line)
                print(f"[{prefix} ERROR] {line}")

    def _is_port_in_use(self, port: int, host: str = "127.0.0.1") -> bool:
        try:
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _wait_for_service(self, url: str, timeout: int) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code < 500:
                    return True
            except Exception:
                pass
            time.sleep(Config.SERVICE_CHECK_INTERVAL)
        return False

    def start_server(self) -> bool:
        if self._is_port_in_use(Config.SERVER_PORT):
            print(f"⚠️  Server port {Config.SERVER_PORT} already in use, verifying service...")
            if self._wait_for_service(Config.SERVER_URL, 5):
                print(f"✅ Server is already running on {Config.SERVER_URL}")
                self._server_was_started_by_us = False
                return True
            else:
                print(f"❌ Port {Config.SERVER_PORT} is in use but service is not responding")
                return False

        print(f"🚀 Starting OpenViking server on {Config.SERVER_URL}...")
        cmd = ["python", "-m", "openviking.server.bootstrap"]

        try:
            self.server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            threading.Thread(
                target=self._log_output,
                args=(self.server_process, self.server_output, "SERVER"),
                daemon=True,
            ).start()

            if self._wait_for_service(Config.SERVER_URL, Config.SERVER_STARTUP_TIMEOUT):
                print(f"✅ Server started successfully on {Config.SERVER_URL}")
                self._server_was_started_by_us = True
                return True
            else:
                print(f"❌ Failed to start server after {Config.SERVER_STARTUP_TIMEOUT} seconds")
                self._print_process_output("SERVER", self.server_output)
                self.stop_server()
                return False
        except Exception as e:
            print(f"❌ Error starting server: {e}")
            self.stop_server()
            return False

    def _print_process_output(self, name: str, output: list):
        if output:
            print(f"\n{'=' * 60}")
            print(f"{name} Output:")
            print("=" * 60)
            for line in output[-50:]:
                print(line)
            print("=" * 60 + "\n")

    def _find_process_by_port(self, port: int) -> Optional[psutil.Process]:
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    connections = proc.connections()
                    for conn in connections:
                        if conn.laddr.port == port:
                            return proc
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
        except Exception:
            pass
        return None

    def stop_server(self):
        if self.server_process:
            print("🛑 Stopping server (started by us)...")
            self._terminate_process_tree(self.server_process.pid)
            self.server_process = None
        elif self._is_port_in_use(Config.SERVER_PORT):
            print("🛑 Server was not started by us, but port is in use - trying to stop...")
            proc = self._find_process_by_port(Config.SERVER_PORT)
            if proc:
                print(
                    f"   Found process {proc.pid} ({proc.name()}) using port {Config.SERVER_PORT}"
                )
                self._terminate_process_tree(proc.pid)
        self._server_was_started_by_us = False

    def _terminate_process_tree(self, pid: int):
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            parent.terminate()
            gone, alive = psutil.wait_procs(children + [parent], timeout=5)
            for p in alive:
                p.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            print(f"⚠️  Error terminating process {pid}: {e}")

    def start_all(self) -> bool:
        print("\n" + "=" * 60)
        print("Starting OpenViking Services")
        print("=" * 60 + "\n")

        if not self.start_server():
            print("\n❌ Failed to start all services")
            return False

        print("\n✅ All services started successfully!")
        print("=" * 60 + "\n")
        return True

    def stop_all(self):
        print("\n" + "=" * 60)
        print("Stopping OpenViking Services")
        print("=" * 60 + "\n")
        self.stop_server()
        print("\n✅ All services stopped")
        print("=" * 60 + "\n")

    def __enter__(self):
        if not self.start_all():
            raise RuntimeError("Failed to start OpenViking services")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_all()
