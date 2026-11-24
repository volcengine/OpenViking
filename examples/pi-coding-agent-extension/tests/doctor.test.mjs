import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DOCTOR = fileURLToPath(new URL("../scripts/ov-memory-doctor.mjs", import.meta.url));

function runDoctor(home) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/^(OPENVIKING_|OV_|PI_CODING_AGENT_)/u.test(name)));
  // Only node on PATH, so no real pi on this machine is asked anything.
  const result = spawnSync(process.execPath, [DOCTOR, "--offline", "--json"], {
    cwd: home,
    env: { ...env, HOME: home, PATH: dirname(process.execPath), NO_COLOR: "1" },
    encoding: "utf8",
  });
  assert.ok(result.stdout, result.stderr);
  return { status: result.status, report: JSON.parse(result.stdout) };
}

const installSection = (report) => report.sections.find((section) => section.title === "Plugin install");
const problems = (section) => section.findings.filter((finding) => finding.level === "fail" || finding.level === "warn");

function fakeInstall(home) {
  const dest = join(home, ".pi", "agent", "extensions", "openviking");
  const files = ["index.ts", "config.ts", "lib/mcp-bridge.mjs", "shared/credentials.mjs", "shared/ov-http.mjs", "shared/mcp-proxy-config.mjs",
    "node_modules/@modelcontextprotocol/client/package.json",
    ...["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search", "ov-memory-doctor"].map((skill) => `skills/${skill}/SKILL.md`)];
  for (const rel of files) {
    mkdirSync(dirname(join(dest, rel)), { recursive: true });
    writeFileSync(join(dest, rel), "");
  }
  writeFileSync(join(dest, "package.json"), JSON.stringify({ name: "openviking-pi-extension", version: "0.0.0" }));
  writeFileSync(join(home, ".pi", "agent", "settings.json"), JSON.stringify({ packages: [] }));
  return dest;
}

test("the doctor reports harness pi and fails the install on an empty machine", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-pi-doctor-"));
  try {
    const { status, report } = runDoctor(home);
    assert.equal(report.harness, "pi");
    assert.equal(status, 1);
    assert.ok(installSection(report).findings.some((finding) => finding.level === "fail"));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("a healthy install has no install problems", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-pi-doctor-"));
  try {
    fakeInstall(home);
    const { report } = runDoctor(home);
    assert.deepEqual(problems(installSection(report)), []);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("settings that turn the extension off or register it as a package are reported", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-pi-doctor-"));
  try {
    const dest = fakeInstall(home);
    const userSettings = join(home, ".pi", "agent", "settings.json");
    const titles = () => problems(installSection(runDoctor(home).report)).map((finding) => finding.title);
    // What `pi config` writes when the extension is switched off.
    writeFileSync(userSettings, JSON.stringify({
      extensions: ["extensions/openviking/index.ts", "-extensions/openviking/index.ts"],
      packages: [dest],
    }));
    assert.deepEqual(titles(), ["pi's settings turn this extension off", "the extension is also registered as a pi package, so it loads twice"]);

    // The same override in the project's settings, with an absolute path.
    writeFileSync(userSettings, JSON.stringify({ packages: [] }));
    mkdirSync(join(home, ".pi"), { recursive: true });
    writeFileSync(join(home, ".pi", "settings.json"), JSON.stringify({ extensions: [`-${join(dest, "index.ts")}`] }));
    assert.deepEqual(titles(), ["pi's settings turn this extension off"]);

    // An override for something else is not about this extension.
    writeFileSync(join(home, ".pi", "settings.json"), JSON.stringify({ extensions: ["-extensions/other/index.ts"] }));
    assert.deepEqual(titles(), []);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
