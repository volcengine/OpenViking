import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const rootDir = join(__dirname, "../..");

function readText(path: string): string {
  return readFileSync(join(rootDir, path), "utf8");
}

describe("Volcengine OpenViking one-click install contract", () => {
  it("configures an existing install with the requested identity and protects the key", () => {
    const dir = mkdtempSync(join(tmpdir(), "openviking-installer-"));
    try {
      const stateDir = join(dir, "state");
      const callsPath = join(dir, "calls");
      const envPath = join(stateDir, "openviking.env");
      const apiKey = "local-installer-fixture-secret";
      writeFileSync(join(dir, "openclaw"), [
        "#!/bin/sh",
        'printf "%s\\n" "$*" >> "$OV_INSTALL_TEST_CALLS"',
      ].join("\n"), { mode: 0o700 });
      const args = [
        join(rootDir, "scripts/volcengine-openviking-install.sh"),
        "--source", "existing",
        "--openclaw-state-dir", stateDir,
        "--openviking-base-url", "http://127.0.0.1:1933",
        "--openviking-api-key", apiKey,
        "--peer-role", "sender",
        "--peer-prefix", "bot",
        "--account-id", "acct",
        "--user-id", "ada",
      ];
      const env = {
        PATH: `${dir}:${process.env.PATH}`,
        OPENCLAW_STATE_DIR: stateDir,
        SKILLS_DIR: join(dir, "skills"),
        OV_INSTALL_TEST_CALLS: callsPath,
      };
      const verify = spawnSync("bash", [...args, "--verify-only"], { env, encoding: "utf8" });
      expect(verify.status, verify.stderr).toBe(0);
      expect(existsSync(stateDir)).toBe(false);
      expect(existsSync(callsPath)).toBe(false);

      const setup = spawnSync("bash", args, { env, encoding: "utf8" });
      expect(setup.status, setup.stderr).toBe(0);
      expect(setup.stdout + setup.stderr).not.toContain(apiKey);
      expect(statSync(envPath).mode & 0o777).toBe(0o600);
      expect(readFileSync(envPath, "utf8")).toContain(apiKey);
      expect(readFileSync(callsPath, "utf8").trim().split("\n")).toEqual([
        `openviking setup --base-url http://127.0.0.1:1933 --api-key ${apiKey} --force-slot --peer-role sender --peer-prefix bot --account-id acct --user-id ada`,
        "gateway restart",
        "openviking status --json",
        "config get plugins.slots.contextEngine",
      ]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("keeps the Volcengine wrapper in package build outputs for compatibility", () => {
    const buildScript = readText("build.sh");

    expect(buildScript).toContain("scripts/volcengine-openviking-install.sh");
    expect(buildScript).toContain("output/volcengine-install.sh");
  });
});
