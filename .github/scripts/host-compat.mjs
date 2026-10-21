#!/usr/bin/env node

// Plugins declare the host versions they accept as a peer range, and a range
// open enough to admit host releases nobody has tried yet is only safe if
// somebody does try them. This runs each plugin's tests against the host
// releases its npm dist-tags point at today, so a breaking host release shows
// up here before a user reports it. Hosts are listed in .github/host-compat.json.
//
// Usage: node .github/scripts/host-compat.mjs [--host <id>] [--version <v>] [--report-issues]
//
// The plugin is copied to a scratch directory, every host package its manifest
// pins is moved to the release under test, and its tests run there. The tests
// exercise the plugin's code on the new host packages but never start the host,
// so a host may also list smoke steps: commands that install the real host
// release, add the packed plugin to it, and check the host picked it up. Run
// `node examples/memory-plugin-shared/sync.mjs` first: the copy takes the
// generated shared/ modules with it.

import { execFileSync, spawnSync } from "node:child_process";
import { appendFileSync, cpSync, globSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const CONFIG = join(ROOT, ".github", "host-compat.json");
const LOG_TAIL_LINES = 60;
const STEP_TIMEOUT_MS = 10 * 60 * 1000;

function parseArgs(argv) {
  const args = { host: undefined, version: undefined, reportIssues: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--host") args.host = argv[++i];
    else if (arg === "--version") args.version = argv[++i];
    else if (arg === "--report-issues") args.reportIssues = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  return args;
}

function run(command, args, cwd, env = process.env) {
  const result = spawnSync(command, args, {
    cwd, env, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout: STEP_TIMEOUT_MS,
  });
  const timedOut = result.error?.code === "ETIMEDOUT" ? `\ntimed out after ${STEP_TIMEOUT_MS / 1000}s` : "";
  return { ok: result.status === 0, output: `${result.stdout ?? ""}${result.stderr ?? ""}${timedOut}` };
}

function tail(text) {
  return text.trimEnd().split("\n").slice(-LOG_TAIL_LINES).join("\n");
}

/** Distinct releases the host's dist-tags point at, each with the tags naming it. */
function releasesUnderTest(host) {
  const tags = JSON.parse(execFileSync("npm", ["view", host.probe, "dist-tags", "--json"], { encoding: "utf8" }));
  const releases = new Map();
  for (const tag of host.tags) {
    const version = tags[tag];
    if (!version) continue;
    releases.set(version, [...(releases.get(version) ?? []), tag]);
  }
  return [...releases].map(([version, names]) => ({ version, tags: names }));
}

function retarget(manifest, pattern, version) {
  for (const field of ["devDependencies", "overrides"]) {
    for (const name of Object.keys(manifest[field] ?? {})) {
      if (pattern.test(name)) manifest[field][name] = version;
    }
  }
}

/** Host peers whose declared range does not admit the release, judged the way the host judges. */
function rejectedPeers(manifest, pattern, version, pluginDir, includePrerelease) {
  const semver = createRequire(join(pluginDir, "package.json"))("semver");
  return Object.entries(manifest.peerDependencies ?? {})
    .filter(([name, range]) => pattern.test(name) && !semver.satisfies(version, range, { includePrerelease }))
    .map(([name, range]) => `${name}@"${range}"`);
}

function checkRelease(host, release) {
  const pattern = new RegExp(host.packages);
  const source = join(ROOT, host.plugin);
  const work = mkdtempSync(join(tmpdir(), `host-compat-${host.id}-`));
  try {
    cpSync(source, work, { recursive: true, filter: (path) => basename(path) !== "node_modules" });
    const manifestPath = join(work, "package.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    retarget(manifest, pattern, release.version);
    writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
    rmSync(join(work, "package-lock.json"), { force: true });

    const install = run("npm", ["install", "--no-audit", "--no-fund", "--ignore-scripts"], work);
    if (!install.ok) return { stage: "install", log: tail(install.output) };

    const rejected = rejectedPeers(manifest, pattern, release.version, work, host.includePrerelease);
    if (rejected.length > 0) {
      return { stage: "peer range", log: `${host.probe}@${release.version} is outside ${rejected.join(", ")}` };
    }

    const skip = new Set(host.skipTests ?? []);
    const files = globSync(host.tests, { cwd: work }).filter((file) => !skip.has(file)).sort();
    const tests = run(process.execPath, ["--test", ...files], work);
    if (!tests.ok) return { stage: "tests", log: tail(tests.output) };
    return host.smoke ? smoke(host, release, work) : undefined;
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

/**
 * Install the real host release in an isolated home and run the host's smoke
 * steps against the plugin packed the way npm would publish it. Steps substitute
 * {version} and {tarball}; a step with `expect` must print a line matching it.
 */
function smoke(host, release, work) {
  const dir = mkdtempSync(join(tmpdir(), `host-compat-${host.id}-smoke-`));
  try {
    const home = join(dir, "home");
    const hostDir = join(dir, "host");
    mkdirSync(home);
    mkdirSync(hostDir);
    const pack = run("npm", ["pack", "--ignore-scripts", "--json", "--pack-destination", dir], work);
    if (!pack.ok) return { stage: "smoke: pack", log: tail(pack.output) };
    const tarball = join(dir, JSON.parse(pack.output.slice(pack.output.indexOf("[")))[0].filename);

    // The host's own state (profiles, stores, credentials) lands in the scratch home.
    const env = { ...process.env, HOME: home, USERPROFILE: home, XDG_CONFIG_HOME: join(home, ".config") };
    const fill = (arg) => arg.replaceAll("{version}", release.version).replaceAll("{tarball}", tarball);
    for (const step of host.smoke) {
      const [command, ...args] = step.run.map(fill);
      const label = `smoke: ${step.run.join(" ")}`;
      const result = run(command.startsWith(".") ? join(hostDir, command) : command, args, hostDir, env);
      if (!result.ok) return { stage: label, log: tail(result.output) };
      if (step.expect && !new RegExp(step.expect, "m").test(result.output)) {
        return { stage: label, log: `output has no line matching /${step.expect}/\n${tail(result.output)}` };
      }
    }
    return undefined;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** One issue per failing host release; a release that already has an open issue gets no repeat. */
function reportIssue(host, release, failure) {
  const title = `Host compatibility: ${host.plugin} fails on ${host.probe}@${release.version}`;
  const open = JSON.parse(execFileSync("gh", [
    "issue", "list", "--state", "open", "--search", `"${title}" in:title`, "--json", "title",
  ], { encoding: "utf8" }));
  if (open.some((issue) => issue.title === title)) return;
  const runUrl = process.env.GITHUB_RUN_ID
    ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
    : "a local run";
  const body = [
    `The scheduled host compatibility check failed at the **${failure.stage}** stage for`,
    `\`${host.plugin}\` on \`${host.probe}@${release.version}\` (dist-tags: ${release.tags.join(", ")}).`,
    "",
    `Run: ${runUrl}`,
    "",
    "```",
    failure.log,
    "```",
  ].join("\n");
  execFileSync("gh", ["issue", "create", "--title", title, "--body", body], { stdio: "inherit" });
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const { hosts } = JSON.parse(readFileSync(CONFIG, "utf8"));
  const selected = hosts.filter((host) => !args.host || host.id === args.host);
  if (selected.length === 0) throw new Error(`no host named ${args.host} in ${CONFIG}`);

  const rows = [];
  for (const host of selected) {
    const releases = args.version ? [{ version: args.version, tags: ["--version"] }] : releasesUnderTest(host);
    for (const release of releases) {
      console.log(`::group::${host.id} ${release.version} (${release.tags.join(", ")})`);
      const failure = checkRelease(host, release);
      if (failure) console.log(failure.log);
      console.log("::endgroup::");
      rows.push({ host, release, failure });
      if (failure && args.reportIssues) reportIssue(host, release, failure);
    }
  }

  const summary = [
    "| Host | Release | Tags | Result |",
    "|---|---|---|---|",
    ...rows.map(({ host, release, failure }) =>
      `| ${host.name} | \`${release.version}\` | ${release.tags.join(", ")} | ${failure ? `failed: ${failure.stage}` : "passed"} |`),
  ].join("\n");
  console.log(summary);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${summary}\n`);
  if (rows.some((row) => row.failure)) process.exitCode = 1;
}

main();
