import fs from "node:fs/promises";
import { delimiter, join } from "node:path";
import { constants } from "node:fs";
import { createAPI, detectBot } from "./api.mjs";
import { AdapterError } from "./files.mjs";

export async function serverCLIInstalled(env = process.env) {
  for (const directory of (env.PATH || "").split(delimiter).filter(Boolean)) {
    try {
      const file = join(directory, process.platform === "win32" ? "openviking-server.exe" : "openviking-server");
      await fs.access(file, constants.X_OK);
      if ((await fs.stat(file)).isFile()) return true;
    } catch { /* Try the next PATH entry. */ }
  }
  return false;
}
export async function doctor(config, { fetcher = fetch, env = process.env } = {}) {
  const result = { localServerCLIInstalled: await serverCLIInstalled(env) };
  try {
    const file = await createAPI(config, fetcher)("/api/v1/admin/configuration?source=file");
    return { ...result, configurationAccess: "ok", serverRestartSupported: file.restart?.supported === true,
      restartRequired: file.restart_required === true, bot: (await detectBot(config, fetcher)).state };
  } catch (error) {
    return { ...result, configurationAccess: error instanceof AdapterError ? error.code : "unavailable" };
  }
}
