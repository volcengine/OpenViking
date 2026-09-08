#!/usr/bin/env node

// What the marketplace archive must contain is not a list anybody keeps: it is
// what each plugin's manifests name, what those entrypoints import, and what the
// shared-module sync generates. The hand-typed list this replaced was a second
// copy of all three and had drifted from every one of them — it named eleven of
// the twenty-two modules the installer assembles and four of the thirty-eight
// copies the sync generates.

import { readdir, readFile, stat } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import {
  MANIFEST_PATH,
  ROOT,
  assembledClosure,
  importSpecifiers,
  resolveTargets,
} from "../../examples/memory-plugin-shared/sync.mjs";

const EXAMPLES = join(ROOT, "examples");

// Manifests a host reads by name to find everything else.
const HOST_MANIFESTS = ["openviking.integration.json", ".mcp.json", "hooks.json", join("hooks", "hooks.json")];

// Directories a host loads whole; what is inside them is named nowhere.
const CONTENT_DIRS = ["skills", "rules", "commands"];

const SCRIPT_RE = /[^\s"'`]+\.(?:mjs|cjs|js)\b/g;

async function isFile(path) {
  return stat(path)
    .then((info) => info.isFile())
    .catch(() => false);
}

async function isDirectory(path) {
  return stat(path)
    .then((info) => info.isDirectory())
    .catch(() => false);
}

async function filesUnder(dir, out = []) {
  const entries = await readdir(dir, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) await filesUnder(path, out);
    else if (entry.isFile()) out.push(path);
  }
  return out;
}

/** Every string a JSON document holds, at any depth. */
function jsonStrings(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) for (const item of value) jsonStrings(item, out);
  else if (value && typeof value === "object") for (const item of Object.values(value)) jsonStrings(item, out);
  return out;
}

/**
 * The file a host-templated command names, if the plugin has it.
 *
 * Each host writes its plugin root differently — `${CLAUDE_PLUGIN_ROOT}`,
 * `__OPENVIKING_PLUGIN_ROOT__`, `$PLUGIN_DIR`, or nothing at all — so the
 * prefix is dropped a segment at a time until what is left is a file in the
 * plugin, which may be one the plugin reaches across its own boundary.
 */
async function resolveInPlugin(root, token) {
  const parts = token.split("/").filter((part) => part && part !== ".");
  for (let i = 0; i < parts.length; i += 1) {
    const candidate = join(root, ...parts.slice(i));
    if (await isFile(candidate)) return candidate;
  }
  return null;
}

async function namedScripts(root, text) {
  const found = [];
  for (const token of text.match(SCRIPT_RE) || []) {
    const path = await resolveInPlugin(root, token);
    if (path) found.push(path);
  }
  return found;
}

/** Every module reachable from `entries` by relative import, entries included. */
async function importClosure(entries) {
  const reached = new Set();
  const pending = [...entries];
  while (pending.length) {
    const file = pending.pop();
    if (reached.has(file) || !(await isFile(file))) continue;
    reached.add(file);
    const source = await readFile(file, "utf-8");
    for (const spec of importSpecifiers(source)) {
      if (spec.startsWith(".")) pending.push(resolve(dirname(file), spec));
    }
  }
  return reached;
}

/**
 * Where one plugin's host manifests sit: its own root, plus a directory per host
 * for a plugin that serves several of them.
 */
async function manifestRoots(root) {
  const roots = [root];
  for (const entry of await readdir(join(root, "hosts"), { withFileTypes: true }).catch(() => [])) {
    if (entry.isDirectory()) roots.push(join(root, "hosts", entry.name));
  }
  return roots;
}

/** The manifests, entrypoints and content one staged plugin directory owes. */
async function pluginRequirements(root) {
  const required = new Set();
  const entrypoints = new Set();

  for (const entry of await readdir(root, { withFileTypes: true }).catch(() => [])) {
    // `.claude-plugin/`, `.codex-plugin/`: the host's own manifest directory,
    // always small and always shipped whole.
    if (entry.isDirectory() && entry.name.startsWith(".")) {
      for (const file of await filesUnder(join(root, entry.name))) required.add(file);
    }
  }
  for (const host of await manifestRoots(root)) {
    for (const manifest of HOST_MANIFESTS) {
      if (await isFile(join(host, manifest))) required.add(join(host, manifest));
    }
    for (const dir of CONTENT_DIRS) {
      for (const file of await filesUnder(join(host, dir))) required.add(file);
    }
  }

  for (const file of [...required]) {
    if (file.endsWith(".json")) {
      for (const value of jsonStrings(JSON.parse(await readFile(file, "utf-8")))) {
        for (const script of await namedScripts(root, value)) entrypoints.add(script);
      }
    } else if (file.endsWith(".md")) {
      // A skill's own document is its manifest: the command it tells the agent
      // to run has to be in the archive too.
      for (const script of await namedScripts(root, await readFile(file, "utf-8"))) {
        entrypoints.add(script);
      }
    }
  }

  for (const file of await importClosure([...entrypoints])) required.add(file);
  return required;
}

/** Every file the staged marketplace tree must hold, relative to its root. */
export async function requiredArchiveFiles(stagedNames) {
  const staged = new Set(stagedNames);
  const required = new Set();
  // The config-driven hook hosts import the shared runtime across the plugin
  // boundary; anything outside the staged tree is not this archive's.
  const addIfStaged = (path) => {
    const name = relative(EXAMPLES, path);
    if (!name.startsWith("..") && staged.has(name.split(sep)[0])) required.add(name);
  };

  for (const name of staged) {
    if (name === "memory-plugin-shared") continue;
    const root = join(EXAMPLES, name);
    if (!(await isDirectory(root))) continue;
    // `.claude-plugin/` and `.agents/` are the marketplace manifests the
    // installer registers the unzipped directory with, not plugins.
    if (name.startsWith(".")) {
      for (const file of await filesUnder(root)) addIfStaged(file);
      continue;
    }
    for (const file of await pluginRequirements(root)) addIfStaged(file);
  }

  // The plugins that vendor a copy of the shared runtime ship what the sync put
  // there, and the harnesses that vendor nothing ship the closure the installer
  // assembles for them, named by the manifest the installer reads.
  for (const target of await resolveTargets()) {
    const dir = relative(EXAMPLES, target.dir);
    if (dir.startsWith("..") || !staged.has(dir.split(sep)[0])) continue;
    for (const file of target.files) required.add(join(dir, file));
  }
  if (staged.has("memory-plugin-shared")) {
    addIfStaged(MANIFEST_PATH);
    for (const file of await assembledClosure()) addIfStaged(join(dirname(MANIFEST_PATH), file));
  }

  return [...required].sort();
}

async function main(stage, expected) {
  const missing = [];
  for (const name of expected) {
    if (!(await isDirectory(join(stage, name)))) missing.push(`${name}/`);
  }
  for (const file of await requiredArchiveFiles(expected)) {
    if (!(await isFile(join(stage, file)))) missing.push(file);
  }
  if (missing.length) {
    for (const file of missing) process.stderr.write(`Marketplace archive is missing ${file}\n`);
    process.exit(1);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const [stage, ...expected] = process.argv.slice(2);
  // The expected directories are the caller's list, not the stage's own: a name
  // dropped from it would otherwise leave both this check and the staging script
  // agreeing that whatever happens to be there is complete.
  if (!stage || !expected.length) {
    process.stderr.write("usage: check-marketplace-archive.mjs <stage-dir> <plugin-dir>...\n");
    process.exit(2);
  }
  main(stage, expected).catch((err) => {
    process.stderr.write(`${err?.stack || err}\n`);
    process.exit(1);
  });
}
