/**
 * Session-start profile injection helper.
 *
 * Builds a <user-profile> + <available-memories> block from
 *   viking://user/<space>/memories/profile.md
 *   viking://user/<space>/memories/preferences/   (ls with abstracts)
 *   viking://user/<space>/memories/entities/      (ls with abstracts)
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

const USER_RESERVED_DIRS = new Set(["memories"]);
let _userSpaceCache = null;

/**
 * Mirrors auto-recall.mjs resolveScopeSpace for scope="user" (lines 123-147).
 * Duplicated rather than imported to avoid coupling session-start to
 * auto-recall's module-level fetchJSON closure.
 */
async function resolveUserSpace(fetchJSON) {
  if (_userSpaceCache) return _userSpaceCache;

  let fallbackSpace = "default";
  const status = await fetchJSON("/api/v1/system/status");
  if (status.ok && typeof status.result?.user === "string" && status.result.user.trim()) {
    fallbackSpace = status.result.user.trim();
  }

  const lsRes = await fetchJSON(
    `/api/v1/fs/ls?uri=${encodeURIComponent("viking://user")}&output=original`,
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

async function readProfile(fetchJSON, profileUri) {
  const res = await fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(profileUri)}`);
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
async function lsDir(fetchJSON, dirUri) {
  const url = `/api/v1/fs/ls?uri=${encodeURIComponent(dirUri)}&output=agent&recursive=true&abs_limit=512&node_limit=512`;
  const res = await fetchJSON(url);
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

function formatListing(headerUri, entries, budgetTokens) {
  if (entries.length === 0) return { lines: [], used: 0, dropped: 0 };
  // Header is the full directory URI; child lines are relative paths so the
  // agent can reconstruct each leaf's full URI by concatenation while the
  // listing itself stays compact.
  const header = `  ${headerUri}/`;
  const headerTokens = estimateTokens(header);
  // If the header alone busts the budget, emit just a one-line stub instead
  // of silently violating the cap (Copilot review point).
  if (headerTokens > budgetTokens) {
    const stub = `  ${headerUri}/  (${entries.length} entries, budget too tight; use \`memory_recall\`)`;
    return { lines: [stub], used: estimateTokens(stub), dropped: entries.length };
  }
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
      const remaining = entries.length - i;
      const tail = `    ... +${remaining} more, use \`memory_recall\``;
      const tailTokens = estimateTokens(tail);
      // Only emit the tail if it fits; otherwise the listing closes silently
      // rather than violating the cap to advertise its own truncation.
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
 * Build the profile injection block.
 *
 * Returns null when neither profile.md nor either listing has any content.
 * The returned `block` is just the inner <user-profile>/<available-memories>
 * payload — the caller wraps it in <openviking-context source="...">.
 *
 * @param {Function} fetchJSON  ov-session.mjs:makeFetchJSON closure
 * @param {number} totalBudgetTokens  chars/4 budget, total for the whole block
 * @returns {Promise<null | {
 *   block: string, chars: number, tokens: number, profileUri: string,
 *   profileChars: number, prefCount: number, entCount: number,
 *   droppedPref: number, droppedEnt: number,
 * }>}
 */
export async function buildProfileBlock(fetchJSON, totalBudgetTokens) {
  const space = await resolveUserSpace(fetchJSON);
  const profileUri = `viking://user/${space}/memories/profile.md`;
  const prefUri = `viking://user/${space}/memories/preferences`;
  const entUri = `viking://user/${space}/memories/entities`;

  const [profile, prefs, ents] = await Promise.all([
    readProfile(fetchJSON, profileUri),
    lsDir(fetchJSON, prefUri),
    lsDir(fetchJSON, entUri),
  ]);

  if (!profile && prefs.length === 0 && ents.length === 0) return null;

  // Profile gets up to half the total budget; listings split the rest.
  // Sub-cap protects against a runaway profile blowing the listing budget.
  const profileBudget = Math.floor(totalBudgetTokens / 2);
  const profileTrunc = profile ? elideProfile(profile, profileBudget) : null;
  const profileTokens = estimateTokens(profileTrunc || "");

  const listingBudget = Math.max(0, totalBudgetTokens - profileTokens);
  const halfListing = Math.floor(listingBudget / 2);
  const prefBlock = formatListing(prefUri, prefs, halfListing);
  const entBudget = Math.max(0, listingBudget - prefBlock.used);
  const entBlock = formatListing(entUri, ents, entBudget);

  const lines = [];
  if (profileTrunc) {
    lines.push(`<user-profile uri="${profileUri}">`);
    lines.push(profileTrunc);
    lines.push(`</user-profile>`);
  }
  if (prefBlock.lines.length > 0 || entBlock.lines.length > 0) {
    lines.push(`<available-memories>`);
    lines.push(...prefBlock.lines);
    lines.push(...entBlock.lines);
    lines.push(`</available-memories>`);
  }

  const block = lines.join("\n");
  return {
    block,
    chars: block.length,
    tokens: estimateTokens(block),
    profileUri,
    profileChars: profile?.length ?? 0,
    prefCount: prefs.length,
    entCount: ents.length,
    droppedPref: prefBlock.dropped,
    droppedEnt: entBlock.dropped,
  };
}
