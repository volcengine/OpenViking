import { settings } from "./lib/config.mjs";
import { synchronize, status } from "./lib/runtime.mjs";
import { AdapterError } from "./lib/files.mjs";
import { activate } from "./lib/activation.mjs";
import { doctor } from "./lib/doctor.mjs";

try {
  const action = process.argv[2] || "status";
  if (!["status", "sync", "restore", "apply", "doctor"].includes(action)) throw new AdapterError("usage_status_sync_restore_apply_doctor");
  const config = await settings();
  const result = action === "status" ? await status(config) : action === "doctor" ? await doctor(config) : action === "apply" ? await activate(config, { retry: true })
    : await synchronize(config, { restore: action === "restore", resume: action === "sync" });
  console.log(JSON.stringify(result, null, 2));
  if (["conflict", "error", "apply_failed"].includes(result.status)) process.exitCode = 1;
} catch (error) {
  console.error(error instanceof AdapterError ? error.message : "OpenViking models: adapter_unavailable");
  process.exitCode = 1;
}
