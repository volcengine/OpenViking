import { homedir } from "node:os";
import { join } from "node:path";

/** The directory the Codex plugin has always kept its own files in. */
export function getStateDir() {
  return process.env.OPENVIKING_CODEX_STATE_DIR || join(homedir(), ".openviking", "codex-plugin-state");
}
