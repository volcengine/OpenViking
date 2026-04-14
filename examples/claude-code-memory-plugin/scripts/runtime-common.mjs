import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { access, copyFile, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

export const INSTALL_TIMEOUT_MS = 120000;

const LOCK_STALE_MS = 10 * 60 * 1000;
const FALLBACK_PLUGIN_DATA_ROOT = join(homedir(), ".openviking", "claude-code-memory-plugin");
const RUNTIME_ENV_META_PATH = ".runtime-env.json";

export function getRuntimePaths() {
  const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT;
  const pluginDataRoot = process.env.CLAUDE_PLUGIN_DATA || FALLBACK_PLUGIN_DATA_ROOT;

  if (!pluginRoot) throw new Error("CLAUDE_PLUGIN_ROOT is not set");

  const runtimeRoot = join(pluginDataRoot, "runtime");

  return {
    pluginRoot,
    pluginDataRoot,
    runtimeRoot,
    sourcePackagePath: join(pluginRoot, "package.json"),
    sourceLockPath: join(pluginRoot, "package-lock.json"),
    sourceServerPath: join(pluginRoot, "servers", "memory-server.js"),
    runtimePackagePath: join(runtimeRoot, "package.json"),
    runtimeLockPath: join(runtimeRoot, "package-lock.json"),
    runtimeServerPath: join(runtimeRoot, "servers", "memory-server.js"),
    runtimeNodeModulesPath: join(runtimeRoot, "node_modules"),
    statePath: join(runtimeRoot, "install-state.json"),
    lockDir: join(runtimeRoot, ".install-lock"),
    envMetaPath: join(runtimeRoot, RUNTIME_ENV_META_PATH),
    usingFallbackPluginData: !process.env.CLAUDE_PLUGIN_DATA,
  };
}

export async function computeSourceState(paths) {
  const [pkgRaw, lockRaw, serverRaw] = await Promise.all([
    readFile(paths.sourcePackagePath),
    readFile(paths.sourceLockPath),
    readFile(paths.sourceServerPath),
  ]);

  const pkg = JSON.parse(pkgRaw.toString("utf8"));

  return {
    pluginVersion: typeof pkg.version === "string" ? pkg.version : "0.0.0",
    manifestHash: sha256(pkgRaw, lockRaw),
    serverHash: sha256(serverRaw),
  };
}

export async function loadInstallState(paths) {
  try {
    return JSON.parse(await readFile(paths.statePath, "utf8"));
  } catch {
    return null;
  }
}

export async function writeInstallState(paths, state) {
  await mkdir(paths.runtimeRoot, { recursive: true });
  await writeFile(
    paths.statePath,
    JSON.stringify(
      {
        ...state,
        updatedAt: new Date().toISOString(),
      },
      null,
      2,
    ) + "\n",
  );
}

export async function writeRuntimeEnvMeta(paths) {
  await mkdir(paths.runtimeRoot, { recursive: true });
  await writeFile(
    paths.envMetaPath,
    JSON.stringify(
      {
        pluginDataRoot: paths.pluginDataRoot,
        runtimeRoot: paths.runtimeRoot,
        usingFallbackPluginData: paths.usingFallbackPluginData,
        updatedAt: new Date().toISOString(),
      },
      null,
      2,
    ) + "\n",
  );
}

export async function runtimeIsReady(paths, expectedState) {
  const state = await loadInstallState(paths);
  if (!state || state.status !== "ready") return false;
  if (state.manifestHash !== expectedState.manifestHash) return false;
  if (state.serverHash !== expectedState.serverHash) return false;

  for (const target of [
    paths.runtimePackagePath,
    paths.runtimeLockPath,
    paths.runtimeServerPath,
    paths.runtimeNodeModulesPath,
  ]) {
    if (!(await pathExists(target))) return false;
  }

  return true;
}

export async function syncRuntimeFiles(paths) {
  await mkdir(join(paths.runtimeRoot, "servers"), { recursive: true });
  await copyFile(paths.sourcePackagePath, paths.runtimePackagePath);
  await copyFile(paths.sourceLockPath, paths.runtimeLockPath);
  await copyFile(paths.sourceServerPath, paths.runtimeServerPath);
  await writeRuntimeEnvMeta(paths);
}

export async function acquireInstallLock(paths, timeoutMs = INSTALL_TIMEOUT_MS) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    await mkdir(paths.runtimeRoot, { recursive: true });

    try {
      await mkdir(paths.lockDir);
      await writeFile(
        join(paths.lockDir, "owner.json"),
        JSON.stringify({
          pid: process.pid,
          createdAt: new Date().toISOString(),
        }) + "\n",
      );
      return async () => {
        await rm(paths.lockDir, { recursive: true, force: true });
      };
    } catch (err) {
      if (err?.code !== "EEXIST") throw err;

      if (await isStaleLock(paths.lockDir)) {
        await rm(paths.lockDir, { recursive: true, force: true });
        continue;
      }

      await wait(500);
    }
  }

  throw new Error(`Timed out waiting for install lock in ${paths.runtimeRoot}`);
}

export async function waitForRuntime(paths, expectedState, options = {}) {
  const timeoutMs = options.timeoutMs ?? INSTALL_TIMEOUT_MS;
  const pollMs = options.pollMs ?? 500;
  const graceMs = options.graceMs ?? 5000;
  const startedAt = Date.now();
  let sawLock = await pathExists(paths.lockDir);

  while (Date.now() - startedAt < timeoutMs) {
    if (await runtimeIsReady(paths, expectedState)) return true;

    const lockExists = await pathExists(paths.lockDir);
    sawLock ||= lockExists;

    if (!sawLock && Date.now() - startedAt >= graceMs) return false;

    await wait(pollMs);
  }

  return runtimeIsReady(paths, expectedState);
}

export function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function ensureRuntimeInstalled(paths, expectedState) {
  if (await runtimeIsReady(paths, expectedState)) return false;

  const releaseLock = await acquireInstallLock(paths, INSTALL_TIMEOUT_MS);

  try {
    if (await runtimeIsReady(paths, expectedState)) return false;

    await syncRuntimeFiles(paths);
    const result = spawnSync(getNpmCommand(), installArgs(), {
      cwd: paths.runtimeRoot,
      encoding: "utf8",
      stdio: "pipe",
      shell: process.platform === "win32",
    });

    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(formatInstallFailure(result));

    await writeInstallState(paths, {
      status: "ready",
      pluginVersion: expectedState.pluginVersion,
      manifestHash: expectedState.manifestHash,
      serverHash: expectedState.serverHash,
      pluginDataRoot: paths.pluginDataRoot,
      usingFallbackPluginData: paths.usingFallbackPluginData,
    });

    return true;
  } catch (err) {
    await rm(paths.runtimeNodeModulesPath, { recursive: true, force: true });

    await writeInstallState(paths, {
      status: "error",
      pluginVersion: expectedState.pluginVersion,
      manifestHash: expectedState.manifestHash,
      serverHash: expectedState.serverHash,
      pluginDataRoot: paths.pluginDataRoot,
      usingFallbackPluginData: paths.usingFallbackPluginData,
      error: err instanceof Error ? err.message : String(err),
    });

    throw err;
  } finally {
    await releaseLock();
  }
}

async function pathExists(target) {
  try {
    await access(target, fsConstants.F_OK);
    return true;
  } catch {
    return false;
  }
}

async function isStaleLock(lockDir) {
  try {
    const info = await stat(lockDir);
    return Date.now() - info.mtimeMs > LOCK_STALE_MS;
  } catch {
    return false;
  }
}

function sha256(...buffers) {
  const hash = createHash("sha256");
  for (const buf of buffers) hash.update(buf);
  return hash.digest("hex");
}

function getNpmCommand() {
  return process.platform === "win32" ? "npm.cmd" : "npm";
}

function installArgs() {
  return ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"];
}

function formatInstallFailure(result) {
  const lines = [
    `npm ci exited with status ${result.status ?? "unknown"}`,
    trimOutput(result.stderr),
    trimOutput(result.stdout),
  ].filter(Boolean);

  return lines.join("\n");
}

function trimOutput(output) {
  if (!output) return "";
  const text = output.trim();
  if (!text) return "";

  const maxChars = 4000;
  if (text.length <= maxChars) return text;
  return text.slice(-maxChars);
}
