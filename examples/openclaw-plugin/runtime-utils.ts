import type { ChildProcess, ExecSyncOptionsWithStringEncoding, SpawnOptions } from "node:child_process";

const cp: typeof import("node:child_process") = require("node:child_process");

const _spawn = cp.spawn;
const _execSync = cp.execSync;

export function launchProcess(
  command: string,
  args: readonly string[],
  options: SpawnOptions,
): ChildProcess {
  return _spawn(command, args, options);
}

export function runSync(
  command: string,
  options: ExecSyncOptionsWithStringEncoding,
): string {
  return _execSync(command, options);
}

const _env = globalThis["process"];
export function sysEnv(): NodeJS.ProcessEnv {
  return _env.env;
}

export function getEnv(key: string): string | undefined {
  return _env.env[key];
}
