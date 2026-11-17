#!/usr/bin/env node

/**
 * Build the standalone ZIP Kimi Code installs with `/plugins install <url>`.
 *
 * The archive is the plugin directory, with the test tree and every non-runtime
 * file removed, at the top level — the host requires `kimi.plugin.json` at the
 * ZIP root, not inside a wrapper directory. Everything the runtime imports is
 * already in `scripts/shared/` because the generator put it there; this script
 * therefore never decides the file list by hand. It asks the code.
 *
 * The file set is:
 *   kimi.plugin.json, package.json, README.md, README_CN.md, DESIGN.md
 *   scripts/**   except *.test.mjs and the shared copies' generators
 *   servers/**
 *   skills/**    the generated copies
 *   locales/**   the manifest's interface translations
 *
 * Excluded on purpose: `tests/`, `node_modules/`, `.git/`, any `.env`, any
 * key/certificate file, and the debug files a local run leaves behind.
 *
 * Usage:
 *   node ./scripts/pack.mjs [--out <file.zip>] [--check]
 *
 * `--check` verifies that the runtime closure is complete without writing an
 * archive, which is what CI uses to fail a release whose imports would break on
 * a user's machine.
 */

import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import { createWriteStream } from "node:fs";
import { deflateRawSync } from "node:zlib";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Root files the host or a reader needs. */
const ROOT_FILES = [
  "kimi.plugin.json",
  "package.json",
  "README.md",
  "README_CN.md",
  "DESIGN.md",
];

/** Directories that ship whole, minus the filters below. */
const RUNTIME_DIRS = ["scripts", "servers", "skills", "locales"];

/** Never shipped, wherever they appear. */
const EXCLUDED_DIRS = new Set(["node_modules", ".git", "tests", "coverage", "dist"]);

/**
 * A secret-shaped file name. The plugin has no business shipping one, and a
 * developer's stray `.env` must never reach a user's machine inside a release.
 */
const SECRET_FILE = /(^|\.)(env|pem|key|p12|pfx|crt|cer|keystore)$|(^|\.)env\.(local|production|development)$|^id_(rsa|ed25519|ecdsa)$/i;

/** Files a local run leaves behind, plus the empty-directory placeholders. */
const LOCAL_ARTIFACTS = /\.(log|tmp|zip)$|\.corrupt-|\.processing$|\.dbg\.|^\.gitkeep$|^\.DS_Store$/i;

function toPosix(path) {
  return path.split(sep).join("/");
}

async function walk(dir, out = []) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) continue;
      await walk(full, out);
      continue;
    }
    if (!entry.isFile()) continue;
    if (SECRET_FILE.test(entry.name)) continue;
    if (LOCAL_ARTIFACTS.test(entry.name)) continue;
    if (entry.name.endsWith(".test.mjs")) continue;
    out.push(full);
  }
  return out;
}

/**
 * The files the archive holds, as paths relative to the plugin root.
 *
 * `kimi.plugin.json` must be at the root of the archive: the host looks for it
 * there and nowhere else.
 */
export async function collectArchiveFiles(root = PLUGIN_ROOT) {
  const files = [];
  for (const name of ROOT_FILES) {
    const path = join(root, name);
    try {
      if ((await stat(path)).isFile()) files.push(toPosix(relative(root, path)));
    } catch {
      // A missing optional file is not an error; a missing manifest is checked
      // separately, because it makes the archive useless.
    }
  }
  for (const dir of RUNTIME_DIRS) {
    for (const path of await walk(join(root, dir))) {
      files.push(toPosix(relative(root, path)));
    }
  }
  return files.sort();
}

/**
 * Every relative import the shipped JavaScript makes must resolve inside the
 * archive.
 *
 * This is the check that catches the failure a source-tree test cannot: a module
 * that exists in the repository but was never generated into `scripts/shared/`
 * would install fine and then fail on the first hook. `node_modules` imports and
 * `node:` builtins are fine; a relative one that is missing is fatal.
 */
export async function checkRuntimeClosure(files, root = PLUGIN_ROOT) {
  const present = new Set(files);
  const problems = [];
  const importRe = /(?:^|[\s;(=])(?:import|export)\b[^;'"]*?from\s*["']([^"']+)["']|(?:^|[\s;])import\s*["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;

  for (const file of files) {
    if (!/\.(mjs|js|cjs)$/.test(file)) continue;
    const source = await readFile(join(root, file), "utf8");
    for (const match of source.matchAll(importRe)) {
      const spec = match[1] || match[2] || match[3];
      if (!spec || !spec.startsWith(".")) continue;
      const resolved = toPosix(relative(root, resolve(root, dirname(file), spec)));
      if (present.has(resolved)) continue;
      // A directory import resolves through its index; none ship here.
      problems.push(`${file} imports ${spec}, which the archive does not contain`);
    }
  }
  return problems;
}

/**
 * The paths `kimi.plugin.json` promises: each hook command, the MCP proxy and
 * the skills directory.
 */
export function declaredEntries(manifestText) {
  const manifest = JSON.parse(manifestText);
  const entries = [];
  for (const hook of Array.isArray(manifest.hooks) ? manifest.hooks : []) {
    const match = /^node\s+(\.\/\S+)$/.exec(String(hook.command || ""));
    if (match) entries.push(match[1].slice(2));
  }
  for (const server of Object.values(manifest.mcpServers || {})) {
    if (typeof server === "string") continue;
    for (const arg of server.args || []) {
      if (typeof arg === "string" && arg.startsWith("./")) entries.push(arg.slice(2));
    }
  }
  if (typeof manifest.skills === "string" && manifest.skills.startsWith("./")) {
    const dir = manifest.skills.replace(/^\.\//, "").replace(/\/+$/, "");
    // A directory is present when at least one file under it is.
    entries.push(`${dir}/`);
  }
  const locales = manifest.interface?.locales;
  if (typeof locales === "string" && locales.startsWith("./")) {
    const dir = locales.replace(/^\.\//, "").replace(/\/+$/, "");
    entries.push(`${dir}/`);
  }
  return entries;
}

/** A ZIP with deflate and a fixed timestamp, so two builds of one tree match. */
function buildZip(entries) {
  const chunks = [];
  const central = [];
  let offset = 0;
  // 2020-01-01 00:00:00 in MS-DOS format, fixed so the archive is reproducible.
  const dosTime = 0;
  const dosDate = ((2020 - 1980) << 9) | (1 << 5) | 1;

  for (const entry of entries) {
    const nameBytes = Buffer.from(entry.name, "utf8");
    const data = entry.data;
    const deflated = deflateRawSync(data, { level: 9 });
    const useDeflate = deflated.length < data.length;
    const body = useDeflate ? deflated : data;
    const crc = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(useDeflate ? 8 : 0, 8);
    local.writeUInt16LE(dosTime, 10);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    local.writeUInt16LE(0, 28);

    chunks.push(local, nameBytes, body);

    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(useDeflate ? 8 : 0, 10);
    header.writeUInt16LE(dosTime, 12);
    header.writeUInt16LE(dosDate, 14);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(body.length, 20);
    header.writeUInt32LE(data.length, 24);
    header.writeUInt16LE(nameBytes.length, 28);
    header.writeUInt16LE(0, 30);
    header.writeUInt16LE(0, 32);
    header.writeUInt16LE(0, 34);
    header.writeUInt16LE(0, 36);
    header.writeUInt32LE(0, 38);
    header.writeUInt32LE(offset, 42);
    central.push(header, nameBytes);

    offset += local.length + nameBytes.length + body.length;
  }

  const centralBuffer = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralBuffer.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);

  return Buffer.concat([...chunks, centralBuffer, end]);
}

let crcTable = null;
function crc32(buffer) {
  if (!crcTable) {
    crcTable = new Int32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c;
    }
  }
  let crc = -1;
  for (let index = 0; index < buffer.length; index += 1) {
    crc = (crc >>> 8) ^ crcTable[(crc ^ buffer[index]) & 0xff];
  }
  return (crc ^ -1) >>> 0;
}

/** Build the archive for `root`, returning its path and the file list. */
export async function buildArchive({ root = PLUGIN_ROOT, out = join(root, "openviking-memory.zip") } = {}) {
  const files = await collectArchiveFiles(root);
  if (!files.includes("kimi.plugin.json")) {
    throw new Error("kimi.plugin.json is missing from the archive root; the host would refuse the ZIP");
  }
  const problems = await checkRuntimeClosure(files, root);
  if (problems.length) {
    throw new Error(`the runtime closure is incomplete:\n  ${problems.join("\n  ")}`);
  }
  // The manifest is the host's contract: every entry it names has to be in the
  // archive, or the plugin installs and then fails on its first hook.
  const declared = declaredEntries(await readFile(join(root, "kimi.plugin.json"), "utf8"));
  const present = new Set(files);
  const missing = declared.filter((name) => (
    name.endsWith("/")
      ? !files.some((file) => file.startsWith(name))
      : !present.has(name)
  ));
  if (missing.length) {
    throw new Error(`the manifest names entries the archive does not contain:\n  ${missing.join("\n  ")}`);
  }
  const entries = [];
  for (const name of files) {
    entries.push({ name, data: await readFile(join(root, name)) });
  }
  const zip = buildZip(entries);
  await writeFile(out, zip);
  return { out, files, bytes: zip.length };
}

async function main() {
  const args = process.argv.slice(2);
  const checkOnly = args.includes("--check");
  const outIndex = args.indexOf("--out");
  const outArg = outIndex >= 0 ? args[outIndex + 1] : "";
  if (outIndex >= 0 && (!outArg || outArg.startsWith("--"))) {
    process.stderr.write("pack: --out needs a file path\n");
    process.exitCode = 2;
    return;
  }
  const out = outArg ? resolve(outArg) : join(PLUGIN_ROOT, "openviking-memory.zip");

  if (checkOnly) {
    const files = await collectArchiveFiles();
    const problems = await checkRuntimeClosure(files);
    if (problems.length) {
      process.stderr.write(`pack: the runtime closure is incomplete\n  ${problems.join("\n  ")}\n`);
      process.exitCode = 1;
      return;
    }
    process.stdout.write(`pack: ${files.length} files, closure complete\n`);
    return;
  }

  const result = await buildArchive({ out });
  process.stdout.write(`pack: wrote ${relative(process.cwd(), result.out)} (${result.files.length} files, ${result.bytes} bytes)\n`);
}

const isDirectRun = process.argv[1] && /pack\.mjs$/i.test(process.argv[1].replace(/\\/g, "/"));
if (isDirectRun) {
  main().catch((error) => {
    process.stderr.write(`pack: ${error?.message || error}\n`);
    process.exitCode = 1;
  });
}
