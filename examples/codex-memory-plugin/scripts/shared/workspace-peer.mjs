// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Which peer a workspace writes its memories under.
 *
 * The peer used to be the working directory with every non-alphanumeric byte
 * turned into a dash, which made the identity an accident of where the
 * repository happened to sit: a clone on another machine, a rename, a worktree
 * or simply `cd examples/` each minted a separate, empty namespace. The default
 * is now git's own idea of the repository, so one project keeps one memory
 * wherever it is checked out.
 *
 * `peer.source` decides the rule. Presets cover the three answers most people
 * want; a template — or a list of templates tried in order — covers the rest.
 * The old behaviour is still one word away, byte for byte.
 */

import { legacySanitize, resolveWorkspaceIdentity, sanitizePeerId } from "./workspace-identity.mjs";

/**
 * `git` is the default, and it resolves only inside a repository: the remote
 * first, because that is the one name every clone agrees on, then the
 * repository root for a repo that has none. Anywhere else it resolves to
 * nothing and no peer is sent — a scratch folder or an app's per-task
 * directory would otherwise mint a fresh, empty namespace each time, so those
 * memories go to the user-level space instead. Naming such a directory is what
 * `peer.id` is for, and deriving one from a bare path is what `cwd` is for;
 * both are opt-in.
 *
 * No preset adds a prefix. A path-derived id starts with `-` on POSIX, so it
 * cannot collide with a remote-derived one; anyone who wants a prefix writes
 * their own template.
 */
export const PEER_SOURCE_PRESETS = {
  git: ["{git_remote}", "{git_root}"],
  cwd: ["{cwd}"],
  none: [],
};

export const DEFAULT_PEER_SOURCE = "git";

const VARIABLE_RE = /\{([a-z_]+)\}/g;

export function deriveWorkspacePeerId(cwd) {
  return legacySanitize(cwd);
}

/**
 * Normalize `peer.source` — a preset name, a template, or a list — to templates.
 *
 * A bare string with no `{` is a misspelled preset, not a template: `Git` or
 * `gti` would otherwise become a peer literally named `Git`, quietly stranding
 * the workspace's memories in a namespace nobody meant to create. Such a value
 * warns and falls back to the default chain. An array is left alone — writing a
 * list is explicit enough to mean the constant it contains.
 */
export function peerSourceTemplates(source, onWarn = null) {
  if (Array.isArray(source)) return source.map(String).filter(Boolean);
  const raw = String(source ?? "").trim();
  if (!raw) return PEER_SOURCE_PRESETS[DEFAULT_PEER_SOURCE];
  if (Object.hasOwn(PEER_SOURCE_PRESETS, raw)) return PEER_SOURCE_PRESETS[raw];
  if (raw.includes("{")) return [raw];

  const message = `OpenViking: ignored peer.source ${JSON.stringify(raw)}: it is neither a preset `
    + `(${Object.keys(PEER_SOURCE_PRESETS).join(", ")}) nor a template such as "team-{dir}". `
    + `Falling back to ${DEFAULT_PEER_SOURCE}.`;
  if (typeof onWarn === "function") onWarn(message);
  else process.stderr.write(`${message}\n`);
  return PEER_SOURCE_PRESETS[DEFAULT_PEER_SOURCE];
}

/**
 * Substitute one template, or return "" when any variable it names is empty.
 *
 * All-or-nothing on purpose: a half-resolved template like `git-` would be a
 * silently shared identity, so an empty variable falls through to the next
 * template instead.
 */
export function renderPeerTemplate(template, vars) {
  const text = String(template || "");
  if (!text) return "";
  let empty = false;
  const rendered = text.replace(VARIABLE_RE, (match, name) => {
    if (!Object.hasOwn(vars, name)) {
      empty = true;
      return "";
    }
    const value = String(vars[name] ?? "");
    if (!value) empty = true;
    return value;
  });
  return empty ? "" : rendered;
}

/**
 * The peer this process should send, and where it came from.
 *
 * `source` keeps its three values — call sites compare it against the literal
 * `"workspace"` to decide whether a session pin may be reused — while `origin`
 * names the template that actually produced the id, and `legacyPeerId` carries
 * the pre-git id whenever it differs, so recall can still reach memories
 * written under it.
 */
export function resolveEffectivePeerId({ cfg = {}, cwd = "", identity = null, env = process.env, onWarn = null } = {}) {
  const explicit = String(cfg.peerId || "").trim();
  if (explicit) return { peerId: explicit, source: "explicit", origin: "explicit", legacyPeerId: "" };

  // `OPENVIKING_WORKSPACE_PEER=0` predates `peer.source` and still means "none".
  if (cfg.workspacePeer === false) return { peerId: "", source: "none", origin: "disabled", legacyPeerId: "" };

  const templates = peerSourceTemplates(cfg.peerSource, onWarn);
  if (!templates.length) return { peerId: "", source: "none", origin: "none", legacyPeerId: "" };

  // `harness` is composed here rather than in the identity, whose result is
  // cached on disk under a cwd-only key — two harnesses in one directory would
  // otherwise read each other's peer back out of that cache.
  const vars = {
    ...((identity || resolveWorkspaceIdentity({ cwd, env })).vars || {}),
    harness: sanitizePeerId(cfg.harness || cfg.clientId || ""),
  };
  const legacyPeerId = deriveWorkspacePeerId(cwd);
  for (const template of templates) {
    const peerId = renderPeerTemplate(template, vars);
    if (!peerId) continue;
    return {
      peerId,
      source: "workspace",
      origin: template,
      legacyPeerId: peerId === legacyPeerId ? "" : legacyPeerId,
    };
  }
  // No peer, but the pre-git id is still what earlier sessions in this
  // directory wrote under, so recall keeps reaching it.
  return { peerId: "", source: "none", origin: "unresolved", legacyPeerId };
}
