import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const DOCTOR = fileURLToPath(new URL("./scripts/ov-memory-doctor.mjs", import.meta.url));
const PACKAGE = "@openviking/dsh-memory-plugin";

function runDoctor(home, extra = {}) {
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([key]) => !key.startsWith("OPENVIKING_") && !key.startsWith("DSH_")));
  const run = spawnSync(process.execPath, [DOCTOR, "--offline", "--json"], {
    cwd: home,
    // Only node on PATH, so the `dsh --version` probe never starts a real dsh.
    env: { ...env, ...extra, HOME: home, PATH: dirname(process.execPath) },
    encoding: "utf8",
  });
  const out = JSON.parse(run.stdout);
  const install = out.sections.find((section) => section.title === "Plugin install");
  return { out, install, levels: install.findings.map((finding) => finding.level) };
}

function fakeProfile(home, name, { bundled = true } = {}) {
  const dir = join(home, ".dsh", "profiles", name);
  mkdirSync(join(dir, "node_modules", "@openviking", "dsh-memory-plugin"), { recursive: true });
  writeFileSync(join(dir, "package.json"), JSON.stringify({
    dependencies: { [PACKAGE]: "1.0.0" },
    dsh: { profile: { bundles: ["@deepseek-ai/dsh-base", ...(bundled ? [PACKAGE] : [])] } },
  }));
  writeFileSync(join(dir, "node_modules", "@openviking", "dsh-memory-plugin", "package.json"),
    JSON.stringify({ name: PACKAGE, version: "1.0.0" }));
}

test("on a machine without DeepSeek Harness the doctor fails the install", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-dsh-doctor-"));
  try {
    const { out, levels } = runDoctor(home);
    assert.equal(out.harness, "dsh");
    assert.ok(levels.includes("fail"), JSON.stringify(levels));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("a profile that loads the plugin is a clean install", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-dsh-doctor-"));
  try {
    fakeProfile(home, "web");
    const { install, levels } = runDoctor(home);
    assert.deepEqual(levels.filter((level) => level === "fail" || level === "warn"), [], JSON.stringify(install.findings));
    assert.ok(install.findings.some((finding) => finding.title.includes("profile web loads")));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("a dsh shell names its profile, and the doctor checks that one", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-dsh-doctor-"));
  try {
    fakeProfile(home, "web");
    fakeProfile(home, "desktop", { bundled: false });
    const shell = (profile) => runDoctor(home, { DSH_SHELL: "1", DSH_PROFILE: profile });
    assert.ok(shell("desktop").levels.includes("warn"));
    assert.ok(shell("other").levels.includes("fail"));
    assert.ok(!shell("web").levels.some((level) => level === "fail" || level === "warn"));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("a connection given only in the profile's cordis patch is a warning, not a missing config", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-dsh-doctor-"));
  try {
    fakeProfile(home, "web");
    const config = (out) => out.sections.find((section) => section.title === "Configuration").findings
      .filter((finding) => /^no (usable )?config/u.test(finding.title))
      .map((finding) => `${finding.level}: ${finding.title}`);
    assert.deepEqual(config(runDoctor(home).out), ["fail: no usable config — the plugin falls back to http://127.0.0.1:1933 with no key"]);
    writeFileSync(join(home, ".dsh", "profiles", "web", "cordis.patch.yml"),
      "- id: openviking-memory-runtime\n  config:\n    endpoint: http://127.0.0.1:1933\n");
    assert.deepEqual(config(runDoctor(home).out), ["warn: no config outside profile web's cordis.patch.yml"]);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
