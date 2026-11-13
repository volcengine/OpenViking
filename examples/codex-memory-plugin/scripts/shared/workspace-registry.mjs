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
import { realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve as resolvePath } from "node:path";

import { readWorkspaceFile } from "./workspace-config.mjs";

export function openvikingHome(env = process.env) {
  const home = String(env.OPENVIKING_HOME || "").trim();
  const base = home ? home.replace(/^~(?=$|\/)/, homedir()) : join(homedir(), ".openviking");
  return base;
}

export function registryDir(env = process.env) {
  return join(openvikingHome(env), "workspaces");
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
  // The registry is the one layer allowed to carry a connection pointer: it is
  // machine-local, lives under $HOME and is never committed, so a pointer here
  // cannot redirect a repository's credential chain without the user writing
  // it there by hand.
  const file = readWorkspaceFile(path, { layer: "registry", allowConnectionPointer: true });
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

/** A connection name is a file name, not a path: no separators, no traversal. */
const CONNECTION_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;

/**
 * The realpath of the nearest existing ancestor, so confinement still holds
 * when the pointed-to file has not been created yet.
 */
function realAncestor(path) {
  let probe = path;
  for (;;) {
    try {
      return realpathSync(probe);
    } catch {
      const parent = dirname(probe);
      if (parent === probe) return path;
      probe = parent;
    }
  }
}

/**
 * Resolve a registry entry's `connection` pointer to the user-private
 * connection file it names.
 *
 * Two shapes: `{ name }` picks `<home>/connections/<name>.conf` from the
 * user-private connection library, and `{ file }` points at one connection
 * file directly — the per-workspace equivalent of `OPENVIKING_CLI_CONFIG_FILE`.
 * Both resolve through the ordinary ovcli-shaped credential chain; nothing
 * here introduces a credential format, and the pointer never carries secret
 * material.
 *
 * A `file` pointer is confined to the user's OpenViking home, with the same
 * realpath guard `readWorkspaceFile` applies to workspace files: a registry
 * entry may not reach an arbitrary filesystem path, and a symlink planted at
 * the pointer's target cannot escape either. Both shapes honor
 * `OPENVIKING_HOME` the way the registry itself does.
 *
 * Returns `{ path }` — the file to feed `loadCredentialFiles` — plus the
 * warnings, and an empty `path` when there is no usable pointer, which is
 * today's behavior exactly.
 */
export function resolveConnectionPointer(connection, { env = process.env } = {}) {
  const warnings = [];
  const miss = { name: "", file: "", path: "", warnings };
  if (!connection || typeof connection !== "object" || Array.isArray(connection)) return miss;

  const home = openvikingHome(env);
  if (typeof connection.file === "string" && connection.file) {
    if (typeof connection.name === "string" && connection.name) {
      warnings.push("the registry entry names both connection 'file' and 'name'; the file wins");
    }
    const raw = connection.file.trim();
    // `~` and `~/...` expand against the OS home; a bare relative path resolves
    // against the OpenViking home rather than wherever the process was launched,
    // so one registry entry means the same file from every harness.
    const expanded = raw === "~"
      ? ""
      : raw.replace(/^~(?=\/|$)/, homedir());
    if (!expanded) {
      warnings.push(`ignored connection.file ${JSON.stringify(raw)}: it must name a file, not the home directory itself`);
      return miss;
    }
    const abs = isAbsolute(expanded) ? resolvePath(expanded) : resolvePath(join(home, expanded));
    if (abs === resolvePath(home)) {
      warnings.push(`ignored connection.file ${JSON.stringify(raw)}: it must name a file, not the OpenViking home itself`);
      return miss;
    }
    const realBase = realAncestor(home);
    // The parent, not the file: a pointer may name a file that does not exist
    // yet, and its own realpath would then collapse onto the home's, which is
    // inside — but its existing ancestors already tell whether it escapes.
    const realParent = realAncestor(dirname(abs));
    const rel = relative(realBase, realParent);
    if (rel.startsWith("..") || isAbsolute(rel)) {
      warnings.push(`ignored connection.file ${JSON.stringify(raw)}: it must stay inside ${home}`);
      return miss;
    }
    return { ...miss, file: raw, path: abs };
  }

  if (typeof connection.name === "string" && connection.name) {
    const name = connection.name.trim();
    if (!CONNECTION_NAME_RE.test(name)) {
      warnings.push(`ignored connection.name ${JSON.stringify(name)}: a connection name is letters, digits, dots, dashes or underscores`);
      return miss;
    }
    const path = join(home, "connections", `${name}.conf`);
    const realBase = realAncestor(home);
    const realParent = realAncestor(dirname(path));
    const rel = relative(realBase, realParent);
    if (rel.startsWith("..") || isAbsolute(rel)) {
      warnings.push(`connection.name ${JSON.stringify(name)} resolves outside ${home}`);
      return miss;
    }
    return { ...miss, name, path };
  }

  warnings.push("the registry entry's connection pointer has neither a usable 'name' nor a 'file'");
  return miss;
}
