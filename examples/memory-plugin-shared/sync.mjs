#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { access, mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { dirname, join, posix, relative, resolve as resolvePath, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const SHARED_DIR = join(ROOT, "examples", "agent-hook-plugin", "lib");

// What a plugin ships equals what it imports, and neither side is written down.
// Hand-kept lists were the drift: a group named after one harness got spread
// into another's, and modules nobody imported ended up vendored into four
// directories while a module somebody did import went missing and became an
// ERR_MODULE_NOT_FOUND on the first hook of a fresh install. Each target below
// only says where its code lives, where its copies go, and whether the copies
// have to be committed; the file set is the transitive closure of what that
// code actually imports.
//
// `committed` is about how the plugin is delivered, not about taste. A host
// that installs by pointing at a directory in this repository can only see
// files git has, so those copies are committed and a bot regenerates them on
// main. A plugin published as an npm package or assembled into a tarball builds
// its copies at pack time, so committing them would only tax every review diff.
export const TARGETS = [
  {
    root: join(ROOT, "examples", "magpie-memory-plugin"),
    dir: join(ROOT, "examples", "magpie-memory-plugin", "shared"),
    committed: false,
  },
  {
    root: join(ROOT, "examples", "codex-memory-plugin"),
    dir: join(ROOT, "examples", "codex-memory-plugin", "scripts", "shared"),
    committed: true,
  },
  {
    root: join(ROOT, "agent-plugins"),
    dir: join(ROOT, "agent-plugins", "servers", "shared"),
    committed: true,
  },
  {
    root: join(ROOT, "examples", "opencode-plugin"),
    dir: join(ROOT, "examples", "opencode-plugin", "lib", "shared"),
    committed: false,
  },
  {
    root: join(ROOT, "examples", "dsh-memory-plugin"),
    dir: join(ROOT, "examples", "dsh-memory-plugin", "shared"),
    committed: false,
  },
  {
    root: join(ROOT, "examples", "pi-coding-agent-extension"),
    dir: join(ROOT, "examples", "pi-coding-agent-extension", "shared"),
    committed: false,
  },
  // Published as a package too, but ov-install's GitHub source downloads the
  // plugin file by file at a git ref, and it has no way to run this generator.
  {
    root: join(ROOT, "examples", "openclaw-plugin"),
    dir: join(ROOT, "examples", "openclaw-plugin", "shared"),
    committed: true,
  },
];

export const GENERATED_HEADER = "// GENERATED FROM examples/agent-hook-plugin/lib. DO NOT EDIT.\n";

// A data file a module reads beside itself (`new URL("./x.json",
// import.meta.url)`) is part of that module's closure like an import, but its
// copies are byte-identical: JSON cannot carry the generated-file header.
export const DATA_EXTENSION = ".json";

// Copies of lib/ data files outside the plugins: the gateway reads the server
// feature table from its own package, so both sides answer from one source.
// Each copy is committed; CI fails a pull request whose copy differs.
export const DATA_TARGETS = [
  { file: "server-features.json", dir: join(ROOT, "openviking_gateway") },
];

// Skills are copied verbatim — a generated-from banner ahead of the `---`
// frontmatter would break every skill loader.
//
// One entry per copy, the shape TARGETS uses, so the same assertions reach both
// kinds of generated file. `committed` is true for every skill copy: .gitignore
// covers the vendored shared/ directories only, and each host installs a skill
// by copying its path out of this repository, so a copy git does not hold ships
// nothing. Being a skill under examples/skills is not what ships it — an entry
// here is.
export const SKILLS_DIR = join(ROOT, "examples", "skills");
export const SKILL_TARGETS = [
  // openviking-memory is not shipped to openclaw-plugin: its REST tool surface
  // has its own operator skill (openviking-context-database) with different
  // tool names. Nor to agent-plugins, whose copy of this one skill is a
  // deliberately different hook-free variant.
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "codex-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "cursor", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "codebuddy", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "qoder", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "dsh-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "pi-coding-agent-extension", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "opencode-plugin", "skills"),
    committed: true,
  },
  // The harnesses that bundle skills. agent-plugins has no hooks, so no
  // session-start catalog: there the skill is the only way the model learns
  // that the skills in OpenViking exist.
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "codex-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "agent-hook-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "cursor", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "codebuddy", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "qoder", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "dsh-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "agent-plugins", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "pi-coding-agent-extension", "skills"),
    committed: true,
  },
  {
    skill: "openviking-skills",
    dir: join(ROOT, "examples", "opencode-plugin", "skills"),
    committed: true,
  },
  // The harnesses that ship the experience workflow today. Where capture sends
  // no tool parts (agent-plugins, Cursor; dsh with captureToolResults off),
  // the copy only retrieves and applies Experience, and its reads feed no
  // trajectory back to the server.
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "codex-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "agent-plugins", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "openclaw-plugin", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "cursor", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "codebuddy", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "qoder", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "dsh-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "pi-coding-agent-extension", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "opencode-plugin", "skills"),
    committed: true,
  },
  // MCP hosts discover rule_search dynamically. OpenClaw exposes ov_rule_search
  // through its REST client. Ship the workflow wherever the host bundles skills.
  ...[
    ["examples", "agent-hook-plugin", "skills"],
    ["examples", "codex-memory-plugin", "skills"],
    ["examples", "agent-hook-plugin", "hosts", "cursor", "skills"],
    ["examples", "agent-hook-plugin", "hosts", "codebuddy", "skills"],
    ["examples", "agent-hook-plugin", "hosts", "qoder", "skills"],
    ["examples", "dsh-memory-plugin", "skills"],
    ["examples", "pi-coding-agent-extension", "skills"],
    ["examples", "opencode-plugin", "skills"],
    ["examples", "openclaw-plugin", "skills"],
    ["agent-plugins", "skills"],
  ].map((path) => ({
    skill: "openviking-rule-search",
    dir: join(ROOT, ...path),
    committed: true,
  })),
  // TRAE and ZCode read these from the user's skills directory, Kimi Code and
  // Antigravity from the plugin itself.
  ...["trae", "zcode", "kimicode", "antigravity"].flatMap((host) => [
    "openviking-memory",
    "openviking-skills",
    "ov-experience-memory",
    "openviking-rule-search",
  ].map((skill) => ({
    skill,
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", host, "skills"),
    committed: true,
  }))),
  // Claude Code and Codex each document a doctor of their own under this name;
  // every other harness shares this one.
  ...[
    ...["cursor", "trae", "zcode", "kimicode", "codebuddy", "qoder", "antigravity"]
      .map((host) => ["agent-hook-plugin", "hosts", host, "skills"]),
    ["opencode-plugin", "skills"],
    ["pi-coding-agent-extension", "skills"],
    ["dsh-memory-plugin", "skills"],
  ].map((path) => ({
    skill: "ov-memory-doctor",
    dir: join(ROOT, "examples", ...path),
    committed: true,
  })),
];

const SOURCE_EXTENSIONS = new Set([".mjs", ".js", ".cjs", ".ts", ".mts"]);
const SKIPPED_DIRS = new Set(["node_modules", ".git", "dist", "coverage"]);

const STATIC_IMPORT_RE = /(?:^|[\s;(=])(?:import|export)\b[^;'"]*?from\s*["']([^"']+)["']/g;
const DYNAMIC_IMPORT_RE = /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;
const SIDE_EFFECT_IMPORT_RE = /(?:^|[\s;])import\s*["']([^"']+)["']/g;
const DATA_FILE_RE = /\bnew\s+URL\s*\(\s*["'](\.\/[^"']+\.json)["']\s*,\s*import\.meta\.url\s*\)/g;

/** Every module specifier a source file names, in any of the three forms, and the data files it reads beside itself. */
export function importSpecifiers(source) {
  const found = [];
  for (const re of [STATIC_IMPORT_RE, DYNAMIC_IMPORT_RE, SIDE_EFFECT_IMPORT_RE, DATA_FILE_RE]) {
    for (const match of source.matchAll(re)) found.push(match[1]);
  }
  return found;
}

async function sourceFilesUnder(dir, skip, out = []) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIPPED_DIRS.has(entry.name) || path === skip) continue;
      await sourceFilesUnder(path, skip, out);
      continue;
    }
    const dot = entry.name.lastIndexOf(".");
    if (dot > 0 && SOURCE_EXTENSIONS.has(entry.name.slice(dot))) out.push(path);
  }
  return out;
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

/**
 * The shared modules a target's own code imports directly.
 *
 * Everything under `root` except the vendored directory itself is the target's
 * own code — including re-export shims, which may be the only importer of a module.
 */
export async function directSharedImports({ root, dir }) {
  const seeds = new Set();
  const missing = [];
  for (const file of await sourceFilesUnder(root, dir)) {
    const source = await readFile(file, "utf-8");
    for (const spec of importSpecifiers(source)) {
      if (!spec.startsWith(".")) continue;
      const resolved = resolvePath(dirname(file), spec);
      if (!resolved.startsWith(dir + sep)) continue;
      const name = resolved.slice(dir.length + 1);
      seeds.add(name);
      // A module this sync has not generated yet is not missing — it is the
      // reason to run the sync. What is missing is a module that exists
      // neither in lib/ nor beside the copies as a plugin-local file.
      if (!(await exists(join(SHARED_DIR, name))) && !(await exists(resolved))) {
        missing.push({ name, importer: relative(ROOT, file) });
      }
    }
  }
  return { seeds: [...seeds].sort(), missing };
}

/**
 * The transitive closure of `seeds` inside lib/.
 *
 * A seed that lib/ does not have is a plugin-local module living in the same
 * directory (pi's recall-ledger), not an error: it is simply not generated.
 */
export async function sharedClosure(seeds) {
  const generated = new Set();
  const pending = [...seeds];
  while (pending.length) {
    const name = pending.pop();
    if (generated.has(name)) continue;
    const source = await readFile(join(SHARED_DIR, name), "utf-8").catch(() => null);
    if (source === null) continue;
    generated.add(name);
    for (const spec of importSpecifiers(source)) {
      if (!spec.startsWith(".")) continue;
      // Relative to the module, which may sit in a subdirectory of lib/.
      const imported = posix.normalize(posix.join(posix.dirname(name), spec));
      if (!imported.startsWith("..")) pending.push(imported);
    }
  }
  return [...generated].sort();
}

/**
 * Whether TypeScript in the plugin imports the shared copies, so they need
 * their `.d.mts` declarations. TypeScript that imports none of them (a Claude
 * Code hooks module beside plain `.mjs` hooks) does not make the target typed.
 */
async function importsFromTypeScript(files, dir) {
  for (const file of files) {
    if (!file.endsWith(".ts") && !file.endsWith(".mts") && !file.endsWith(".tsx")) continue;
    for (const spec of importSpecifiers(await readFile(file, "utf-8"))) {
      if (spec.startsWith(".") && resolvePath(dirname(file), spec).startsWith(dir + sep)) return true;
    }
  }
  return false;
}

/** Every target with the file set its own imports resolve to. */
export async function resolveTargets() {
  const resolved = [];
  for (const target of TARGETS) {
    const { seeds, missing } = await directSharedImports(target);
    if (missing.length) {
      const detail = missing.map((m) => `${m.name} (imported by ${m.importer})`).join(", ");
      throw new Error(`${relative(ROOT, target.dir)}: imports a module that exists nowhere: ${detail}`);
    }
    const own = await sourceFilesUnder(target.root, target.dir);
    resolved.push({
      ...target,
      files: await sharedClosure(seeds),
      typed: await importsFromTypeScript(own, target.dir),
    });
  }
  return resolved;
}

/**
 * Copy one module, plus its type declaration for a target written in
 * TypeScript.
 *
 * The `.d.mts` files used to live in the vendored directories, hand-written and
 * hand-kept in step with modules they sat beside — two harnesses had two
 * different, both incomplete, declarations of the same module. They are part of
 * the module now. A JavaScript target has no use for them, so `typed` says
 * whether this one imports from TypeScript.
 */
async function copySharedFile(file, targetDir, typed) {
  await mkdir(dirname(join(targetDir, file)), { recursive: true });
  const data = file.endsWith(DATA_EXTENSION);
  const names = typed && !data ? [file, `${file.slice(0, -4)}.d.mts`] : [file];
  for (const name of names) {
    const body = await readFile(join(SHARED_DIR, name), "utf-8").catch(() => null);
    if (body === null) continue;
    // A declaration that has no module of its own travels with the one that imports it.
    if (name.endsWith(".d.mts")) {
      for (const spec of importSpecifiers(body)) {
        const imported = posix.normalize(posix.join(posix.dirname(name), spec));
        if (spec.startsWith(".") && spec.endsWith(".d.mts") && !names.includes(imported)) names.push(imported);
      }
    }
    // Written through a rename so a reader never sees half a module: the
    // marketplace staging script runs this generator, and it can run while a
    // test is byte-comparing the copies.
    const target = join(targetDir, name);
    const staging = `${target}.${process.pid}.tmp`;
    await writeFile(staging, data ? body : `${GENERATED_HEADER}${body}`, "utf-8");
    await rename(staging, target);
  }
}

async function copySkill(skill, targetDir) {
  const sourceDir = join(SKILLS_DIR, skill);
  for (const file of (await readdir(sourceDir)).sort()) {
    const target = join(targetDir, skill);
    await mkdir(target, { recursive: true });
    // Through a rename, for the reason the module copies are: a test can be
    // byte-comparing this file while the staging script runs the generator.
    const path = join(target, file);
    const staging = `${path}.${process.pid}.tmp`;
    await writeFile(staging, await readFile(join(sourceDir, file), "utf-8"), "utf-8");
    await rename(staging, path);
  }
}

/** Vendored copies the target no longer imports; the sync would never touch them again. */
async function staleCopies(target, keep) {
  const stale = [];
  const entries = await readdir(target.dir, { recursive: true, withFileTypes: true }).catch(() => []);
  const names = entries
    .filter((entry) => entry.isFile())
    .map((entry) => relative(target.dir, join(entry.parentPath, entry.name)).split(sep).join("/"));
  for (const name of names.sort()) {
    if (name.endsWith(DATA_EXTENSION)) {
      // No banner to tell a copy by: one named after a lib/ data file is ours.
      if (!keep.includes(name) && (await exists(join(SHARED_DIR, name)))) stale.push(name);
      continue;
    }
    const module = name.endsWith(".d.mts") ? `${name.slice(0, -6)}.mjs` : name;
    if (!module.endsWith(".mjs") || keep.includes(module)) continue;
    const body = await readFile(join(target.dir, name), "utf-8");
    if (body.startsWith(GENERATED_HEADER)) stale.push(name);
  }
  return stale;
}

async function main() {
  const claimed = new Set();
  for (const target of await resolveTargets()) {
    for (const file of target.files) {
      await copySharedFile(file, target.dir, target.typed);
      claimed.add(file);
      process.stdout.write(`synced ${file} -> ${relative(ROOT, target.dir)}\n`);
    }
    for (const file of await staleCopies(target, target.files)) {
      process.stdout.write(`stale  ${file} in ${relative(ROOT, target.dir)} — nothing imports it; delete it\n`);
    }
  }

  const { seeds } = await directSharedImports({ root: dirname(SHARED_DIR), dir: SHARED_DIR });
  for (const file of await sharedClosure(seeds)) claimed.add(file);

  const unclaimed = (await readdir(SHARED_DIR))
    .filter((file) => file.endsWith(".mjs") && !claimed.has(file))
    .sort();
  for (const file of unclaimed) {
    process.stdout.write(`unused lib/${file} — no target imports it\n`);
  }

  for (const { file, dir } of DATA_TARGETS) {
    await copySharedFile(file, dir, false);
    process.stdout.write(`synced ${file} -> ${relative(ROOT, dir)}\n`);
  }

  for (const { skill, dir } of SKILL_TARGETS) {
    await copySkill(skill, dir);
    process.stdout.write(`synced ${skill}/ -> ${relative(ROOT, dir)}\n`);
  }
}

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

// Guard the sync behind the entrypoint check so sync.test.mjs can import the
// target lists as the single source of truth instead of keeping its own copy —
// the duplicated lists had drifted, and a drifted vendored file passed CI.
if (isDirectRun()) {
  main().catch((err) => {
    process.stderr.write(`${err?.stack || err}\n`);
    process.exit(1);
  });
}
