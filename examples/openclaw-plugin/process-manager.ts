import { readFileSync, existsSync } from "node:fs";
import { Socket } from "node:net";
import { platform } from "node:os";
import { launchProcess, runSync, sysEnv, getEnv } from "./runtime-utils.js";

export const IS_WIN = platform() === "win32";

export function waitForHealth(baseUrl: string, timeoutMs: number, intervalMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (Date.now() > deadline) {
        reject(new Error(`OpenViking health check timeout at ${baseUrl}`));
        return;
      }
      fetch(`${baseUrl}/health`)
        .then((r) => r.json())
        .then((body: { status?: string }) => {
          if (body?.status === "ok") {
            resolve();
            return;
          }
          setTimeout(tick, intervalMs);
        })
        .catch(() => setTimeout(tick, intervalMs));
    };
    tick();
  });
}

export function waitForHealthOrExit(
  baseUrl: string,
  timeoutMs: number,
  intervalMs: number,
  child: ReturnType<typeof launchProcess>,
): Promise<void> {
  const exited =
    child.killed || child.exitCode !== null || child.signalCode !== null;
  if (exited) {
    return Promise.reject(
      new Error(
        `OpenViking subprocess exited before health check ` +
          `(code=${child.exitCode}, signal=${child.signalCode})`,
      ),
    );
  }

  return new Promise((resolve, reject) => {
    let settled = false;

    const cleanup = () => {
      child.off?.("error", onError);
      child.off?.("exit", onExit);
    };

    const finishResolve = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolve();
    };

    const finishReject = (err: unknown) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(err instanceof Error ? err : new Error(String(err)));
    };

    const onError = (err: Error) => {
      finishReject(err);
    };

    const onExit = (code: number | null, signal: string | null) => {
      finishReject(
        new Error(
          `OpenViking subprocess exited before health check ` +
            `(code=${code}, signal=${signal})`,
        ),
      );
    };

    child.once("error", onError);
    child.once("exit", onExit);
    waitForHealth(baseUrl, timeoutMs, intervalMs).then(finishResolve, finishReject);
  });
}

export function withTimeout<T>(promise: Promise<T>, timeoutMs: number, timeoutMessage: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

export function quickTcpProbe(host: string, port: number, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new Socket();
    let done = false;
    const finish = (ok: boolean) => {
      if (done) {
        return;
      }
      done = true;
      socket.destroy();
      resolve(ok);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    try {
      socket.connect(port, host);
    } catch {
      finish(false);
    }
  });
}

export async function quickHealthCheck(baseUrl: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}/health`, {
      method: "GET",
      signal: controller.signal,
    });
    if (!response.ok) {
      return false;
    }
    const body = (await response.json().catch(() => ({}))) as { status?: string };
    return body.status === "ok";
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function quickRecallPrecheck(
  mode: "local" | "remote",
  baseUrl: string,
  defaultPort: number,
  localProcess: ReturnType<typeof launchProcess> | null,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  const healthOk = await quickHealthCheck(baseUrl, 500);
  if (healthOk) {
    return { ok: true };
  }

  let host = "127.0.0.1";
  let port = defaultPort;
  try {
    const parsed = new URL(baseUrl);
    if (parsed.hostname) {
      host = parsed.hostname;
    }
    if (parsed.port) {
      const parsedPort = Number(parsed.port);
      if (Number.isFinite(parsedPort) && parsedPort > 0) {
        port = parsedPort;
      }
    }
  } catch {
    // Keep defaults when baseUrl is malformed.
  }

  if (mode === "local") {
    const portOk = await quickTcpProbe(host, port, 200);
    if (!portOk) {
      return { ok: false, reason: `local port unavailable (${host}:${port})` };
    }
    if (localProcess && (localProcess.killed || localProcess.exitCode !== null || localProcess.signalCode !== null)) {
      return { ok: false, reason: "local process is not running" };
    }
    if (localProcess === null) {
      return { ok: true };
    }
  }
  return { ok: false, reason: "health check failed" };
}

export interface ProcessLogger {
  info?: (msg: string) => void;
  warn?: (msg: string) => void;
}

/**
 * Prepare a port for local OpenViking startup.
 *
 * 1. If the port hosts an OpenViking instance (health check passes) → kill it, return same port.
 * 2. If the port is occupied by something else → auto-find the next free port.
 * 3. If the port is free → return it as-is.
 */
export async function prepareLocalPort(
  port: number,
  logger: ProcessLogger,
  maxRetries: number = 10,
): Promise<number> {
  const isOpenViking = await quickHealthCheck(`http://127.0.0.1:${port}`, 2000);
  if (isOpenViking) {
    logger.info?.(`openviking: killing stale OpenViking on port ${port}`);
    await killProcessOnPort(port, logger);
    return port;
  }

  const occupied = await quickTcpProbe("127.0.0.1", port, 500);
  if (!occupied) {
    return port;
  }

  // Port occupied by non-OpenViking process — find next free port
  logger.warn?.(`openviking: port ${port} is occupied by another process, searching for a free port...`);
  for (let candidate = port + 1; candidate <= port + maxRetries; candidate++) {
    if (candidate > 65535) break;
    const taken = await quickTcpProbe("127.0.0.1", candidate, 300);
    if (!taken) {
      logger.info?.(`openviking: using free port ${candidate} instead of ${port}`);
      return candidate;
    }
  }
  throw new Error(
    `openviking: port ${port} is occupied and no free port found in range ${port + 1}-${port + maxRetries}`,
  );
}

function killProcessOnPort(port: number, logger: ProcessLogger): Promise<void> {
  return IS_WIN ? killProcessOnPortWin(port, logger) : killProcessOnPortUnix(port, logger);
}

async function killProcessOnPortWin(port: number, logger: ProcessLogger): Promise<void> {
  try {
    const netstatOut = runSync(
      `netstat -ano | findstr "LISTENING" | findstr ":${port}"`,
      { encoding: "utf-8", shell: "cmd.exe" },
    ).trim();
    if (!netstatOut) return;
    const pids = new Set<number>();
    for (const line of netstatOut.split(/\r?\n/)) {
      const m = line.trim().match(/\s(\d+)\s*$/);
      if (m) pids.add(Number(m[1]));
    }
    for (const pid of pids) {
      if (pid > 0) {
        logger.info?.(`openviking: killing pid ${pid} on port ${port}`);
        try { runSync(`taskkill /PID ${pid} /F`, { encoding: "utf-8", shell: "cmd.exe" }); } catch { /* already gone */ }
      }
    }
    if (pids.size) await new Promise((r) => setTimeout(r, 500));
  } catch { /* netstat not available or no stale process */ }
}

async function killProcessOnPortUnix(port: number, logger: ProcessLogger): Promise<void> {
  try {
    let pids: number[] = [];
    try {
      const lsofOut = runSync(`lsof -ti tcp:${port} -s tcp:listen 2>/dev/null || true`, {
        encoding: "utf-8",
        shell: "/bin/sh",
      }).trim();
      if (lsofOut) pids = lsofOut.split(/\s+/).map((s) => Number(s)).filter((n) => n > 0);
    } catch { /* lsof not available */ }
    if (pids.length === 0) {
      try {
        const ssOut = runSync(
          `ss -tlnp 2>/dev/null | awk -v p=":${port}" '$4 ~ p {gsub(/.*pid=/,""); gsub(/,.*/,""); print; exit}'`,
          { encoding: "utf-8", shell: "/bin/sh" },
        ).trim();
        if (ssOut) {
          const n = Number(ssOut);
          if (n > 0) pids = [n];
        }
      } catch { /* ss not available */ }
    }
    for (const pid of pids) {
      logger.info?.(`openviking: killing pid ${pid} on port ${port}`);
      try { globalThis["process"].kill(pid, "SIGKILL"); } catch { /* already gone */ }
    }
    if (pids.length) await new Promise((r) => setTimeout(r, 500));
  } catch { /* port check failed */ }
}

export function resolvePythonCommand(logger: ProcessLogger): string {
  const defaultPy = IS_WIN ? "python" : "python3";
  let pythonCmd = getEnv("OPENVIKING_PYTHON");

  if (!pythonCmd) {
    const { join } = require("node:path") as typeof import("node:path");
    const { homedir } = require("node:os") as typeof import("node:os");
    const defaultDir = join(homedir(), ".openclaw");
    const profileDir = getEnv("OPENCLAW_STATE_DIR");
    const searchDirs = profileDir && profileDir !== defaultDir
      ? [profileDir, defaultDir]
      : [defaultDir];
    for (const dir of searchDirs) {
      if (pythonCmd) break;
      if (IS_WIN) {
        const envBat = join(dir, "openviking.env.bat");
        if (existsSync(envBat)) {
          try {
            const content = readFileSync(envBat, "utf-8");
            const m = content.match(/set\s+OPENVIKING_PYTHON=(.+)/i);
            if (m?.[1]) pythonCmd = m[1].trim();
          } catch { /* ignore */ }
        }
      } else {
        const envFile = join(dir, "openviking.env");
        if (existsSync(envFile)) {
          try {
            const content = readFileSync(envFile, "utf-8");
            const m = content.match(/OPENVIKING_PYTHON=['"]([^'"]+)['"]/);
            if (m?.[1]) pythonCmd = m[1];
          } catch { /* ignore */ }
        }
      }
    }
  }

  if (!pythonCmd) {
    if (IS_WIN) {
      try {
        pythonCmd = runSync("where python", { encoding: "utf-8", shell: "cmd.exe" }).split(/\r?\n/)[0].trim();
      } catch {
        pythonCmd = "python";
      }
    } else {
      try {
        pythonCmd = runSync("command -v python3 || which python3", {
          encoding: "utf-8",
          env: sysEnv(),
          shell: "/bin/sh",
        }).trim();
      } catch {
        pythonCmd = "python3";
      }
    }
  }

  if (pythonCmd === defaultPy) {
    logger.info?.(
      `openviking: 未解析到 ${defaultPy} 路径，将用 "${defaultPy}"。若 openviking 在自定义 Python 下，请设置 OPENVIKING_PYTHON` +
      (IS_WIN ? ' 或 call "%USERPROFILE%\\.openclaw\\openviking.env.bat"' : " 或 source ~/.openclaw/openviking.env"),
    );
  }

  return pythonCmd;
}

// ---------------------------------------------------------------------------
// Local runtime pre-flight check (detect only, never auto-install)
// ---------------------------------------------------------------------------

function checkPythonVersion(pythonCmd: string): { ok: boolean; version?: string; error?: string } {
  try {
    const out = runSync(
      `"${pythonCmd}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"`,
      { encoding: "utf-8", shell: IS_WIN ? "cmd.exe" : "/bin/sh" },
    ).trim();
    const [major, minor] = out.split(".").map(Number);
    if (major < 3 || (major === 3 && minor < 10)) {
      return { ok: false, error: `Python ${out} too old, need >= 3.10` };
    }
    return { ok: true, version: out };
  } catch {
    return { ok: false, error: "Python not found or failed to execute" };
  }
}

function isOpenVikingImportable(pythonCmd: string): boolean {
  try {
    runSync(`"${pythonCmd}" -c "import openviking"`, {
      encoding: "utf-8",
      shell: IS_WIN ? "cmd.exe" : "/bin/sh",
    });
    return true;
  } catch {
    return false;
  }
}

export interface CheckRuntimeResult {
  pythonCmd: string;
  installed: boolean;
  configExists: boolean;
}

/**
 * Pre-startup check for local mode: verify Python >= 3.10 and that the
 * openviking package is importable. Does NOT auto-install anything — if
 * the package is missing, the caller should warn the user to install it
 * manually (e.g. `pip install openviking`).
 */
export function checkLocalRuntime(
  pythonCmd: string,
  configPath: string,
  logger: ProcessLogger,
): CheckRuntimeResult {
  const pyCheck = checkPythonVersion(pythonCmd);
  if (!pyCheck.ok) {
    logger.warn?.(`openviking: runtime check — ${pyCheck.error}`);
    return { pythonCmd, installed: false, configExists: existsSync(configPath) };
  }
  logger.info?.(`openviking: Python ${pyCheck.version} ✓`);

  const installed = isOpenVikingImportable(pythonCmd);
  if (installed) {
    logger.info?.("openviking: package detected ✓");
  }

  return { pythonCmd, installed, configExists: existsSync(configPath) };
}
