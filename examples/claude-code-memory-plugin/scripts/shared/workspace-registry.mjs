// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * The per-machine workspace registry, `~/.openviking/workspaces/`.
 *
 * One file per workspace rather than one file listing them all: hooks are many
 * short-lived processes, and a shared JSON file loses writes whenever any two
 * of them touch unrelated workspaces at once.
 *
 * The plugins only read this layer; nothing writes it today. A user who wants
 * an entry creates the file by hand, and it then outranks both workspace files
 * — so it is where they keep the last word over any repository they clone.
 */

import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { basename, join } from "node:path";

import { readWorkspaceFile } from "./workspace-config.mjs";

export function registryDir(env = process.env) {
  const home = String(env.OPENVIKING_HOME || "").trim();
  const base = home ? home.replace(/^~(?=$|\/)/, homedir()) : join(homedir(), ".openviking");
  return join(base, "workspaces");
}

/**
 * A readable name plus a hash, keyed on the workspace's identity rather than
 * its path wherever git supplies one.
 *
 * Two linked worktrees of one repository are one workspace — the same peer and
 * the same settings — and keying on the checkout path would silently split
 * them in two. Outside a repository there is no
 * identity but the path, so two `~/src/api` clones still get separate entries.
 */
export function slotName(root, identity = null) {
  const path = String(root || "");
  const key = identity ? identityKey(identity) : "path";
  const source = key === "path" ? path : key;
  const label = key.startsWith("remote:") ? key.split("/").pop() : basename(path);
  const readable = String(label || "").replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
  const digest = createHash("sha256").update(source).digest("hex").slice(0, 12);
  return `${readable ? `${readable}-` : ""}${digest}.json`;
}

export function entryPath(root, env = process.env, identity = null) {
  return join(registryDir(env), slotName(root, identity));
}

/**
 * The identity a stored entry is checked against. Path alone is not enough:
 * a directory can be deleted and a different repository cloned in its place,
 * and inheriting the old entry's peer would silently cross two projects.
 */
export function identityKey(identity) {
  // The normalized remote, so re-spelling origin (ssh ↔ https, or rotating an
  // embedded token) is not mistaken for a different repository.
  const remote = String(identity?.remote || "").trim();
  if (remote) return `remote:${remote}`;
  if (identity?.isGit) return `git:${identity.gitCommonDir || ""}`;
  return "path";
}

/**
 * Read this workspace's entry, or null.
 *
 * A stored entry whose identity contradicts the current one is treated as a
 * miss — negative evidence. Nothing is inherited from it.
 */
export function readEntry(root, { identity = null, env = process.env } = {}) {
  const path = entryPath(root, env, identity);
  const file = readWorkspaceFile(path, { layer: "registry" });
  if (!file.data) return { path, entry: null, warnings: file.warnings, conflict: false };

  const entry = file.data;
  const warnings = [...file.warnings];
  if (identity) {
    const expected = identityKey(identity);
    const stored = String(entry.identity || "");
    if (stored && stored !== expected) {
      warnings.push(
        `${path} was recorded for a different repository (${stored}); starting a fresh entry for ${expected}`,
      );
      return { path, entry: null, warnings, conflict: true };
    }
  }
  return { path, entry, warnings, conflict: false };
}
