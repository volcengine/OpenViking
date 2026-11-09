import { settings } from "./lib/config.mjs";
import { synchronize, status } from "./lib/runtime.mjs";
import { AdapterError } from "./lib/files.mjs";

try {
  const action = process.argv[2] || "status";
  if (!["status", "sync", "restore"].includes(action)) throw new AdapterError("usage_status_sync_restore");
  const config = await settings();
  const result = action === "status" ? await status(config)
    : await synchronize(config, { restore: action === "restore", resume: action === "sync" });
  console.log(JSON.stringify(result, null, 2));
  if (["conflict", "error"].includes(result.status)) process.exitCode = 1;
} catch (error) {
  console.error(error instanceof AdapterError ? error.message : "OpenViking models: adapter_unavailable");
  process.exitCode = 1;
}
