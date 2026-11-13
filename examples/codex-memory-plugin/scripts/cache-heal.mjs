/**
 * Keep running Codex sessions working across a plugin upgrade.
 *
 * Codex expands ${PLUGIN_ROOT} to the versioned cache directory
 * (plugins/cache/<marketplace>/openviking-memory/<version>) once, when a
 * session loads its hooks, and an upgrade deletes the previous version right
 * away. Every session started before the upgrade then fails each hook with
 * MODULE_NOT_FOUND until it is restarted (openai/codex#31383).
 *
 * Each hook run records the version it runs from. When an older recorded
 * version's directory is gone, it is recreated as a link to the running one,
 * so the next hook in an older session runs this build instead of failing.
 * Codex only treats real directories as installed versions, so the links do
 * not change which version it activates or prunes. A version not seen for a
 * week is forgotten and its link removed.
 */

import { existsSync, lstatSync, mkdirSync, readFileSync, rmdirSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { getStateDir } from "./session-state.mjs";

const RETAIN_MS = 7 * 24 * 60 * 60 * 1000;
const PLUGIN_ROOT = fileURLToPath(new URL("..", import.meta.url));

function compareVersions(a, b) {
  const pa = a.split(/[.+-]/).map(Number);
  const pb = b.split(/[.+-]/).map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d;
  }
  return 0;
}

const isLink = (path) => { try { return lstatSync(path).isSymbolicLink(); } catch { return false; } };

// A Windows junction is removed as a directory, a symlink as a file.
function removeLink(path) {
  if (!isLink(path)) return;
  try { unlinkSync(path); } catch { rmdirSync(path); }
}

export function healRetiredVersions({ root = PLUGIN_ROOT, now = Date.now() } = {}) {
  try {
    root = root.replace(/[\\/]+$/, "");
    const version = basename(root);
    const manifest = JSON.parse(readFileSync(join(root, ".codex-plugin", "plugin.json"), "utf-8"));
    // Only a versioned cache install; a checkout or a local marketplace is left alone.
    if (manifest.version !== version || !/^\d+(\.\d+)*$/.test(version)) return;

    const recordPath = join(getStateDir(), "plugin-versions.json");
    let seen = {};
    try { seen = JSON.parse(readFileSync(recordPath, "utf-8")) || {}; } catch {}

    const kept = { [version]: now };
    for (const [old, at] of Object.entries(seen)) {
      if (old === version || !/^\d+(\.\d+)*$/.test(old)) continue;
      const oldRoot = join(dirname(root), old);
      if (!(now - at < RETAIN_MS) || compareVersions(old, version) > 0) {
        try { removeLink(oldRoot); } catch {}
        continue;
      }
      kept[old] = at;
      if (existsSync(oldRoot)) continue;
      try {
        removeLink(oldRoot); // points at a version Codex has since removed
        symlinkSync(root, oldRoot, "junction");
      } catch {}
    }

    mkdirSync(dirname(recordPath), { recursive: true });
    writeFileSync(recordPath, JSON.stringify(kept));
  } catch {}
}
