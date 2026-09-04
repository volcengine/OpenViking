#!/usr/bin/env node

import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const SHARED_DIR = join(ROOT, "examples", "memory-plugin-shared", "lib");
// What a plugin ships must equal what it imports. The groups below are
// capabilities, and every target composes the ones it actually uses — no list
// is named after a harness and then spread into another, because that is how a
// module nobody imports ends up vendored into four directories.

/** The files every hook-driven harness plugin imports. */
const HOOK_SHARED_FILES = [
  "credentials.mjs",
  "capture-utils.mjs",
  "session-model.mjs",
  "pending-queue.mjs",
  "debug-log.mjs",
  "recall-compress-core.mjs",
  "recall-core.mjs",
  "retryable.mjs",
  "workspace-peer.mjs",
  "workspace-identity.mjs",
  "profile-inject.mjs",
  "uri-guard.mjs",
];
/** The interactive installer, for the plugins that ship a `scripts/setup.mjs`. */
const SETUP_WIZARD_SHARED_FILES = ["setup-wizard.mjs"];
/** The stdio MCP proxy, for the plugins that bundle one. */
const MCP_PROXY_SHARED_FILES = ["mcp-proxy-core.mjs", "mcp-proxy-config.mjs"];
/** Batched session sends, for the plugins that flush off the hook's hot path. */
const BATCH_SHARED_FILES = ["batch-send.mjs"];
/** The detached write path, for the plugins whose hooks are short-lived subprocesses. */
const ASYNC_WRITE_SHARED_FILES = ["async-writer.mjs"];
/** The layered workspace config, its per-machine registry, and the loader over both. */
const WORKSPACE_CONFIG_SHARED_FILES = ["plugin-config.mjs", "workspace-config.mjs", "workspace-registry.mjs"];

const DOCTOR_SHARED_FILES = [
  ...HOOK_SHARED_FILES,
  ...SETUP_WIZARD_SHARED_FILES,
  ...MCP_PROXY_SHARED_FILES,
  ...BATCH_SHARED_FILES,
  ...ASYNC_WRITE_SHARED_FILES,
  ...WORKSPACE_CONFIG_SHARED_FILES,
  "doctor-core.mjs",
];
// opencode is imported in-process by its host, so it has no hook subprocess to
// detach from: it takes the batch sender without the async write path.
const OPENCODE_SHARED_FILES = [...HOOK_SHARED_FILES, ...SETUP_WIZARD_SHARED_FILES, ...MCP_PROXY_SHARED_FILES, ...BATCH_SHARED_FILES];
// dsh and zcode ship no setup entry point, so nothing there calls the wizard.
const ZCODE_SHARED_FILES = [...HOOK_SHARED_FILES, ...MCP_PROXY_SHARED_FILES, ...BATCH_SHARED_FILES, ...ASYNC_WRITE_SHARED_FILES, "agent-hook-runtime.mjs", "agent-uri-guard.mjs"];
const DSH_SHARED_FILES = [...HOOK_SHARED_FILES, ...MCP_PROXY_SHARED_FILES];
const PI_SHARED_FILES = [...HOOK_SHARED_FILES, ...SETUP_WIZARD_SHARED_FILES];
// Agent Plugins 1.0 has no hooks: it is the proxy and nothing else.
const AGENT_PLUGINS_SHARED_FILES = ["credentials.mjs", "debug-log.mjs", ...MCP_PROXY_SHARED_FILES];
// openclaw assembles recall server-side, so it takes the recall pair alone.
const OPENCLAW_SHARED_FILES = ["recall-compress-core.mjs", "recall-core.mjs"];
export const TARGETS = [
  { dir: join(ROOT, "examples", "claude-code-memory-plugin", "scripts", "shared"), files: DOCTOR_SHARED_FILES },
  { dir: join(ROOT, "examples", "codex-memory-plugin", "scripts", "shared"), files: DOCTOR_SHARED_FILES },
  { dir: join(ROOT, "examples", "opencode-plugin", "lib", "shared"), files: OPENCODE_SHARED_FILES },
  { dir: join(ROOT, "examples", "dsh-memory-plugin", "shared"), files: DSH_SHARED_FILES },
  { dir: join(ROOT, "examples", "pi-coding-agent-extension", "shared"), files: PI_SHARED_FILES },
  { dir: join(ROOT, "examples", "zcode-memory-plugin", "scripts", "shared") , files: ZCODE_SHARED_FILES },
  { dir: join(ROOT, "agent-plugins", "servers", "shared"), files: AGENT_PLUGINS_SHARED_FILES },
  { dir: join(ROOT, "examples", "openclaw-plugin", "shared"), files: OPENCLAW_SHARED_FILES },
];

export const GENERATED_HEADER = "// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.\n";

// Skills are copied verbatim — a generated-from banner ahead of the `---`
// frontmatter would break every skill loader.
export const SKILLS_DIR = join(ROOT, "examples", "skills");
export const SKILL_TARGETS = [
  {
    // Not shipped to openclaw-plugin: its REST tool surface has its own
    // operator skill (openviking-context-database) with different tool names.
    skill: "openviking-memory",
    dirs: [
      join(ROOT, "examples", "codex-memory-plugin", "skills"),
      join(ROOT, "examples", "claude-code-memory-plugin", "skills"),
      join(ROOT, "examples", "cursor-memory-plugin", "skills"),
      join(ROOT, "examples", "dsh-memory-plugin", "skills"),
    ],
  },
  {
    // Only the two harnesses that ship the experience workflow today.
    skill: "ov-experience-memory",
    dirs: [
      join(ROOT, "examples", "codex-memory-plugin", "skills"),
      join(ROOT, "examples", "claude-code-memory-plugin", "skills"),
    ],
  },
];

async function listSharedFiles() {
  const files = await readdir(SHARED_DIR);
  return files.filter((file) => file.endsWith(".mjs")).sort();
}

async function copySharedFile(file, targetDir) {
  await mkdir(targetDir, { recursive: true });
  const source = join(SHARED_DIR, file);
  const target = join(targetDir, file);
  const body = await readFile(source, "utf-8");
  await writeFile(target, `${GENERATED_HEADER}${body}`, "utf-8");
}

async function copySkill(skill, targetDir) {
  const sourceDir = join(SKILLS_DIR, skill);
  for (const file of (await readdir(sourceDir)).sort()) {
    const target = join(targetDir, skill);
    await mkdir(target, { recursive: true });
    await writeFile(join(target, file), await readFile(join(sourceDir, file), "utf-8"), "utf-8");
  }
}

async function main() {
  const allFiles = await listSharedFiles();
  for (const target of TARGETS) {
    const files = target.files ?? allFiles;
    for (const file of files) {
      if (!allFiles.includes(file)) {
        throw new Error(`shared file not found: ${file}`);
      }
      await copySharedFile(file, target.dir);
      process.stdout.write(`synced ${file} -> ${relative(ROOT, target.dir)}\n`);
    }
  }
  for (const { skill, dirs } of SKILL_TARGETS) {
    for (const dir of dirs) {
      await copySkill(skill, dir);
      process.stdout.write(`synced ${skill}/ -> ${relative(ROOT, dir)}\n`);
    }
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
