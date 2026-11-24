#!/usr/bin/env node

// DeepSeek Harness Desktop keeps its plugins in a reserved `desktop` profile
// that only its own `dsh` launcher may manage: npm `dsh` refuses the profile,
// and no `dsh` will boot it or dump its config. Desktop is an Electron app that
// is not on npm and has no Linux build, so this emulates its launcher with the
// npm dsh release under test, the way Desktop wires the same packages:
// apps/desktop/src/project-manager.ts initializes the profile from the web
// template on first launch, and apps/desktop-host/src/cli.ts runs the ordinary
// CLI with runCli({ manageDesktopProfile: true }). Desktop's bundled pnpm is
// the one part left out; the pnpm on PATH stands in for it.
//
// Usage, as a host-compat smoke step from the directory the dsh release is
// installed in, with HOME pointing at a scratch home:
//   node .github/scripts/dsh-desktop-smoke.mjs <tarball> <runtime-id>
//
// It adds the packed plugin to the desktop profile, requires the profile to
// list and compose it, and checks the refusals the installer relies on.

import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT = fileURLToPath(import.meta.url);
const LAUNCHER = "--as-desktop-launcher";
const DESKTOP_REFUSAL = /profile "desktop" is managed exclusively by the Electron application/;
const STEP_TIMEOUT_MS = 5 * 60 * 1000;

const requireFromHost = createRequire(join(process.cwd(), "package.json"));
const dshDir = dirname(requireFromHost.resolve("@deepseek-ai/dsh/package.json"));
const requireFromDsh = createRequire(join(dshDir, "package.json"));
const bin = requireFromDsh.resolve("@deepseek-ai/dsh/lib/bin.js");

function fail(message, output = "") {
  console.error(`dsh desktop smoke: ${message}${output ? `\n${output.trimEnd()}` : ""}`);
  process.exit(1);
}

function run(args) {
  const result = spawnSync(process.execPath, args, { encoding: "utf8", timeout: STEP_TIMEOUT_MS });
  const error = result.error ? `\n${result.error.message}` : "";
  return { ok: result.status === 0, output: `${result.stdout ?? ""}${result.stderr ?? ""}${error}` };
}

/** Desktop's launcher: the ordinary CLI with access to the reserved profile. */
function launcher(...args) {
  return run([SCRIPT, LAUNCHER, ...args]);
}

/** npm dsh: the same CLI without that access. */
function npmDsh(...args) {
  return run([bin, ...args]);
}

function expectOk(label, result) {
  if (!result.ok) fail(`${label} failed`, result.output);
  console.log(`ok: ${label}`);
  return result.output;
}

function expectRefused(label, result, pattern) {
  if (result.ok) fail(`${label} succeeded, expected a refusal`, result.output);
  if (!pattern.test(result.output)) fail(`${label} failed without matching /${pattern.source}/`, result.output);
  console.log(`ok: ${label} is refused`);
}

if (process.argv[2] === LAUNCHER) {
  process.argv.splice(2, 1);
  const { runCli } = await import(pathToFileURL(bin).href);
  await runCli({ manageDesktopProfile: true });
} else {
  const [tarball, runtimeId] = process.argv.slice(2);
  if (!tarball || !runtimeId) fail("usage: dsh-desktop-smoke.mjs <tarball> <runtime-id>");
  // The profile resolves under DSH_HOME first; an inherited one would escape the scratch home.
  if (process.env.DSH_HOME) fail("DSH_HOME is set; unset it so the desktop profile lands in the scratch HOME");

  const appBoot = await import(
    pathToFileURL(requireFromDsh.resolve("@deepseek-ai/dsh-app-boot")).href
  );
  const { initProfile, resolveProfileDir } = appBoot;
  // DSH 0.2.2 moved shipped templates out of app-boot; earlier releases still export them there.
  const { PROFILE_TEMPLATES } = appBoot.PROFILE_TEMPLATES ? appBoot : await import(
    pathToFileURL(requireFromDsh.resolve("@deepseek-ai/dsh-official-bundles")).href
  );
  const { name } = JSON.parse(execFileSync("tar", ["-xzOf", tarball, "package/package.json"], { encoding: "utf8" }));
  const profileDir = resolveProfileDir("desktop");

  expectRefused("npm dsh plugin --profile desktop", npmDsh("plugin", "--profile", "desktop", "ls"), DESKTOP_REFUSAL);
  expectRefused(
    "launcher plugin --profile desktop before Desktop's first launch",
    launcher("plugin", "--profile", "desktop", "ls"),
    /Open DeepSeek Harness Desktop once to initialize its profile/,
  );

  // Desktop's first launch: createPluginProfile() in apps/desktop/src/project-manager.ts.
  initProfile(profileDir, PROFILE_TEMPLATES.web.bundles);

  expectOk(`launcher plugin --profile desktop add ${tarball}`, launcher("plugin", "--profile", "desktop", "add", tarball));
  const listed = expectOk("launcher plugin --profile desktop ls", launcher("plugin", "--profile", "desktop", "ls"));
  if (!listed.includes(name)) fail(`plugin ls does not list ${name}`, listed);

  const manifest = JSON.parse(readFileSync(join(profileDir, "package.json"), "utf8"));
  const bundles = manifest.dsh?.profile?.bundles ?? [];
  if (!bundles.includes(name)) fail(`dsh.profile.bundles does not include ${name}: ${JSON.stringify(bundles)}`);
  console.log(`ok: dsh.profile.bundles is ${JSON.stringify(bundles)}`);

  const patchPath = join(profileDir, "node_modules", ...name.split("/"), "cordis.patch.yml");
  if (!existsSync(patchPath)) fail(`${patchPath} is missing`);
  if (!new RegExp(`^\\s*- id: ${runtimeId}$`, "m").test(readFileSync(patchPath, "utf8"))) {
    fail(`${patchPath} has no entry with id ${runtimeId}`);
  }
  console.log(`ok: the installed patch inserts ${runtimeId}`);

  expectRefused("launcher --profile desktop --dump-config", launcher("--profile", "desktop", "--dump-config"), DESKTOP_REFUSAL);
}
