#!/usr/bin/env node

import { access, mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve as resolvePath, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const SHARED_DIR = join(ROOT, "examples", "memory-plugin-shared", "lib");

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
    root: join(ROOT, "examples", "claude-code-memory-plugin"),
    dir: join(ROOT, "examples", "claude-code-memory-plugin", "scripts", "shared"),
    committed: true,
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
  {
    root: join(ROOT, "examples", "openclaw-plugin"),
    dir: join(ROOT, "examples", "openclaw-plugin", "shared"),
    committed: false,
  },
];

// cursor, trae, trae-cn and zcode vendor nothing: the installer copies the
// canonical runtime to `$OV_HOME/agent-integrations/memory-plugin-shared/lib`
// and they import it by the relative path that resolves both there and here.
export const ASSEMBLED_ROOTS = [
  join(ROOT, "examples", "agent-hook-plugin"),
];

export const GENERATED_HEADER = "// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.\n";

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
  // Not shipped to openclaw-plugin: its REST tool surface has its own operator
  // skill (openviking-context-database) with different tool names. Nor to
  // agent-plugins, whose copy is a deliberately different hook-free variant.
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "codex-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "claude-code-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "agent-hook-plugin", "hosts", "cursor", "skills"),
    committed: true,
  },
  {
    skill: "openviking-memory",
    dir: join(ROOT, "examples", "dsh-memory-plugin", "skills"),
    committed: true,
  },
  // Only the two harnesses that ship the experience workflow today.
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "codex-memory-plugin", "skills"),
    committed: true,
  },
  {
    skill: "ov-experience-memory",
    dir: join(ROOT, "examples", "claude-code-memory-plugin", "skills"),
    committed: true,
  },
];

const SOURCE_EXTENSIONS = new Set([".mjs", ".js", ".cjs", ".ts", ".mts"]);
const SKIPPED_DIRS = new Set(["node_modules", ".git", "dist", "coverage"]);

const STATIC_IMPORT_RE = /(?:^|[\s;(=])(?:import|export)\b[^;'"]*?from\s*["']([^"']+)["']/g;
const DYNAMIC_IMPORT_RE = /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;
const SIDE_EFFECT_IMPORT_RE = /(?:^|[\s;])import\s*["']([^"']+)["']/g;

/** Every module specifier a source file names, in any of the three forms. */
export function importSpecifiers(source) {
  const found = [];
  for (const re of [STATIC_IMPORT_RE, DYNAMIC_IMPORT_RE, SIDE_EFFECT_IMPORT_RE]) {
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
 * own code — including re-export shims like claude-code's `scripts/lib/`, which
 * are the only importer of several modules.
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
      if (spec.startsWith("./")) pending.push(spec.slice(2));
    }
  }
  return [...generated].sort();
}

// The installer reads this file instead of computing the closure itself: it
// runs against a marketplace archive, a flat layout where this generator finds
// no plugin sources and would silently resolve an empty list.
export const MANIFEST_PATH = join(SHARED_DIR, "MANIFEST");

/** The closure the installer has to assemble for the harnesses that vendor nothing. */
export async function assembledClosure() {
  const seeds = new Set();
  for (const root of ASSEMBLED_ROOTS) {
    const { seeds: found, missing } = await directSharedImports({ root, dir: SHARED_DIR });
    if (missing.length) {
      const detail = missing.map((m) => `${m.name} (imported by ${m.importer})`).join(", ");
      throw new Error(`the assembled runtime is missing: ${detail}`);
    }
    for (const seed of found) seeds.add(seed);
  }
  return sharedClosure([...seeds]);
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
      typed: own.some((file) => file.endsWith(".ts") || file.endsWith(".mts")),
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
  await mkdir(targetDir, { recursive: true });
  const names = typed ? [file, `${file.slice(0, -4)}.d.mts`] : [file];
  for (const name of names) {
    const body = await readFile(join(SHARED_DIR, name), "utf-8").catch(() => null);
    if (body === null) continue;
    // Written through a rename so a reader never sees half a module: the
    // marketplace staging script runs this generator, and it can run while a
    // test is byte-comparing the copies.
    const target = join(targetDir, name);
    const staging = `${target}.${process.pid}.tmp`;
    await writeFile(staging, `${GENERATED_HEADER}${body}`, "utf-8");
    await rename(staging, target);
  }
}

async function writeManifest(files) {
  const staging = `${MANIFEST_PATH}.${process.pid}.tmp`;
  await writeFile(staging, files.map((file) => `${file}\n`).join(""), "utf-8");
  await rename(staging, MANIFEST_PATH);
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
  for (const name of (await readdir(target.dir).catch(() => [])).sort()) {
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

  const assembled = await assembledClosure();
  await writeManifest(assembled);
  process.stdout.write(`wrote ${relative(ROOT, MANIFEST_PATH)}\n`);
  for (const file of assembled) claimed.add(file);

  const unclaimed = (await readdir(SHARED_DIR))
    .filter((file) => file.endsWith(".mjs") && !claimed.has(file))
    .sort();
  for (const file of unclaimed) {
    process.stdout.write(`unused lib/${file} — no target imports it\n`);
  }

  for (const { skill, dir } of SKILL_TARGETS) {
    await copySkill(skill, dir);
    process.stdout.write(`synced ${skill}/ -> ${relative(ROOT, dir)}\n`);
  }
}

// Guard the sync behind the entrypoint check so sync.test.mjs can import the
// target lists as the single source of truth instead of keeping its own copy —
// the duplicated lists had drifted, and a drifted vendored file passed CI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  main().catch((err) => {
    process.stderr.write(`${err?.stack || err}\n`);
    process.exit(1);
  });
}
