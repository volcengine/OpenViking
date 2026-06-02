"""
openviking-watchdog.py  v2.1
Monitors the OpenViking REST server and restarts it on failure.

Features:
  - Checks server health every 15 seconds
  - Restarts after 5 consecutive failures
  - Restart counter resets after 10 min of stable uptime (never permanently exits)
  - Runs embedding selector before each restart (Ollama or Jina fallback)
  - Embedding selector uses -NoProfile to avoid slow PowerShell startup

Usage:
  python openviking-watchdog.py

Register as a Windows Scheduled Task at logon using register-ov-watchdog.ps1.
"""
import subprocess
import time
import sys
import os
import logging
import urllib.request
from datetime import datetime

# ── Config (edit paths to match your setup) ───────────────────────────────────
OV_URL            = "http://localhost:1933/health"
OV_API_KEY        = os.environ.get("OV_API_KEY", "YOUR_LOCAL_API_KEY")
CHECK_INTERVAL    = 15    # seconds between health checks
RESTART_DELAY     = 5     # seconds after kill before restarting
STARTUP_WAIT      = 25    # seconds to wait for server after restart
FAILURE_THRESHOLD = 5     # consecutive failures before restart
EMBED_TIMEOUT     = 45    # seconds for PowerShell embedding selector
STABLE_RESET_SECS = 600   # reset restart_count after 10 min of stable uptime

# Edit these paths to match your installation
BASE_DIR     = os.path.join(os.environ["USERPROFILE"], ".openviking")
LOG_DIR      = os.path.join(os.environ["USERPROFILE"], ".claude-memory", "logs")
PYTHON_EXE   = os.path.join(os.environ["USERPROFILE"],
                            "AppData", "Local", "Programs", "Python",
                            "Python313", "python.exe")
WATCHDOG_LOG = os.path.join(LOG_DIR, "openviking-watchdog.log")

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(WATCHDOG_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("ov-watchdog")

ov_process    = None
restart_count = 0
last_restart  = 0.0
stable_since  = time.time()


def is_server_up():
    try:
        req = urllib.request.Request(
            OV_URL,
            headers={"Authorization": "Bearer " + OV_API_KEY}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def start_server():
    global ov_process
    log.info("Starting OpenViking server...")

    # Run embedding selector before each start
    select_script = os.path.join(BASE_DIR, "select-embedding.ps1")
    if os.path.exists(select_script):
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", select_script],
                timeout=EMBED_TIMEOUT,
                capture_output=True
            )
            log.info("Embedding provider selected.")
        except subprocess.TimeoutExpired:
            log.warning("Embedding selector timed out (%ds) -- using existing ov.conf", EMBED_TIMEOUT)
        except Exception as e:
            log.warning("Embedding selector failed (%s) -- using existing ov.conf", e)

    log_stdout = os.path.join(LOG_DIR, "openviking-server-" + datetime.now().strftime("%Y%m%d") + ".log")
    log_stderr = os.path.join(LOG_DIR, "openviking-server-" + datetime.now().strftime("%Y%m%d") + "-err.log")

    ov_process = subprocess.Popen(
        [PYTHON_EXE, "-m", "openviking_cli.server_bootstrap"],
        cwd=BASE_DIR,
        stdout=open(log_stdout, "a", encoding="utf-8"),
        stderr=open(log_stderr, "a", encoding="utf-8"),
        env=dict(os.environ, OPENVIKING_CONFIG=os.path.join(BASE_DIR, "ov.conf"))
    )
    log.info("Server PID: %d  Log: %s", ov_process.pid, log_stdout)


def stop_server():
    global ov_process
    if ov_process and ov_process.poll() is None:
        log.info("Terminating server PID %d", ov_process.pid)
        ov_process.terminate()
        try:
            ov_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("Terminate timed out -- killing")
            ov_process.kill()
        ov_process = None


def restart_server(reason):
    global restart_count, last_restart, stable_since
    restart_count += 1
    last_restart   = time.time()
    stable_since   = time.time()
    log.warning("Restart #%d triggered: %s", restart_count, reason)
    stop_server()
    time.sleep(RESTART_DELAY)
    start_server()
    log.info("Waiting %ds for server to initialize...", STARTUP_WAIT)
    time.sleep(STARTUP_WAIT)
    if is_server_up():
        log.info("Server restarted successfully (restart #%d).", restart_count)
    else:
        log.error("Server did not come up after restart #%d. Will keep trying.", restart_count)


def main():
    global restart_count, stable_since
    log.info("OpenViking Watchdog v2.1 starting.")
    log.info("Monitoring: %s  Interval: %ds  EmbedTimeout: %ds", OV_URL, CHECK_INTERVAL, EMBED_TIMEOUT)

    if not is_server_up():
        log.info("Server not running on startup -- starting it.")
        start_server()
        time.sleep(STARTUP_WAIT)
        if not is_server_up():
            log.error("Server failed to start on initial attempt. Will keep monitoring.")

    consecutive_failures = 0

    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            # Reset restart counter after sustained stable uptime
            if restart_count > 0 and (time.time() - stable_since) > STABLE_RESET_SECS:
                log.info("10 min stable uptime -- resetting restart counter (was %d).", restart_count)
                restart_count = 0

            if ov_process and ov_process.poll() is not None:
                exit_code = ov_process.poll()
                restart_server("process exited with code " + str(exit_code))
                consecutive_failures = 0
                stable_since = time.time()
                continue

            if not is_server_up():
                consecutive_failures += 1
                log.warning("Health check failed (%d/%d)", consecutive_failures, FAILURE_THRESHOLD)
                if consecutive_failures >= FAILURE_THRESHOLD:
                    restart_server(str(FAILURE_THRESHOLD) + " consecutive health check failures")
                    consecutive_failures = 0
                    stable_since = time.time()
            else:
                if consecutive_failures > 0:
                    log.info("Server recovered after %d failures.", consecutive_failures)
                    stable_since = time.time()
                consecutive_failures = 0

    except KeyboardInterrupt:
        log.info("Watchdog stopped by user.")
        stop_server()
        sys.exit(0)


if __name__ == "__main__":
    main()
