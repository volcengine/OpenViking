import type { EngineInterface, Register, ResolveInput } from "claude-code";
import type { Lookup, Turn } from "../types";
import {
  ICON,
  classifyCall,
  consulted,
  groupOf,
  parseRecall,
  summaryLine,
  titleOf,
  urisIn,
} from "./sources";

const turnsRef = { plugin: "ov-usage", key: "turns" } as const;
const repliesRef = { plugin: "ov-usage", key: "replies" } as const;
const expandedRef = { plugin: "ov-usage", key: "expanded" } as const;

const MAX_TURNS = 50;
const MAX_SESSIONS = 20;
const ACCENT = "cyan";

// Read, change, write back against the version read: tool calls run in parallel.
async function editTurns($: EngineInterface, fn: (turns: Turn[]) => Turn[]) {
  for (let i = 0; i < 10; i++) {
    const held = await $.state.get(turnsRef);
    const { isSet } = await $.state.set(turnsRef, fn(held.value ?? []), {
      ifVersion: held.version,
    });
    if (isSet) return;
  }
}

async function lastTurn($: EngineInterface): Promise<Turn | undefined> {
  const { value = [] } = await $.state.get(turnsRef);
  return value.at(-1);
}

// $.state is lost when the process restarts; $.store keeps each session's cards
// so `claude --continue` draws them again.
async function save($: EngineInterface) {
  const id = await $.session.id();
  if (!id) return;
  await $.store.set(`session:${id}`, {
    turns: (await $.state.get(turnsRef)).value ?? [],
    replies: (await $.state.get(repliesRef)).value ?? [],
    expanded: (await $.state.get(expandedRef)).value ?? [],
  });
  const index = ((await $.store.get("sessions")) as string[] | undefined) ?? [];
  const next = [...index.filter((k) => k !== id), id];
  for (const old of next.slice(0, -MAX_SESSIONS)) await $.store.delete(`session:${old}`);
  await $.store.set("sessions", next.slice(-MAX_SESSIONS));
}

async function restore($: EngineInterface) {
  if ((await $.state.get(turnsRef)).value?.length) return; // a hot reload keeps $.state
  const id = await $.session.id();
  const snap = id
    ? ((await $.store.get(`session:${id}`)) as Record<string, never> | undefined)
    : undefined;
  if (!snap) return;
  await $.state.set(turnsRef, snap.turns ?? []);
  await $.state.set(repliesRef, snap.replies ?? []);
  await $.state.set(expandedRef, snap.expanded ?? []);
}

async function setExpanded($: EngineInterface, n: number | "all", isOpen: boolean) {
  const { value: turns = [] } = await $.state.get(turnsRef);
  const { value: list = [] } = await $.state.get(expandedRef);
  const which = n === "all" ? turns.map((t) => t.n) : [n];
  await $.state.set(
    expandedRef,
    isOpen ? [...new Set([...list, ...which])] : list.filter((x) => !which.includes(x)),
  );
  await save($);
}

// The card under an answer: one line of totals, or that line plus every source
// and Claude's own lookups. Nothing when the answer drew on nothing.
async function card($: EngineInterface, e: ResolveInput, turn: Turn) {
  const { Box, Text, Button } = $.ui.resolve(e);
  const c = consulted(turn);
  const lookups = turn.lookups;
  if (c.rows.length === 0 && lookups.length === 0) return null;
  const { value: expanded = [] } = await $.state.get(expandedRef);
  const isOpen = expanded.includes(turn.n);
  return (
    <Box flexDirection="column" marginLeft={2} marginTop={1}>
      <Box flexWrap="wrap" columnGap={2}>
        <Text color={ACCENT} wrap="wrap">
          {summaryLine(c)}
        </Text>
        <Button
          key={`ov-toggle-${turn.n}`}
          label={isOpen ? "[Collapse]" : "[Expand]"}
          plain
          onPress={() => setExpanded($, turn.n, !isOpen)}
        />
      </Box>
      {isOpen &&
        c.rows.map((r) => (
          <Text key={`ov-src-${turn.n}-${r.uri}`} wrap="wrap">
            {"  "}
            {ICON[groupOf(r.uri)]} {titleOf(r.uri)}
            <Text dimColor>
              {r.from === "recall"
                ? ` · auto-recalled${r.score > 0 ? ` ${r.score.toFixed(2)}` : ""}`
                : " · found by Claude"}
              {r.isOpened ? " · read in full" : ""}
            </Text>
          </Text>
        ))}
      {isOpen && lookups.length > 0 && <Text bold>Claude's own lookups</Text>}
      {isOpen &&
        lookups.map((l) => (
          <Text key={`ov-lookup-${l.id}`} wrap="wrap">
            {"  "}
            {l.query !== null ? `⌕ Searched “${l.query}” · ${l.found.length} results` : ""}
            {l.query !== null && l.opened.length ? " · " : ""}
            {l.opened.length ? `▤ Read ${l.opened.map(titleOf).join(", ")}` : ""}
            {l.isError && <Text color="red"> · failed</Text>}
          </Text>
        ))}
    </Box>
  );
}

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    await restore($);
    await $.command.register({
      name: "openviking-usage",
      description: "Expand or collapse the OpenViking cards under answers",
      argumentHint: "expand | collapse",
    });
    return next(e);
  });

  on("command.run", { command: "openviking-usage" }, async ($, e) => {
    const verb = e.args.trim().toLowerCase();
    if (verb === "expand" || verb === "collapse") {
      await setExpanded($, "all", verb === "expand");
      return {};
    }
    return { text: "Usage: /openviking-usage expand | collapse" };
  });

  // Each prompt starts an answer; openviking-memory's recall block says what it injected.
  on("classic.UserPromptSubmit", async ($, e, next) => {
    const res = await next(e);
    const block = (res.additionalContext ?? []).find(
      (c) =>
        c.includes("<openviking-context") &&
        !/source="(startup|resume|compact|skill-experience)"/.test(c),
    );
    const prev = await lastTurn($);
    const turn: Turn = {
      n: (prev?.n ?? 0) + 1,
      recalled: block ? parseRecall(block) : [],
      lookups: [],
    };
    await editTurns($, (list) => [...list, turn].slice(-MAX_TURNS));
    await save($);
    return res;
  });

  // Claude's own OpenViking reads and searches, through MCP or the `ov` CLI.
  on("tool.call", async ($, e, next) => {
    const call = classifyCall(e.tool, { ...e } as Record<string, unknown>);
    const turn = call ? await lastTurn($) : undefined;
    if (!call || !turn) return next(e);
    const ran = await next(e);
    const text = "text" in ran && typeof ran.text === "string" ? ran.text : "";
    const lookup: Lookup = {
      id: e.tool_use_id,
      query: call.query,
      opened: call.opened,
      found: call.query !== null ? urisIn(text).filter((u) => !call.opened.includes(u)) : [],
      isError: ran.deny !== undefined || ran.isError === true,
    };
    await editTurns($, (list) =>
      list.map((t) => (t.n === turn.n ? { ...t, lookups: [...t.lookups, lookup] } : t)),
    );
    return ran;
  });

  // The last text row of an answer carries its card.
  on("session.append", async ($, e, next) => {
    const res = await next(e);
    if (res.deny !== undefined || e.agentId || e.door !== "response") return res;
    const hasText = (res.message.content ?? []).some(
      (b) => b && typeof b === "object" && "type" in b && b.type === "text",
    );
    const turn = hasText ? await lastTurn($) : undefined;
    if (turn) {
      const { value: list = [] } = await $.state.get(repliesRef);
      const kept = [...list.filter((r) => r.n !== turn.n), { id: res.uuid, n: turn.n }];
      await $.state.set(repliesRef, kept.slice(-MAX_TURNS));
    }
    return res;
  });

  on("turn.complete", async ($, e, next) => {
    const done = await next(e);
    await save($);
    return done;
  });

  on("ui.render", { component: "AssistantMessage" }, async ($, e, next) => {
    const { value: replies = [] } = await $.state.get(repliesRef);
    const n = replies.find((r) => r.id === e.requestId)?.n;
    const { value: turns = [] } = await $.state.get(turnsRef);
    const turn = n === undefined ? undefined : turns.find((t) => t.n === n);
    const below = turn ? await card($, e, turn) : null;
    if (!below) return next(e);
    const { Box } = $.ui.resolve(e);
    return (
      <Box flexDirection="column">
        {await next(e)}
        {below}
      </Box>
    );
  });
};
