/** Load index.ts under node --test with a clean OPENVIKING_* environment. */

import { registerHooks } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const EXTENSION_DIR = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const EXTENSION_URL = pathToFileURL(EXTENSION_DIR + "/").href;

// Only the extension's own relative `.js` specifiers are touched, and only
// after the real resolution has already failed, so nothing else in the process
// can change meaning because this file ran.
registerHooks({
  resolve(specifier, context, nextResolve) {
    const inExtension = typeof context?.parentURL === "string"
      && context.parentURL.startsWith(EXTENSION_URL);
    if (!inExtension || !specifier.startsWith(".") || !specifier.endsWith(".js")) {
      return nextResolve(specifier, context);
    }
    try {
      return nextResolve(specifier, context);
    } catch {
      return nextResolve(specifier.slice(0, -3) + ".ts", context);
    }
  },
});

const MANAGED_ENV = [
  "OPENVIKING_CREDENTIAL_SOURCE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_URL",
  "OPENVIKING_BASE_URL",
  "OPENVIKING_MCP_URL",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
  "OPENVIKING_ACCOUNT",
  "OPENVIKING_USER",
  "OPENVIKING_AUTH_MODE",
  "OPENVIKING_PEER_ID",
  "OPENVIKING_DEBUG",
  "OPENVIKING_DEBUG_LOG",
  "OV_DEBUG_LOG",
  "OPENVIKING_PENDING_DIR",
  "OPENVIKING_SESSION_HISTORY_POINTER",
];

/** Clear every variable the extension reads, apply `env`, and return a restore function. */
export function isolateEnv(env) {
  const saved = Object.fromEntries(MANAGED_ENV.map((name) => [name, process.env[name]]));
  for (const name of MANAGED_ENV) delete process.env[name];
  Object.assign(process.env, env);
  return () => {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  };
}

export const loadExtension = async () => (await import(join(EXTENSION_DIR, "index.ts"))).default;
