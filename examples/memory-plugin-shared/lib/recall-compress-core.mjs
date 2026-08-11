import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export const NO_RELEVANT_MEMORY = "NO_RELEVANT_MEMORY";
export const DIGEST_HEADER = "OpenViking memory digest:";
const COMPRESS_OK = "ok";
const COMPRESS_EMPTY = "empty";
const COMPRESS_FAILED = "failed";

// Medium-constraint prompt: state the goal and two structural floors, but no hard
// bullet-length contract. Hard per-bullet limits pin the digest to headline
// density; leaving it unconstrained lets small models rewrite long URIs into dead
// links, which the URI repair below cleans up.
export function buildRecallCompressionPrompt({ query, rendered, maxBullets = 6 }) {
  return `You are a memory relevance compressor utility.
Do not use any tools. Do not investigate. Only transform the given text.

User query:
${query}

Retrieved OpenViking context fragments:
${rendered}

Write a memory digest for a coding agent about to answer that query. Keep the
concrete facts (paths, identifiers, decisions, constraints); drop pleasantries
and conversational filler.

Format rules:
- Group related facts by topic, one bullet per topic, at most ${maxBullets} bullets.
- Start every bullet with "- ".
- End every bullet with its source, copied verbatim from the fragments above:
  "来源：viking://..." or "source: viking://...". Never edit, shorten, or invent a URI.
- Output the digest body only. No preamble, no closing remark.

If nothing above is relevant to the query, output exactly: ${NO_RELEVANT_MEMORY}`;
}

function editDistance(a, b) {
  if (a === b) return 0;
  const rows = a.length + 1;
  const cols = b.length + 1;
  let prev = Array.from({ length: cols }, (_, i) => i);
  for (let i = 1; i < rows; i += 1) {
    const cur = [i];
    for (let j = 1; j < cols; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost);
    }
    prev = cur;
  }
  return prev[cols - 1];
}

function nearestUri(candidate, validUris) {
  let best = "";
  let bestDistance = Infinity;
  for (const uri of validUris) {
    const distance = editDistance(candidate, uri);
    if (distance < bestDistance) {
      best = uri;
      bestDistance = distance;
    }
  }
  // Only repair near-misses; an unrelated hallucination is dropped instead.
  const tolerance = Math.max(4, Math.floor(candidate.length * 0.25));
  return bestDistance <= tolerance ? best : "";
}

/**
 * Small models occasionally mangle long URIs. Snap every cited URI back onto the
 * set the server actually returned, and drop bullets whose citation cannot be
 * recovered so the digest never carries a dead link.
 */
export function repairDigestUris(digest, validUris = []) {
  const text = String(digest || "");
  if (!text) return "";
  const valid = validUris.map((uri) => String(uri || "").trim()).filter(Boolean);
  if (!valid.length) return text;
  const validSet = new Set(valid);

  const lines = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim().startsWith("- ")) {
      lines.push(line);
      continue;
    }
    let dropped = false;
    const repaired = line.replace(/viking:\/\/[^\s<>"')\]]+/g, (uri) => {
      if (validSet.has(uri)) return uri;
      const nearest = nearestUri(uri, valid);
      if (nearest) return nearest;
      dropped = true;
      return uri;
    });
    if (!dropped) lines.push(repaired);
  }
  return lines.join("\n").trim();
}

export function normalizeCompressedContext(raw, maxChars = 4000, maxBullets = 6) {
  const text = String(raw || "").trim();
  if (!text) return null;
  if (text.toUpperCase() === NO_RELEVANT_MEMORY) return "";
  const bullets = text.split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => /^[-*]\s+/.test(line) && line.includes("viking://"))
    .slice(0, Math.max(1, maxBullets))
    .map((line) => `- ${line.replace(/^[-*]\s+/, "").slice(0, 500).trim()}`);
  if (!bullets.length) return null;
  return (`${DIGEST_HEADER}\n${bullets.join("\n")}`).slice(0, Math.max(100, maxChars));
}

export function recallDigestCacheKey({
  query = "",
  rendered = "",
  entries = [],
  maxInputChars = 18000,
  maxBullets = 6,
} = {}) {
  const uris = entries.map((entry) => String(entry?.uri || "").trim()).filter(Boolean).sort();
  const source = JSON.stringify({
    version: 2,
    query: String(query),
    rendered: String(rendered).slice(0, maxInputChars),
    uris,
    maxInputChars,
    maxBullets,
  });
  return createHash("sha256").update(source).digest("hex");
}

async function readCache(path) {
  if (!path) return null;
  try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; }
}

async function writeCache(path, value) {
  if (!path) return;
  try {
    await mkdir(dirname(path), { recursive: true });
    const tmp = `${path}.tmp`;
    await writeFile(tmp, JSON.stringify(value));
    await rename(tmp, path);
  } catch { /* best effort */ }
}

export async function compressRecallContext({
  query,
  rendered,
  entries = [],
  cfg = {},
  runCompressor,
  cachePath = "",
  now = 0,
}) {
  const input = String(rendered || "").trim();
  if (!input) return { status: COMPRESS_EMPTY, context: "" };
  const minChars = Math.max(0, Number(cfg.recallCompressMinInputChars ?? 1500));
  if (input.length < minChars) return { status: COMPRESS_OK, context: input };

  const maxInputChars = Math.max(1000, Number(cfg.recallCompressMaxInputChars || 18000));
  const maxBullets = Math.max(1, Number(cfg.recallCompressMaxBullets || 6));
  const key = recallDigestCacheKey({
    query,
    rendered: input,
    entries,
    maxInputChars,
    maxBullets,
  });
  const cached = await readCache(cachePath);
  if (cached?.key === key && typeof cached.digest === "string") {
    return { status: COMPRESS_OK, context: cached.digest };
  }

  const prompt = buildRecallCompressionPrompt({
    query,
    rendered: input.slice(0, maxInputChars),
    maxBullets,
  });
  const raw = await runCompressor(prompt);
  const normalized = normalizeCompressedContext(raw, 4000, maxBullets);
  if (normalized === null) return { status: COMPRESS_FAILED, context: "" };
  if (!normalized) return { status: COMPRESS_EMPTY, context: "" };

  const validUris = entries.map((entry) => entry?.uri).filter(Boolean);
  const digest = repairDigestUris(normalized, validUris.length
    ? validUris
    : (input.match(/viking:\/\/[^\s<>"']+/g) || []));
  if (!digest) return { status: COMPRESS_FAILED, context: "" };

  await writeCache(cachePath, { key, digest, updatedAt: now || 0 });
  return { status: COMPRESS_OK, context: digest };
}
