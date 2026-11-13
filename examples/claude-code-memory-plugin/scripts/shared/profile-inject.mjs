// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Session-start profile injection helper.
 *
 * Builds a <user-profile> + <available-memories> (+ <available-skills>) block from
 *   viking://user/<space>/memories/profile.md
 *   viking://user/<space>/memories/preferences/   (ls with abstracts)
 *   viking://user/<space>/memories/entities/      (ls with abstracts)
 *   GET /api/v1/skills                            (own + account-shared skills)
 *
 * With an actorPeerId the same three paths under
 * viking://user/<space>/peers/<peer>/memories/ are read too, and each section
 * lists the user root first, then the peer root, under one shared budget.
 *
 * Budget enforced via the CJK-aware estimateTokens() below — codepoint >=
 * 0x3000 counts at 1.5 tokens, else chars/4. The estimator is exported so
 * callers (e.g. session-start.mjs) can log token counts that match what
 * the budget logic actually sees.
 *
 * Returned block is the *inner* content only (no outer <openviking-context>);
 * session-start.mjs composes the outer wrapper so the archive block can sit
 * alongside in a single context envelope.
 */

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const USER_RESERVED_DIRS = new Set(["memories"]);
let _userSpaceCache = null;

/**
 * Mirrors auto-recall.mjs resolveScopeSpace for scope="user" (lines 123-147).
 * Duplicated rather than imported to avoid coupling session-start to
 * auto-recall's module-level fetchJSON closure.
 */
async function resolveUserSpace(fetchJSON, actorPeerId = "") {
  if (_userSpaceCache) return _userSpaceCache;

  let fallbackSpace = "default";
  const status = await fetchJSON("/api/v1/system/status");
  if (status.ok && typeof status.result?.user === "string" && status.result.user.trim()) {
    fallbackSpace = status.result.user.trim();
  }

  const lsRes = await fetchJSON(
    `/api/v1/fs/ls?uri=${encodeURIComponent("viking://user")}&output=original`,
    {},
    { actorPeerId },
  );
  if (lsRes.ok && Array.isArray(lsRes.result)) {
    const spaces = lsRes.result
      .filter((e) => e?.isDir)
      .map((e) => (typeof e.name === "string" ? e.name.trim() : ""))
      .filter((n) => n && !n.startsWith(".") && !USER_RESERVED_DIRS.has(n));
    if (spaces.length > 0) {
      if (spaces.includes(fallbackSpace)) { _userSpaceCache = fallbackSpace; return fallbackSpace; }
      if (spaces.includes("default")) { _userSpaceCache = "default"; return "default"; }
      if (spaces.length === 1) { _userSpaceCache = spaces[0]; return spaces[0]; }
    }
  }
  _userSpaceCache = fallbackSpace;
  return fallbackSpace;
}

/**
 * Token estimate that splits CJK from the rest. The rest of the plugin uses a
 * flat chars/4 heuristic, which silently undercounts CJK content by 4-6× and
 * makes a "5000 token budget" really worth ~1k real tokens for Chinese text.
 *
 * Rule:
 *   - codepoint >= 0x3000 → CJK / Hiragana / Katakana / Hangul / CJK
 *     punctuation / fullwidth ASCII; counted at 1.5 tokens/char (empirical
 *     average for cl100k_base on Chinese; Claude's tokenizer is in the same
 *     ballpark).
 *   - everything else → chars/4 (the standard English-ish heuristic).
 *
 * Single linear pass, no dependencies. Errs on the side of overcounting CJK
 * by ~10-20% in the worst case (some common Chinese phrases compress better
 * than 1.5 tokens/char), which is the safe direction for budget enforcement.
 */
export function estimateTokens(text) {
  if (!text) return 0;
  let cjk = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) >= 0x3000) cjk++;
  }
  const other = text.length - cjk;
  return Math.ceil(cjk * 1.5 + other / 4);
}

/**
 * Convert a token budget to a max-chars budget that respects this content's
 * actual CJK density. Avoids the chars/4 trap where a "5000 token" sub-cap
 * yields 20000 chars of pure-CJK text → 30000 actual tokens (6× over).
 *
 * For an empty/missing string, returns 0.
 */
function tokensToCharsBudget(content, maxTokens) {
  if (!content) return 0;
  let cjk = 0;
  for (let i = 0; i < content.length; i++) {
    if (content.charCodeAt(i) >= 0x3000) cjk++;
  }
  const ratio = cjk / content.length;
  const tokensPerChar = ratio * 1.5 + (1 - ratio) * 0.25;
  return Math.floor(maxTokens / Math.max(tokensPerChar, 0.25));
}

async function readProfile(fetchJSON, profileUri, actorPeerId = "") {
  const res = await fetchJSON(
    `/api/v1/content/read?uri=${encodeURIComponent(profileUri)}`,
    {},
    { actorPeerId },
  );
  if (!res.ok || typeof res.result !== "string") return null;
  const trimmed = res.result.trim();
  return trimmed || null;
}

/**
 * Recursive ls of a memory directory, flattening to .md leaves.
 *
 * Memory layout under preferences/ and entities/ is two-level:
 *   <dir>/<owner_name>/<topic>.md
 * so we use the server's recursive=true flag and filter to leaf .md files.
 * `rel_path` (e.g. "zhengxiao.wu/pr_workflow.md") is preserved as display name
 * to keep owner-namespacing visible and unambiguous when multiple owners exist.
 */
async function lsDir(fetchJSON, dirUri, actorPeerId = "") {
  const url = `/api/v1/fs/ls?uri=${encodeURIComponent(dirUri)}&output=agent&recursive=true&abs_limit=512&node_limit=512`;
  const res = await fetchJSON(url, {}, { actorPeerId });
  if (!res.ok || !Array.isArray(res.result)) return [];
  return res.result
    .filter((e) => !e.isDir)
    .map((e) => {
      const rel = typeof e.rel_path === "string" && e.rel_path
        ? e.rel_path
        : (typeof e.name === "string" ? e.name : "");
      return {
        name: rel,
        abstract: typeof e.abstract === "string" ? e.abstract.trim() : "",
      };
    })
    .filter((e) => e.name && e.name.endsWith(".md"))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * When profile exceeds its sub-cap, keep the head (identity block + first
 * timeline events) and the tail (most-recent events), drop the middle. This
 * preserves both stable identity facts (top of file) and most-recent activity
 * (bottom of file) — only the noisy middle timeline is sacrificed.
 *
 * Layout:
 *   <first HEAD_LINES lines>
 *   ... [profile middle elided] ...
 *   <as many trailing lines as fit in remaining budget>
 *
 * Falls back to head-only truncate when the file is too short to elide
 * meaningfully (<HEAD_LINES + 4 lines) or the budget is too tight to fit
 * both head and a useful tail.
 */
function elideProfile(content, maxTokens) {
  // Compute char budget from token budget using *this* content's CJK density,
  // not chars/4 — otherwise the truncated string can still blow the token cap
  // for CJK-heavy profiles (Copilot review point).
  const maxChars = Math.max(400, tokensToCharsBudget(content, maxTokens));
  if (estimateTokens(content) <= maxTokens) return content;

  const HEAD_LINES = 8;
  const ELLIPSIS = "\n... [profile middle elided] ...\n";
  const lines = content.split("\n");

  const fallbackHeadTruncate = () =>
    content.slice(0, maxChars).trimEnd() + "\n... [profile truncated]";

  if (lines.length <= HEAD_LINES + 4) return fallbackHeadTruncate();

  const head = lines.slice(0, HEAD_LINES).join("\n");
  const reserveForTail = maxChars - head.length - ELLIPSIS.length;
  if (reserveForTail < 200) return fallbackHeadTruncate();

  let tailChars = 0;
  let tailStart = lines.length;
  for (let i = lines.length - 1; i > HEAD_LINES; i--) {
    const lineLen = lines[i].length + 1;
    if (tailChars + lineLen > reserveForTail) break;
    tailChars += lineLen;
    tailStart = i;
  }
  if (tailStart >= lines.length - 1) return fallbackHeadTruncate();

  return `${head}${ELLIPSIS}${lines.slice(tailStart).join("\n")}`;
}

const MEMORY_MORE_HINT = "use `memory_recall`";

function formatListing(headerUri, entries, budgetTokens, moreHint = MEMORY_MORE_HINT) {
  if (entries.length === 0) return { lines: [], used: 0, dropped: 0 };
  // Header is the full directory URI; child lines are relative paths so the
  // agent can reconstruct each leaf's full URI by concatenation while the
  // listing itself stays compact.
  const header = `  ${headerUri}/`;
  const headerTokens = estimateTokens(header);
  // If the header alone busts the budget, emit just a one-line stub instead
  // of silently violating the cap (Copilot review point).
  const stubListing = () => {
    const stub = `  ${headerUri}/  (${entries.length} entries, budget too tight; ${moreHint})`;
    return { lines: [stub], used: estimateTokens(stub), dropped: entries.length };
  };
  if (headerTokens > budgetTokens) return stubListing();
  const lines = [header];
  let used = headerTokens;
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    const desc = e.abstract
      ? ` — ${e.abstract.replace(/\s+/g, " ").slice(0, 200)}`
      : "";
    const line = `    - ${e.name}${desc}`;
    const tokens = estimateTokens(line);
    if (used + tokens > budgetTokens) {
      let remaining = entries.length - i;
      const tailFor = (n) => `    ... +${n} more, ${moreHint}`;
      // A listing that stops without the tail hides that anything was cut, so
      // give entries back until it fits.
      while (used + estimateTokens(tailFor(remaining)) > budgetTokens && lines.length > 1) {
        used -= estimateTokens(lines.pop());
        remaining++;
      }
      // A bare header would read as an empty directory.
      if (lines.length === 1) return stubListing();
      const tail = tailFor(remaining);
      const tailTokens = estimateTokens(tail);
      if (used + tailTokens <= budgetTokens) {
        lines.push(tail);
        return { lines, used: used + tailTokens, dropped: remaining };
      }
      return { lines, used, dropped: remaining };
    }
    lines.push(line);
    used += tokens;
  }
  return { lines, used, dropped: 0 };
}

/**
 * Preferences and entities of each root in order, user root first. A root
 * with any entry takes its fair share of what is left, and within a root the
 * preferences get half and the entities the rest. With one root this is the
 * single-scope split unchanged.
 */
function formatListingGroups(scopes, budgetTokens) {
  const populated = scopes.filter((s) => s.prefs.length > 0 || s.ents.length > 0);
  const lines = [];
  let used = 0;
  let droppedPref = 0;
  let droppedEnt = 0;
  for (let i = 0; i < populated.length; i++) {
    const s = populated[i];
    const share = Math.floor(Math.max(0, budgetTokens - used) / (populated.length - i));
    const prefBlock = formatListing(s.prefUri, s.prefs, Math.floor(share / 2));
    const entBlock = formatListing(s.entUri, s.ents, Math.max(0, share - prefBlock.used));
    lines.push(...prefBlock.lines, ...entBlock.lines);
    used += prefBlock.used + entBlock.used;
    droppedPref += prefBlock.dropped;
    droppedEnt += entBlock.dropped;
  }
  return { lines, used, droppedPref, droppedEnt };
}

// Below this a trimmed peer profile is mostly elision markers; point at it instead.
const PEER_PROFILE_MIN_TOKENS = 200;
const PEER_PROFILE_POINTER = "Omitted for length; read this uri with the read tool.";

/**
 * Drop peer-profile lines that already appear in the user profile, so the
 * shared budget is not spent twice on the same fact. Returns null when no
 * non-empty line is left.
 */
function dedupeProfileLines(peerProfile, userProfile) {
  const seen = new Set(
    String(userProfile || "").split("\n").map((l) => l.trim()).filter(Boolean),
  );
  const kept = [];
  for (const line of peerProfile.split("\n")) {
    const trimmed = line.trim();
    if (trimmed && seen.has(trimmed)) continue;
    // Removed lines must not leave runs of blank lines behind.
    if (!trimmed && (kept.length === 0 || !kept[kept.length - 1].trim())) continue;
    kept.push(line);
  }
  // Headings alone carry nothing worth a second <user-profile> element.
  const hasContent = kept.some((l) => l.trim() && !/^#{1,6}\s/.test(l.trim()));
  return hasContent ? kept.join("\n").trim() : null;
}

const AGENT_SKILLS_ROOT = "viking://agent/skills";
// The server caps each skill root at node_limit, so the two roots together
// can return twice this many; the token budget below decides what fits.
const SKILL_CATALOG_NODE_LIMIT = 200;
const SKILL_DESCRIPTION_TOKENS = 40;
const SKILL_MORE_HINT = "search OpenViking skills to find the rest";
const SKILL_USAGE_LINE =
  "  OpenViking skills (stored in OpenViking, not local files). Before following one, read <dir>/<name>/SKILL.md with the OpenViking read tool.";
// Skill descriptions are user-written, and the shared root is account-wide:
// never let one close or open the envelope the catalog sits in.
const ENVELOPE_TAG_RE = /<\/?(?:openviking-context|available-skills|available-memories|user-profile|memory)\b[^>]*>/gi;

function sanitizeInline(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(ENVELOPE_TAG_RE, (tag) => tag.replace(/</g, "&lt;").replace(/>/g, "&gt;"));
}

function truncateToTokens(text, maxTokens) {
  if (estimateTokens(text) <= maxTokens) return text;
  return `${text.slice(0, Math.max(1, tokensToCharsBudget(text, maxTokens) - 1)).trimEnd()}…`;
}

/**
 * Both skill roots from one GET /api/v1/skills, grouped by root with the
 * user's own skills first. A shared skill whose name the user also has is
 * dropped: the private one is the one the agent should load. Servers without
 * the endpoint, or any failure, yield no catalog.
 */
async function fetchSkillCatalog(fetchJSON, actorPeerId = "") {
  const res = await fetchJSON(
    `/api/v1/skills?node_limit=${SKILL_CATALOG_NODE_LIMIT}`,
    {},
    { actorPeerId },
  );
  const skills = res.ok && Array.isArray(res.result?.skills) ? res.result.skills : [];
  const own = [];
  const shared = [];
  for (const skill of skills) {
    const name = typeof skill?.name === "string" ? skill.name.trim() : "";
    const uri = typeof skill?.uri === "string" ? skill.uri.replace(/\/+$/, "") : "";
    if (!name || !uri.includes("/")) continue;
    const entry = {
      name,
      root: uri.slice(0, uri.lastIndexOf("/")),
      abstract: truncateToTokens(sanitizeInline(skill.description), SKILL_DESCRIPTION_TOKENS),
    };
    (uri.startsWith(`${AGENT_SKILLS_ROOT}/`) ? shared : own).push(entry);
  }
  const ownNames = new Set(own.map((e) => e.name));
  const byName = (a, b) => a.name.localeCompare(b.name);
  return [
    own.sort(byName),
    shared.filter((e) => !ownNames.has(e.name)).sort(byName),
  ].filter((group) => group.length > 0);
}

function renderSkillGroups(groups, budgetTokens, withDescriptions) {
  const lists = groups.map((g) => (withDescriptions ? g : g.map((e) => ({ ...e, abstract: "" }))));
  const fullCost = lists.map((entries) => formatListing(entries[0].root, entries, Infinity, SKILL_MORE_HINT).used);
  const lines = [];
  let used = 0;
  let dropped = 0;
  for (let i = 0; i < lists.length; i++) {
    // A group takes its fair share, or everything the later groups leave
    // when listed in full, whichever is larger.
    const remaining = budgetTokens - used;
    const laterCost = fullCost.slice(i + 1).reduce((a, b) => a + b, 0);
    const share = Math.max(Math.floor(remaining / (lists.length - i)), remaining - laterCost);
    const block = formatListing(lists[i][0].root, lists[i], share, SKILL_MORE_HINT);
    lines.push(...block.lines);
    used += block.used;
    dropped += block.dropped;
  }
  return { lines, used, dropped };
}

/**
 * <available-skills> within its own token budget: every entry with its
 * description when that fits, otherwise names only (more skills stay
 * visible, with a "+N more" tail), and a one-line count when not even one
 * name fits.
 */
function formatSkillCatalog(groups, budgetTokens) {
  const count = groups.reduce((n, g) => n + g.length, 0);
  if (count === 0) return { lines: [], used: 0, dropped: 0, count };
  const open = "<available-skills>";
  const close = "</available-skills>";
  const frameTokens = estimateTokens(open) + estimateTokens(SKILL_USAGE_LINE) + estimateTokens(close);
  const listingBudget = Math.max(0, budgetTokens - frameTokens);
  let body = renderSkillGroups(groups, listingBudget, true);
  if (body.dropped > 0) body = renderSkillGroups(groups, listingBudget, false);
  if (body.dropped >= count) {
    const stub = `${open}${count} OpenViking skills; search OpenViking skills to find them.${close}`;
    if (estimateTokens(stub) > budgetTokens) return { lines: [], used: 0, dropped: count, count };
    return { lines: [stub], used: estimateTokens(stub), dropped: count, count };
  }
  return {
    lines: [open, SKILL_USAGE_LINE, ...body.lines, close],
    used: frameTokens + body.used,
    dropped: body.dropped,
    count,
  };
}

/**
 * Build the profile injection block.
 *
 * Returns null when no profile, no listing, and no skill catalog has any
 * content. The returned `block` is just the inner
 * <user-profile>/<available-memories>/<available-skills> payload — the caller
 * wraps it in <openviking-context source="...">.
 *
 * @param {Function} fetchJSON  ov-session.mjs:makeFetchJSON closure
 * @param {number} totalBudgetTokens  budget for the profiles and memory listings
 * @param {string} [actorPeerId]  adds the peer root alongside the user root
 * @param {{ skillCatalog?: boolean, skillCatalogTokenBudget?: number, sessionStartMaxBytes?: number }} [options]
 *   callers pass their resolved plugin config: its skillCatalog knob adds
 *   <available-skills>, budgeted separately by skillCatalogTokenBudget.
 *   Without it the block is unchanged.
 * @returns {Promise<null | {
 *   block: string, chars: number, tokens: number, profileUri: string,
 *   profileChars: number, peerProfileUri: string, peerProfileChars: number,
 *   prefCount: number, entCount: number,
 *   droppedPref: number, droppedEnt: number,
 *   skillCount: number, droppedSkill: number, skillTokens: number,
 * }>}
 *   Counts are summed across both roots; profileChars covers both raw profiles.
 */
export async function buildProfileBlock(fetchJSON, totalBudgetTokens, actorPeerId = "", options = {}) {
  const { skillCatalog = false, skillCatalogTokenBudget = 0, sessionStartMaxBytes = 0 } = options;
  // Hosts that spill (Claude Code, Codex) or drop (ZCode) oversized hook output
  // get a byte cap. An estimated token is about 4 UTF-8 bytes at most, so the
  // token budgets shrink to fit under it.
  const capTokens = sessionStartMaxBytes > 0
    ? Math.max(0, Math.floor(sessionStartMaxBytes / 4) - 50)
    : Infinity;
  const skillBudget = Math.min(skillCatalogTokenBudget, Math.floor(capTokens / 4));
  const space = await resolveUserSpace(fetchJSON, actorPeerId);

  // Profile, preferences and entities carry no message range, so extraction
  // writes the user's own facts to the user root even inside a peer session:
  // with an actor peer both roots hold memories, and both are read.
  const roots = [`viking://user/${space}/memories`];
  if (actorPeerId) roots.push(`viking://user/${space}/peers/${actorPeerId}/memories`);
  const scopes = roots.map((root) => ({
    profileUri: `${root}/profile.md`,
    prefUri: `${root}/preferences`,
    entUri: `${root}/entities`,
  }));

  // Every read is independent, and a hook on the SessionStart path pays for
  // every round trip.
  const reads = await Promise.all([
    ...scopes.flatMap((s) => [
      readProfile(fetchJSON, s.profileUri, actorPeerId),
      lsDir(fetchJSON, s.prefUri, actorPeerId),
      lsDir(fetchJSON, s.entUri, actorPeerId),
    ]),
    skillCatalog && skillBudget > 0
      ? fetchSkillCatalog(fetchJSON, actorPeerId)
      : [],
  ]);
  const skillGroups = reads.pop();
  scopes.forEach((s, i) => {
    [s.profile, s.prefs, s.ents] = reads.slice(i * 3, i * 3 + 3);
  });
  const [user, peer] = scopes;
  const skills = formatSkillCatalog(skillGroups, skillBudget);

  const peerProfile = peer?.profile ? dedupeProfileLines(peer.profile, user.profile) : null;
  if (
    !user.profile && !peerProfile
    && scopes.every((s) => s.prefs.length === 0 && s.ents.length === 0)
    && skills.lines.length === 0
  ) {
    return null;
  }

  const memoryBudget = Math.min(totalBudgetTokens, Math.max(0, capTokens - skills.used));
  // The profiles get up to half the total budget; listings split the rest.
  // Sub-cap protects against a runaway profile blowing the listing budget.
  // The user profile comes first; the peer profile only gets what it leaves.
  const profileBudget = Math.floor(memoryBudget / 2);
  const profileTrunc = user.profile ? elideProfile(user.profile, profileBudget) : null;
  const profileTokens = estimateTokens(profileTrunc || "");
  const profileLines = profileTrunc
    ? [`<user-profile uri="${user.profileUri}">`, profileTrunc, `</user-profile>`]
    : [];
  let peerProfileTokens = 0;
  if (peerProfile) {
    const peerBudget = profileBudget - profileTokens;
    // elideProfile keeps at least 400 chars (~600 tokens of CJK), so a trim
    // that still overruns the peer's share falls back to the pointer.
    const peerTrunc = estimateTokens(peerProfile) <= peerBudget || peerBudget >= PEER_PROFILE_MIN_TOKENS
      ? elideProfile(peerProfile, peerBudget)
      : null;
    if (peerTrunc !== null && estimateTokens(peerTrunc) <= peerBudget) {
      profileLines.push(`<user-profile uri="${peer.profileUri}">`, peerTrunc, `</user-profile>`);
      peerProfileTokens = estimateTokens(peerTrunc);
    } else {
      const pointer = `<user-profile uri="${peer.profileUri}">${PEER_PROFILE_POINTER}</user-profile>`;
      profileLines.push(pointer);
      peerProfileTokens = estimateTokens(pointer);
    }
  }

  const listingBudget = Math.max(0, memoryBudget - profileTokens - peerProfileTokens);
  const listings = formatListingGroups(scopes, listingBudget);

  let memoryLines = listings.lines.length > 0
    ? [`<available-memories>`, ...listings.lines, `</available-memories>`]
    : [];
  let skillLines = skills.lines;
  const render = () => [...profileLines, ...memoryLines, ...skillLines].join("\n");
  let block = render();
  // The estimate can still run over on multi-byte punctuation; the index goes
  // first, then the catalog.
  if (sessionStartMaxBytes > 0 && utf8Bytes(block) > sessionStartMaxBytes) {
    memoryLines = [];
    block = render();
  }
  if (sessionStartMaxBytes > 0 && utf8Bytes(block) > sessionStartMaxBytes) {
    skillLines = [];
    block = render();
  }
  if (!block) return null;

  const prefCount = scopes.reduce((n, s) => n + s.prefs.length, 0);
  const entCount = scopes.reduce((n, s) => n + s.ents.length, 0);
  return {
    block,
    chars: block.length,
    tokens: estimateTokens(block),
    profileUri: user.profileUri,
    profileChars: (user.profile?.length ?? 0) + (peer?.profile?.length ?? 0),
    peerProfileUri: peer?.profileUri ?? "",
    peerProfileChars: peer?.profile?.length ?? 0,
    prefCount,
    entCount,
    droppedPref: memoryLines.length ? listings.droppedPref : prefCount,
    droppedEnt: memoryLines.length ? listings.droppedEnt : entCount,
    skillCount: skills.count,
    droppedSkill: skillLines.length ? skills.dropped : skills.count,
    skillTokens: skillLines.length ? skills.used : 0,
  };
}

function utf8Bytes(text) {
  return Buffer.byteLength(String(text || ""), "utf8");
}

/**
 * Cut `text` to at most `maxBytes` UTF-8 bytes on a line boundary, marking the
 * cut. A non-positive cap returns the text unchanged.
 */
export function truncateToBytes(text, maxBytes) {
  const value = String(text || "");
  if (!(maxBytes > 0) || utf8Bytes(value) <= maxBytes) return value;
  const marker = "\n… [truncated]";
  const lines = value.split("\n");
  const kept = [];
  let used = utf8Bytes(marker);
  for (const line of lines) {
    const cost = utf8Bytes(line) + 1;
    if (used + cost > maxBytes) break;
    kept.push(line);
    used += cost;
  }
  return `${kept.join("\n")}${marker}`;
}

/**
 * Whether `block` is what this session was last given, recording it either
 * way. Resume reuses history that already holds the earlier injection, so an
 * unchanged block need not be sent again.
 */
export function isRepeatInjection(statePath, sessionId, block) {
  if (!statePath || !sessionId) return false;
  const digest = createHash("sha256").update(String(block || "")).digest("hex");
  let seen = {};
  try {
    seen = JSON.parse(readFileSync(statePath, "utf-8")) || {};
  } catch { /* first injection */ }
  const repeat = seen[sessionId] === digest;
  delete seen[sessionId];
  seen[sessionId] = digest;
  const ids = Object.keys(seen);
  for (const id of ids.slice(0, Math.max(0, ids.length - 200))) delete seen[id];
  try {
    mkdirSync(dirname(statePath), { recursive: true });
    writeFileSync(statePath, JSON.stringify(seen));
  } catch { /* best effort: a lost record only means one more injection */ }
  return repeat;
}
