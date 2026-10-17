/**
 * Wire branch-tree reading for Kimi Code sessions.
 *
 * Ported from kimi-code `packages/agent-core-v2/src/wire/tree/tree.ts`
 * (verified against main, 2026-10-17). wire.jsonl is an append-only journal
 * with a branch tree inside it: undo/rewind appends `agent.switched` markers
 * and continues on a new branch, leaving the abandoned branch's records in
 * the file. The official reader (`wireService.read`) yields only the active
 * chain; decoding every raw line would capture turns the user retracted
 * (DESIGN.md §5.4).
 *
 * The port keeps the source's structure and names so the two can be diffed:
 * parseTree → segments + diagnostics; activeChain → the entries the active
 * branch actually owns, in chronological order.
 */

export const MAIN_BRANCH = "main";
export const AGENT_SWITCHED_TYPE = "agent.switched";

function readSwitchEdge(record, line) {
  if (record.type !== AGENT_SWITCHED_TYPE) return undefined;
  const branch = record.branch;
  const base = record.base;
  const legacyUndoLine = record.legacyUndoLine;
  if (typeof branch !== "string") return undefined;
  if (base === null || typeof base !== "object" || Array.isArray(base)) return undefined;
  const baseBranch = base.branch;
  const baseLine = base.line;
  if (typeof baseBranch !== "string" || typeof baseLine !== "number") return undefined;
  return {
    line,
    branch,
    base: { branch: baseBranch, line: baseLine },
    reason: typeof record.reason === "string" ? record.reason : undefined,
    legacyUndoLine: typeof legacyUndoLine === "number" ? legacyUndoLine : undefined,
  };
}

export function branchForLine(tree, line) {
  let owner = MAIN_BRANCH;
  for (const segment of tree.segments) {
    if (segment.fromLine > line) break;
    owner = segment.branch;
  }
  return owner;
}

export function parseTree(entries, lastLine) {
  const edges = [];
  const pairedLegacyUndoLines = new Set();
  const malformedSwitchLines = [];
  for (const { record, line } of entries) {
    if (record.type !== AGENT_SWITCHED_TYPE) continue;
    const edge = readSwitchEdge(record, line);
    if (edge === undefined) {
      malformedSwitchLines.push(line);
      continue;
    }
    edges.push(edge);
    if (edge.legacyUndoLine !== undefined) pairedLegacyUndoLines.add(edge.legacyUndoLine);
  }
  const segments = [];
  const duplicateBranches = [];
  const seenBranches = new Set([MAIN_BRANCH]);
  let branch = MAIN_BRANCH;
  let fromLine = 1;
  let opening;
  for (const edge of edges) {
    segments.push({ branch, edge: opening, fromLine, toLine: edge.line });
    if (seenBranches.has(edge.branch)) {
      if (!duplicateBranches.includes(edge.branch)) duplicateBranches.push(edge.branch);
    }
    seenBranches.add(edge.branch);
    branch = edge.branch;
    opening = edge;
    fromLine = edge.line + 1;
  }
  segments.push({ branch, edge: opening, fromLine, toLine: Math.max(lastLine, fromLine - 1) });
  return {
    edges,
    segments,
    pairedLegacyUndoLines,
    activeBranch: branch,
    diagnostics: { malformedSwitchLines, duplicateBranches },
  };
}

export function activeChain(entries, tree) {
  const segments = new Map(tree.segments.map((segment) => [segment.branch, segment]));
  const out = [];
  const visited = new Set();
  const walk = (branch, upto) => {
    if (visited.has(branch)) {
      throw new Error(`agent.switched base chain cycles at branch '${branch}'`);
    }
    visited.add(branch);
    const segment = segments.get(branch);
    if (segment === undefined) {
      throw new Error(`agent.switched base chain references unknown branch '${branch}'`);
    }
    if (segment.edge !== undefined) walk(segment.edge.base.branch, segment.edge.base.line);
    const floor = segment.edge === undefined ? 1 : segment.edge.line + 1;
    const ceil = upto ?? segment.toLine;
    for (const entry of entries) {
      if (entry.record.type === AGENT_SWITCHED_TYPE) continue;
      if (entry.line < floor || entry.line > ceil) continue;
      out.push(entry);
    }
  };
  walk(tree.activeBranch, undefined);
  return out;
}

/**
 * Read a wire file into the active chain, or a diagnostic status when the
 * tree is corrupt. A capture hook must never mis-capture, so every failure
 * mode here is a status the caller can skip on — never a throw.
 */
export function readActiveChain(raw) {
  const entries = [];
  const lines = raw.split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue; // torn tail line; the journal's own repair handles the file
    }
    if (record && typeof record.type === "string") entries.push({ record, line: i + 1 });
  }
  const lastLine = entries.length ? entries[entries.length - 1].line : 0;
  const tree = parseTree(entries, lastLine);
  try {
    return { status: "ok", tree, entries: activeChain(entries, tree) };
  } catch (error) {
    return { status: "tree_error", tree, error: error?.message || String(error) };
  }
}
