import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const rootDir = join(__dirname, "../..");

function readText(path: string): string {
  return readFileSync(join(rootDir, path), "utf8");
}

describe("Volcengine OpenViking one-click install contract", () => {
  it("keeps the Volcengine install script as a compatibility wrapper", () => {
    const script = readText("scripts/volcengine-openviking-install.sh");

    expect(script).toContain("set -euo pipefail");
    expect(script).toContain("Compatibility wrapper");
    expect(script).toContain("exec \"$SCRIPT_DIR/install.sh\"");
  });

  it("moves Volcengine configuration flags to the global install script", () => {
    const script = readText("scripts/install.sh");

    expect(script).toContain("OPENVIKING_BASE_URL");
    expect(script).toContain("OPENVIKING_API_KEY");
    expect(script).toContain("OPENVIKING_PEER_ROLE");
    expect(script).toContain("OPENVIKING_PEER_PREFIX");
    expect(script).toContain("OPENVIKING_ACCOUNT_ID");
    expect(script).toContain("OPENVIKING_USER_ID");
    expect(script).toContain("OPENCLAW_STATE_DIR");
  });

  it("supports tos, tarball, local, and existing plugin install sources", () => {
    const script = readText("scripts/install.sh");

    expect(script).toContain("--source tos|tarball|local|existing");
    expect(script).toContain("INSTALL_SOURCE=\"${INSTALL_SOURCE:-tos}\"");
    expect(script).toContain("--source existing");
    expect(script).toContain("--tarball");
  });

  it("writes a protected env file and never prints the raw api key", () => {
    const script = readText("scripts/install.sh");

    expect(script).toContain("openviking.env");
    expect(script).toContain("chmod 600 \"$ENV_FILE\"");
    expect(script).toContain("mask_secret");
    expect(script).toContain("redact_arg");
    expect(script).toContain("OPENVIKING_RECALL_RESOURCES");
    expect(script).not.toContain("echo \"$OPENVIKING_API_KEY\"");
  });

  it("delegates configuration to openclaw setup and verifies status", () => {
    const script = readText("scripts/install.sh");

    expect(script).toContain("openclaw openviking setup");
    expect(script).toContain("--base-url \"$OPENVIKING_BASE_URL\"");
    expect(script).toContain("--api-key \"$OPENVIKING_API_KEY\"");
    expect(script).toContain("--peer-role \"$OPENVIKING_PEER_ROLE\"");
    expect(script).toContain("--peer-prefix \"$OPENVIKING_PEER_PREFIX\"");
    expect(script).toContain("--force-slot");
    expect(script).toContain("openclaw gateway restart");
    expect(script).toContain("openclaw openviking status --json");
    expect(script).toContain("openclaw config get plugins.slots.contextEngine");
  });

  it("keeps the Volcengine wrapper in package build outputs for compatibility", () => {
    const buildScript = readText("build.sh");

    expect(buildScript).toContain("scripts/volcengine-openviking-install.sh");
    expect(buildScript).toContain("output/volcengine-install.sh");
  });
});
