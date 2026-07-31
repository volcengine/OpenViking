import { join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import {
  loadCredentialFiles,
  resolveOpenVikingCredentials,
} from "./shared/credentials.mjs";

export { loadCredentialFiles, resolveOpenVikingCredentials } from "./shared/credentials.mjs";

function main() {
  const cmd = process.argv[2] || "";
  if (cmd === "mcp-url") {
    process.stdout.write(resolveOpenVikingCredentials().mcpUrl);
    return;
  }
  if (cmd === "has-api-key") {
    process.stdout.write(resolveOpenVikingCredentials().hasApiKey ? "1" : "0");
    return;
  }
  if (cmd === "has-peer-id") {
    process.stdout.write(resolveOpenVikingCredentials().peerId ? "1" : "0");
    return;
  }
  process.stderr.write("usage: ov-credentials.mjs <mcp-url|has-api-key|has-peer-id>\n");
  process.exitCode = 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  main();
}
