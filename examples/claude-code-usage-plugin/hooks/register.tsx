import type { EngineInterface, Register, ResolveInput } from "claude-code";

import type {
  Group,
  Inject,
  Lifetime,
  Lookup,
  Opening,
  RecallItem,
  Reply,
  Seen,
  OvSettings,
  Turn,
  TurnLookup,
} from "../types";

const PANE = "ov-usage";
const VERSION = "0.1.0";
const TITLE = "OV-Usage";
const ACCENT = "cyan";

const injectRef = { plugin: "ov-usage", key: "inject" } as const;
const lookupsRef = { plugin: "ov-usage", key: "lookups" } as const;
const turnRef = { plugin: "ov-usage", key: "turn" } as const;
const turnCountRef = { plugin: "ov-usage", key: "turnCount" } as const;
const seenRef = { plugin: "ov-usage", key: "seen" } as const;
const mutedRef = { plugin: "ov-usage", key: "muted" } as const;
const lifetimeRef = { plugin: "ov-usage", key: "lifetime" } as const;
const showWeakRef = { plugin: "ov-usage", key: "showWeak" } as const;
const showDetailsRef = { plugin: "ov-usage", key: "showDetails" } as const;
const settingsRef = { plugin: "ov-usage", key: "settings" } as const;
const showSettingsRef = { plugin: "ov-usage", key: "showSettings" } as const;
const langPrefRef = { plugin: "ov-usage", key: "langPref" } as const;
const sysLangRef = { plugin: "ov-usage", key: "sysLang" } as const;
const historyRef = { plugin: "ov-usage", key: "history" } as const;
const viewTurnRef = { plugin: "ov-usage", key: "viewTurn" } as const;
const showHistoryRef = { plugin: "ov-usage", key: "showHistory" } as const;
const cardDetailRef = { plugin: "ov-usage", key: "cardDetail" } as const;
const showAllCardRef = { plugin: "ov-usage", key: "showAllCard" } as const;
const layoutRef = { plugin: "ov-usage", key: "layout" } as const;
const repliesRef = { plugin: "ov-usage", key: "replies" } as const;
const openingRef = { plugin: "ov-usage", key: "opening" } as const;
const MAX_HISTORY = 50;

// A viking:// URI starts with an ASCII scope (user, resources, agent, ~) and
// stops at whitespace or punctuation, CJK included. Prose that merely mentions
// "viking://格式的来源URI" in a summary is not a URI.
const URI = /viking:\/\/(?:~|[A-Za-z][\w.-]*)(?:\/[^\s"'<>)\]`,，。；：！？、（）《》“”]*)?/g;
// A whole URI as stored: file names may contain spaces, never line breaks or CJK punctuation.
const IS_URI = /^viking:\/\/(?:~|[A-Za-z][\w.-]*)(?:\/[^\n"'<>)\]`,，。；：！？、（）《》“”]*)?$/;
// An `ov` or `openviking` CLI invocation at the start of a command or after ; && | (
const OV_CLI = /(?:^|[;&|(]\s*|\s&&\s*)(?:\S+=\S+\s+)*(?:ov|openviking)\s+(?!-)\S/;
// A line that is only a URI, after an optional search-result prefix or inside
// "=== … ===": file names there can contain spaces.
const URI_LINE =
  /^\s*(?:-\s*\[[\w-]+\s+\d{1,3}%\]\s+|===\s+)?(viking:\/\/(?:~|[A-Za-z][\w.-]*)\/[^"'<>`]*?)\s*(?:===)?\s*$/;

// The viking:// URIs a tool's output names: whole-line URIs first (spaces
// allowed), then any others inline.
function urisIn(text: string): string[] {
  const found: string[] = [];
  for (const line of text.split("\n")) {
    const m = URI_LINE.exec(line);
    if (m?.[1]) found.push(m[1]);
  }
  for (const u of text.match(URI) ?? []) {
    if (!found.some((f) => f === u || f.startsWith(`${u} `))) found.push(u);
  }
  return [...new Set(found)];
}

const OV_TOOL = /^mcp__.*openviking.*__(\w+)$/;
const WRITE_TOOLS = new Set(["remember", "write", "edit", "add_resource", "add_skill", "forget"]);
// Tools that open the files they name; the rest return a list of matches.
const OPEN_TOOLS = new Set(["read", "ov cli"]);
// Status checks name no memory, so they are not something an answer referred to.
const QUIET_TOOLS = new Set(["health"]);
const LAYER: Record<string, RecallItem["layer"]> = {
  abstract: "L0",
  overview: "L1",
  full: "L2",
  uri: "URI",
};
const LAYER_COLOR: Record<RecallItem["layer"], string> = {
  L0: "gray",
  L1: "cyan",
  L2: "magenta",
  URI: "blue",
};
// ---------- language ----------

type Lang = "en" | "zh";

const STRINGS = {
  en: {
    allSessions: "All sessions",
    thisConversation: "This conversation",
    settings: "Settings",
    recallState: (on: boolean) => `Auto-recall ${on ? "on" : "off"}`,
    captureState: (on: boolean) => `auto-capture ${on ? "on" : "off"}`,
    on: "on",
    off: "off",
    promptsWithMemory: (n: number) =>
      `Memories auto-added to ${n} ${n === 1 ? "prompt" : "prompts"}`,
    updatesFrom: (c: number, n: number) =>
      `${c} memory ${c === 1 ? "update" : "updates"} saved from ${n} ${n === 1 ? "conversation" : "conversations"}`,
    startupLine: (n: number, role?: string) =>
      `Startup: ${n} ${n === 1 ? "memory" : "memories"} indexed${role ? ` · your profile: ${role}` : ""}`,
    thisAnswer: "This answer",
    usedSection: (n: number) => `✓ ${n} OpenViking ${n === 1 ? "source" : "sources"} consulted`,
    alreadyInContext: (n: number) => `${n} already in context from earlier prompts`,
    lowerMatches: (n: number) => `▤ ${n} less relevant ${n === 1 ? "match" : "matches"}`,
    moreAnswers: (n: number) => `[+${n} more]`,
    fewer: "[Fewer]",
    latestBtn: "[Latest]",
    earlierTitle: "Earlier answers",
    answerN: (n: number, time: string) => `Answer #${n} · ${time}`,
    thisSession: "This session",
    recalledAcross: (n: number, p: number) =>
      `${n} ${n === 1 ? "memory" : "memories"} recalled across ${p} ${p === 1 ? "prompt" : "prompts"}`,
    aboutYouLine: (n: number) => `${n} of them about you (preferences, past events, notes)`,
    usedLine: (n: number) =>
      `${n} OpenViking ${n === 1 ? "source" : "sources"} consulted this session`,
    openedLine: (n: number) => `${n} read in full by Claude`,
    savedLine: (n: number) => `${n} saved to OpenViking`,
    openedFull: (n: number) => `${n} read in full`,
    catPrefs: (n: number) => `${n} ${n === 1 ? "preference" : "preferences"}`,
    catHistory: (n: number) => `${n} past ${n === 1 ? "event" : "events"}`,
    catWork: (n: number) => `${n} work ${n === 1 ? "memory" : "memories"}`,
    catDocs: (n: number) => `${n} team ${n === 1 ? "doc" : "docs"}`,
    catSkills: (n: number) => `${n} ${n === 1 ? "skill" : "skills"}`,
    waiting: "Send a prompt to see which OpenViking sources it draws on.",
    nothingRecalled: "Nothing was recalled for this prompt.",
    emptyIgnored: (n: number) => `${n} empty ${n === 1 ? "document" : "documents"} skipped`,
    show: "[Show]",
    hide: "[Hide]",
    muteThese: "[Mute all]",
    mute: "[Mute]",
    muted: (n: number, hits: number) =>
      `${n} muted for this session${hits ? ` · ${hits} held back from this answer` : ""}`,
    unmuteAll: "[Unmute all]",
    lookedUp: "Claude's own lookups",
    lookupSearch: "Searched",
    lookupRead: "Read",
    lookupWrite: "Saved",
    results: (n: number) => `${n} ${n === 1 ? "result" : "results"}`,
    failed: "failed",
    viaLookup: "found by Claude",
    viaRecall: "auto-recalled",
    nothingUsed: "No OpenViking sources for this answer.",
    statusThis: "this answer",
    statusNone: "no sources for this answer",
    statusSession: (n: number) => `${n} this session`,
    statusSaved: (n: number) => `${n} saved`,
    statusReady: (n: number) => `startup: ${n} ${n === 1 ? "memory" : "memories"} indexed`,
    rowCardDetail: "Card detail",
    detailLow: "Low",
    detailHigh: "High",
    rowLayout: "Show in",
    layoutPane: "Sidebar",
    layoutInline: "In conversation",
    cardAnswer: "OpenViking · this answer",
    cardOverview: "OpenViking",
    opened: "✓ opened",
    details: "[Details]",
    hideDetails: "[Hide details]",
    rowRecall: "Auto-recall",
    rowCapture: "Auto-capture",
    rowStartup: "Startup context",
    rowServer: "Server",
    rowLanguage: "Language",
    rowTools: "Memory tools",
    recallDetail: (th: number, n: number) => ` · threshold ${th} · up to ${n} items`,
    captureOn: (n: number) => ` · saves to memory every ${n} turns`,
    captureOff: " · new conversations are not saved to memory",
    startupDetail: (n: string) => ` · profile ≤${n} tokens`,
    notSet: "not set",
    apiKeySet: " · API key set",
    noApiKey: " · no API key",
    system: "Follow system",
    changeIn: "Edit in ~/.openviking/ovcli.conf",
    configError: (e: string) => `Couldn't read OpenViking's config: ${e}`,
    mutedToast: "Muted for this session. Nothing in OpenViking was changed.",
    mutedManyToast: (n: number) =>
      `Muted ${n} for this session. Nothing in OpenViking was changed.`,
    injectedToast: (src: string) => `OpenViking: loaded ${src} context`,
    kinds: {
      preference: "Your preference",
      history: "Past event",
      lesson: "Lesson",
      notes: "Work note",
      agents: "From your agents",
      skill: "Skill",
      docs: "Team doc",
    },
    reasons: {
      disabled: "Auto-recall is off (autoRecall: false in ovcli.conf).",
      no_results: "No memory scored above the relevance threshold for this prompt.",
      short_query: "Prompt too short to trigger recall.",
      query_filtered: "A recall filter skipped this prompt.",
      offline: "Can't reach the OpenViking server.",
    } as Record<string, string>,
  },
  zh: {
    allSessions: "全局",
    thisConversation: "本次对话",
    settings: "设置",
    recallState: (on: boolean) => `召回${on ? "开" : "关"}`,
    captureState: (on: boolean) => `记录${on ? "开" : "关"}`,
    on: "开",
    off: "关",
    promptsWithMemory: (n: number) => `${n} 次提问自动带上了记忆`,
    updatesFrom: (c: number, n: number) => `${n} 段对话沉淀了 ${c} 次记忆更新`,
    startupLine: (n: number, role?: string) =>
      `启动加载：${n} 条记忆索引${role ? ` · 你的画像：${role}` : ""}`,
    thisAnswer: "本次回答",
    usedSection: (n: number) => `✓ 参考了 ${n} 个 OpenViking 来源`,
    alreadyInContext: (n: number) => `${n} 条之前已在上下文中`,
    lowerMatches: (n: number) => `▤ ${n} 条低相关`,
    moreAnswers: (n: number) => `[还有 ${n} 条]`,
    fewer: "[收起]",
    latestBtn: "[最新]",
    earlierTitle: "之前的回答",
    answerN: (n: number, time: string) => `第 ${n} 次回答 · ${time}`,
    thisSession: "本会话",
    recalledAcross: (n: number, p: number) => `${p} 次提问共召回 ${n} 条记忆`,
    aboutYouLine: (n: number) => `其中 ${n} 条关于你：偏好、经历和笔记`,
    usedLine: (n: number) => `本会话共参考 ${n} 个 OpenViking 来源`,
    openedLine: (n: number) => `Claude 完整打开了 ${n} 条`,
    savedLine: (n: number) => `写回 OpenViking ${n} 条`,
    openedFull: (n: number) => `${n} 条完整打开`,
    catPrefs: (n: number) => `${n} 你的偏好`,
    catHistory: (n: number) => `${n} 你的经历`,
    catWork: (n: number) => `${n} 你的工作记忆`,
    catDocs: (n: number) => `${n} 团队文档`,
    catSkills: (n: number) => `${n} 技能`,
    waiting: "发送一条消息后，这里会显示 OpenViking 的召回。",
    nothingRecalled: "这条提问没有召回记忆。",
    emptyIgnored: (n: number) => `已忽略 ${n} 条空文档`,
    show: "[展开]",
    hide: "[收起]",
    muteThese: "[全部屏蔽]",
    mute: "[屏蔽]",
    muted: (n: number, hits: number) =>
      `本会话已屏蔽 ${n} 条${hits ? ` · 本次回答拦下 ${hits} 条` : ""}`,
    unmuteAll: "[全部取消屏蔽]",
    lookedUp: "Claude 主动查找",
    lookupSearch: "搜索",
    lookupRead: "读取",
    lookupWrite: "写入",
    results: (n: number) => `${n} 条结果`,
    failed: "失败",
    viaLookup: "主动查找",
    viaRecall: "自动召回",
    nothingUsed: "本次回答没有参考 OpenViking 的内容。",
    statusThis: "本次",
    statusNone: "本次未参考",
    statusSession: (n: number) => `本会话 ${n}`,
    statusSaved: (n: number) => `写回 ${n}`,
    statusReady: (n: number) => `启动加载 ${n} 条记忆索引`,
    rowCardDetail: "卡片详情",
    detailLow: "简略",
    detailHigh: "详细",
    rowLayout: "显示位置",
    layoutPane: "侧边栏",
    layoutInline: "对话中",
    cardAnswer: "OpenViking · 本次回答",
    cardOverview: "OpenViking",
    opened: "✓ 已打开",
    details: "[详情]",
    hideDetails: "[收起详情]",
    rowRecall: "自动召回",
    rowCapture: "自动记录",
    rowStartup: "启动上下文",
    rowServer: "服务",
    rowLanguage: "语言",
    rowTools: "记忆工具",
    recallDetail: (th: number, n: number) => ` · 阈值 ${th} · 最多 ${n} 条`,
    captureOn: (n: number) => ` · 每 ${n} 轮写入`,
    captureOff: " · 新对话不会写入记忆",
    startupDetail: (n: string) => ` · 画像 ≤${n} token`,
    notSet: "未设置",
    apiKeySet: " · 已设置 API Key",
    noApiKey: " · 未设置 API Key",
    system: "跟随系统",
    changeIn: "在 ~/.openviking/ovcli.conf 中修改",
    configError: (e: string) => `无法读取 OpenViking 配置：${e}`,
    mutedToast: "已在本会话屏蔽，OpenViking 数据不受影响",
    mutedManyToast: (n: number) => `已在本会话屏蔽 ${n} 条，OpenViking 数据不受影响`,
    injectedToast: (src: string) => `OpenViking：已注入 ${src} 上下文`,
    kinds: {
      preference: "你的偏好",
      history: "你的经历",
      lesson: "经验教训",
      notes: "你的笔记",
      agents: "来自你的 Agent",
      skill: "技能",
      docs: "文档",
    },
    reasons: {
      disabled: "自动召回已关闭（ovcli.conf 中 autoRecall: false）。",
      no_results: "这条提问没有超过相关度阈值的记忆。",
      short_query: "提问太短，没有触发召回。",
      query_filtered: "提问被召回过滤规则拦下。",
      offline: "连不上 OpenViking 服务。",
    } as Record<string, string>,
  },
};

type Strings = (typeof STRINGS)["en"];

// ---------- pure helpers ----------

// Rough token estimate: CJK ≈ 1 token per char, everything else ≈ 4 chars per token.
function estTokens(text: string) {
  const cjk = (text.match(/[　-鿿＀-￯]/g) ?? []).length;
  return Math.max(1, Math.round(cjk + (text.length - cjk) / 4));
}

// The startup <openviking-context>: profile + memory tree.
function parseStartup(text: string, at: string): Inject | null {
  const start = text.indexOf("<openviking-context");
  if (start < 0) return null;
  const body = text.slice(start);
  const source = /source="([^"]+)"/.exec(body)?.[1] ?? "startup";
  const prof = /<user-profile[^>]*>([\s\S]*?)<\/user-profile>/.exec(body)?.[1] ?? "";
  const profile = prof
    .split("\n")
    .map((l) =>
      l
        .replace(/^[-#\s]+/, "")
        .replace(/（as of [^）]+）/, "")
        .trim(),
    )
    .filter(Boolean);
  const groups: Group[] = [];
  for (const line of body.split("\n")) {
    const dir = /^\s*(viking:\/\/\S+\/)\s*$/.exec(line)?.[1];
    if (dir) groups.push({ dir, files: [] });
    const file = /^\s+-\s+(.+)$/.exec(line)?.[1];
    if (file) groups.at(-1)?.files.push(file.trim());
  }
  if (!profile.length && !groups.length) return null;
  return { at, source, profile, groups };
}

// A per-prompt recall: <memory uri score detail> items, or a server-assembled digest.
function parseRecall(text: string): Omit<RecallItem, "firstTurn">[] {
  const items: Omit<RecallItem, "firstTurn">[] = [];
  for (const m of text.matchAll(/<memory\s([^>]*?)(?:\/>|>([\s\S]*?)<\/memory>)/g)) {
    const attrs = m[1] ?? "";
    const body = (m[2] ?? "")
      .split("\n")
      .filter((l) => !/^\s*#{1,6}\s/.test(l))
      .join("\n")
      .trim();
    const uri = /uri="([^"]+)"/.exec(attrs)?.[1];
    if (!uri) continue;
    const detail = /detail="(\w+)"/.exec(attrs)?.[1] ?? "abstract";
    items.push({
      uri,
      layer: LAYER[detail] ?? "L0",
      score: Number(/score="([\d.]+)"/.exec(attrs)?.[1] ?? 0),
      tokens: estTokens(m[0]),
      summary: body.replace(/\s+/g, " ").slice(0, 160),
    });
  }
  if (items.length) return items;
  // Digest form: "- summary 来源：viking://..."
  for (const line of text.split("\n")) {
    const uri = line.match(URI)?.[0];
    if (!uri || !/^\s*-/.test(line)) continue;
    const summary = line.replace(/^\s*-\s*/, "").replace(/\s*(来源|source)[:：]\s*\S+/i, "");
    const same = items.find((it) => it.uri === uri);
    if (same) {
      same.tokens += estTokens(line);
      same.summary = `${same.summary} / ${summary}`.slice(0, 160);
      continue;
    }
    items.push({
      uri,
      layer: "L0",
      score: 0,
      tokens: estTokens(line),
      summary: summary.slice(0, 160),
    });
  }
  return items;
}

// Secrets never reach storage or the pane: JWT-like tokens, Bearer headers,
// sk- keys, and key=value / --api-key style arguments.
function redact(text: string): string {
  return text
    .replace(/[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{40,}/g, "[REDACTED]")
    .replace(/Bearer\s+[A-Za-z0-9._~+/-]{16,}/gi, "Bearer [REDACTED]")
    .replace(/sk-[A-Za-z0-9_-]{20,}/g, "[REDACTED]")
    .replace(
      /((?:api[_-]?key|token|secret|password|passwd|authorization)["']?\s*[:=]\s*["']?|--(?:api-key|token|password)[=\s]+)[^\s"',;&|]+/gi,
      "$1[REDACTED]",
    );
}

function stripMuted(contexts: readonly string[] | undefined, muted: string[]) {
  let hits = 0;
  if (!contexts || !muted.length) return { contexts, hits };
  const out = contexts.map((c) =>
    c
      .replace(
        /<memory\s[^>]*?uri="([^"]+)"[^>]*?(?:\/>|>[\s\S]*?<\/memory>)\n?/g,
        (whole, uri: string) => {
          if (!muted.includes(uri)) return whole;
          hits += 1;
          return "";
        },
      )
      // Digest form: one "- summary 来源：viking://..." line per item.
      .split("\n")
      .filter((line) => {
        const uri = /^\s*-/.test(line) ? line.match(URI)?.[0] : undefined;
        if (!uri || !muted.includes(uri)) return true;
        hits += 1;
        return false;
      })
      .join("\n"),
  );
  return { contexts: out, hits };
}

function isExpanded(uri: string, lookups: Lookup[]) {
  return lookups.some((l) => l.kind === "read" && l.target.includes(uri));
}

// ---------- presentation ----------

type Kind = { icon: string; label: keyof Strings["kinds"]; color: string; personal: boolean };

// What a memory is to the person, from where it lives.
function kindOf(uri: string): Kind {
  if (/^viking:\/\/user\/[^/]+\/memories\/(preferences|profile|identity|soul)/.test(uri))
    return { icon: "★", label: "preference", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/memories\/events\//.test(uri))
    return { icon: "◷", label: "history", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/memories\/experiences\//.test(uri))
    return { icon: "◆", label: "lesson", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/memories\//.test(uri))
    return { icon: "◆", label: "notes", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/peers\//.test(uri))
    return { icon: "⇄", label: "agents", color: "blue", personal: true };
  if (/\/skills?\//.test(uri)) return { icon: "⚙", label: "skill", color: "blue", personal: false };
  return { icon: "▤", label: "docs", color: "gray", personal: false };
}

// Which group a source counts toward in the answer heading: your preferences,
// your history (dated events), your work memory (notes, lessons, your agents'
// memories), team docs, or skills.
type Category = "prefs" | "history" | "work" | "docs" | "skill";
const CATEGORY: Record<Kind["label"], Category> = {
  preference: "prefs",
  history: "history",
  lesson: "work",
  notes: "work",
  agents: "work",
  skill: "skill",
  docs: "docs",
};
function categoryOf(uri: string): Category {
  return CATEGORY[kindOf(uri).label];
}

const EMPTY =
  /empty (document|markdown)|no (actual|clear|meaningful) content|no clear primary purpose|无实际|空白|无任何可识别|乱码|无任何有效|没有实际内容/i;

function isEmptyDoc(it: RecallItem) {
  return EMPTY.test(it.summary);
}

// Listed as relevant only above a score bar, your own memories included, so
// the pane never claims more than recall delivered. Digest items carry no
// score; for those your own memories count.
const RELEVANT_SCORE = 0.55;
// Your own memories (preferences, history, notes) score lower for the same
// usefulness, so they get a lower bar.
const RELEVANT_SCORE_PERSONAL = 0.5;

function isStrong(it: RecallItem) {
  if (EMPTY.test(it.summary)) return false;
  const personal = kindOf(it.uri).personal;
  if (it.score === 0) return personal;
  return it.score >= (personal ? RELEVANT_SCORE_PERSONAL : RELEVANT_SCORE);
}

function titleOf(uri: string): string | null {
  const name = decodeURIComponent(uri.split("/").filter(Boolean).at(-1) ?? "").replace(/\.md$/, "");
  if (
    !name ||
    name.startsWith(".") ||
    /^(prompts|summary|index|n_\d+)$|\.aiff|^[0-9a-f-]{12,}$|conversation_/i.test(name)
  )
    return null;
  return name.replace(/[_-]+/g, " ");
}

function dateOf(uri: string): string {
  const m = /\/(\d{4})\/(\d{2})\/(\d{2})\//.exec(uri);
  return m ? `${Number(m[2])}/${Number(m[3])} ` : "";
}

function gist(summary: string): string {
  const s = summary
    .replace(/^#\s*Summary\s*/i, "")
    .replace(/^(#{1,6}\s*[^#]*?\s+)+(?=[-*\d]|$)/, "")
    .replace(/^#+\s*/, "")
    .replace(/^\s*(\d+[.、)]|[-*])\s*/, "")
    .replace(/\s#+\s[\s\S]*$/, "")
    .replace(
      /^(本文档|该文档|这是|This (document|file) (is|records))(的)?(核心|主要)?(目的)?(是|为)?[：:,，]?\s*/i,
      "",
    )
    .trim();
  return (s.split(/(?<=[。！？.!?])\s*/)[0] ?? s).slice(0, 90);
}

function shortName(uri: string): string {
  return titleOf(uri) ?? decodeURIComponent(uri.split("/").slice(-2).join("/"));
}

// One of Claude's own lookups as a row reads: what it did, and to what.
function describeLookup(t: Strings, l: TurnLookup) {
  const isOpen = OPEN_TOOLS.has(l.tool);
  const isWrite = l.kind === "write";
  return {
    icon: isWrite ? "✎" : isOpen ? "▤" : "⌕",
    verb: isWrite ? t.lookupWrite : isOpen ? t.lookupRead : t.lookupSearch,
    what: isOpen || isWrite ? l.uris.map(shortName).join(", ") || l.query : `“${l.query}”`,
    count: !isOpen && !isWrite && !l.isError ? t.results(l.uris.length) : null,
  };
}

function headline(it: RecallItem): string {
  const title = titleOf(it.uri);
  const g = gist(it.summary);
  const name = title ?? (g || decodeURIComponent(it.uri.split("/").slice(-2).join("/")));
  return `${dateOf(it.uri)}${name}${title && g && !g.includes(title) ? ` — ${g}` : ""}`;
}

// ---------- state ----------

async function getInject($: EngineInterface): Promise<Inject | null> {
  const { value = null } = await $.state.get(injectRef);
  return value;
}

async function getLookups($: EngineInterface): Promise<Lookup[]> {
  const { value = [] } = await $.state.get(lookupsRef);
  return value;
}

async function getTurn($: EngineInterface): Promise<Turn | null> {
  const { value = null } = await $.state.get(turnRef);
  return value;
}

async function getSeen($: EngineInterface): Promise<Seen[]> {
  const { value = [] } = await $.state.get(seenRef);
  return value;
}

async function getMuted($: EngineInterface): Promise<string[]> {
  const { value = [] } = await $.state.get(mutedRef);
  return value;
}

async function getLifetime($: EngineInterface): Promise<Lifetime | null> {
  const { value = null } = await $.state.get(lifetimeRef);
  return value;
}

// Read, apply, write with ifVersion; retry on a miss so parallel tool calls all land.
async function editLookups($: EngineInterface, fn: (list: Lookup[]) => Lookup[]) {
  for (let i = 0; i < 10; i++) {
    const held = await $.state.get(lookupsRef);
    const { isSet } = await $.state.set(lookupsRef, fn(held.value ?? []), {
      ifVersion: held.version,
    });
    if (isSet) return;
  }
}

async function muteUri($: EngineInterface, uri: string) {
  const muted = await getMuted($);
  if (!muted.includes(uri)) await $.state.set(mutedRef, [...muted, uri]);
  await saveSession($);
  $.ui.toast(STRINGS[await getLang($)].mutedToast);
}

async function muteMany($: EngineInterface, uris: string[]) {
  const muted = await getMuted($);
  await $.state.set(mutedRef, [...new Set([...muted, ...uris])]);
  await saveSession($);
  $.ui.toast(STRINGS[await getLang($)].mutedManyToast(uris.length));
}

async function muteShownWeak($: EngineInterface) {
  const latest = await getTurn($);
  const { value: history = [] } = await $.state.get(historyRef);
  const { value: viewN = null } = await $.state.get(viewTurnRef);
  const shown = (viewN !== null ? history.find((h) => h.n === viewN) : undefined) ?? latest;
  const weak = (shown?.items ?? [])
    .filter((it) => !isEmptyDoc(it) && !isStrong(it))
    .map((it) => it.uri);
  if (weak.length) await muteMany($, weak);
}

// Every pane button is answered here by its key. A press can arrive for a
// drawing whose onPress closure is gone (a redraw, a restored pane after a
// restart); handling it by key means it always lands.
async function handlePress($: EngineInterface, key: string): Promise<boolean> {
  if (key === "toggle-settings") await toggle($, "settings");
  else if (key === "toggle-weak") await toggle($, "weak");
  else if (key === "toggle-details") await toggle($, "details");
  else if (key === "toggle-history") await toggle($, "history");
  else if (key === "back-latest") await viewTurn($, null);
  else if (key === "unmute-all") await unmuteAll($);
  else if (key === "mute-weak") await muteShownWeak($);
  else if (key === "lang-en") await setLang($, "en");
  else if (key === "lang-zh") await setLang($, "zh");
  else if (key === "lang-system") await setLang($, "system");
  else if (key === "layout-pane") await setLayout($, "pane");
  else if (key === "layout-inline") await setLayout($, "inline");
  else if (key === "detail-low") await setCardDetail($, "low");
  else if (key === "detail-high") await setCardDetail($, "high");
  else if (key === "toggle-card-more") await toggleCardMore($);
  else if (key.startsWith("mute-")) await muteUri($, key.slice("mute-".length));
  else if (key.startsWith("turn-")) {
    const n = Number(key.slice("turn-".length));
    const latest = await getTurn($);
    await viewTurn($, latest !== null && latest.n === n ? null : n);
  } else return false;
  return true;
}

async function viewTurn($: EngineInterface, n: number | null) {
  await $.state.set(viewTurnRef, n);
}

async function toggle($: EngineInterface, which: "weak" | "details" | "settings" | "history") {
  if (which === "history") {
    const { value = false } = await $.state.get(showHistoryRef);
    await $.state.set(showHistoryRef, !value);
  } else if (which === "settings") {
    const { value = false } = await $.state.get(showSettingsRef);
    await $.state.set(showSettingsRef, !value);
    await $.store.set("ui:showSettings", !value);
  } else if (which === "weak") {
    const { value = false } = await $.state.get(showWeakRef);
    await $.state.set(showWeakRef, !value);
  } else {
    const { value = false } = await $.state.get(showDetailsRef);
    await $.state.set(showDetailsRef, !value);
  }
}

// A profile line that states a role (职业 / Role / Job title …).
const ROLE_LINE =
  /^(?:职业|角色|岗位|职位|职务|role|job(?:\s+title)?|title|occupation|position)\s*[：:]\s*(.+)$/i;

// The numbers the "all sessions" and "this session" sections show, for the pane and the overview card alike.
async function sessionTotals($: EngineInterface) {
  const inj = await getInject($);
  const list = await getLookups($);
  const seen = await getSeen($);
  const { value: history = [] } = await $.state.get(historyRef);
  const { value: turnCount = 0 } = await $.state.get(turnCountRef);
  const { value: cfg = null } = await $.state.get(settingsRef);
  const recallHidden = cfg !== null && !cfg.error && !cfg.autoRecall;
  // Every URI recalled this session (seen), minus the ones the answer history
  // shows were empty documents. Seen covers turns from before history was
  // recorded; history is what tells empty ones apart.
  const emptyUris = new Set(history.flatMap((h) => h.items.filter(isEmptyDoc).map((it) => it.uri)));
  const recalledUris = [
    ...new Set([...seen.map((x) => x.uri), ...history.flatMap((h) => h.items.map((it) => it.uri))]),
  ].filter((u) => !emptyUris.has(u));
  return {
    sessionRecalled: recalledUris.length,
    sessionAboutYou: recalledUris.filter((u) => kindOf(u).personal).length,
    sessionPrompts: Math.max(turnCount, history.length),
    used: new Set(history.flatMap((h) => consultedUris(h, recallHidden))).size,
    opened: seen.filter((s) => isExpanded(s.uri, list)).length,
    saved: list.filter((l) => l.kind === "write" && l.isDone && !l.isError).length,
    files: inj ? inj.groups.reduce((n, g) => n + g.files.length, 0) : 0,
    // The profile line that states a role; no guess from other lines.
    role: inj?.profile
      .map((l) => ROLE_LINE.exec(l)?.[1]?.trim() ?? "")
      .find((l) => l.length > 1 && l.length <= 40),
    hasInject: inj !== null,
    life: await getLifetime($),
    cfg,
  };
}

// With auto-recall off in OpenViking's config, nothing about it is shown:
// no recalled files, no recall counts, no recall timing.
async function isRecallHidden($: EngineInterface): Promise<boolean> {
  const { value: cfg = null } = await $.state.get(settingsRef);
  return cfg !== null && !cfg.error && !cfg.autoRecall;
}

async function setCardDetail($: EngineInterface, detail: "low" | "high") {
  await $.state.set(cardDetailRef, detail);
  await $.store.set("cardDetail", detail);
}

async function toggleCardMore($: EngineInterface) {
  const { value = false } = await $.state.get(showAllCardRef);
  await $.state.set(showAllCardRef, !value);
}

async function setLayout($: EngineInterface, layout: "pane" | "inline") {
  await $.state.set(layoutRef, layout);
  await $.store.set("layout", layout);
}

// A fingerprint of row text, so cards can be matched to rows without keeping
// any of the prompt's or answer's words (FNV-1a, 32-bit).
function fingerprint(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${text.length}:${h.toString(16)}`;
}

// Endings fingerprinted per answer, longest first. The last text block of an
// answer can be short (tool calls split an answer into blocks), so its ending
// is matched at the longest of these lengths it can fill.
const TAILS = [400, 160, 80, 40, 20];
const HEAD = 200;

// The text block that ends an answer carries its card; a later block of the
// same answer takes the card over.
// With no id (the turn's end, which knows the text alone), an id already noted is kept.
async function noteReply($: EngineInterface, id: string, text: string) {
  const turn = await getTurn($);
  if (!turn) return;
  const end = text.trimEnd();
  const tails = TAILS.filter((k) => end.length >= k).map((k) => fingerprint(end.slice(-k)));
  for (let i = 0; i < 10; i++) {
    const held = await $.state.get(repliesRef);
    const list = held.value ?? [];
    const known = list.find((r) => r.n === turn.n);
    if (!id && known?.id) return;
    const next = [
      ...list.filter((r) => r.n !== turn.n),
      { id: id || (known?.id ?? ""), tails, n: turn.n },
    ].slice(-MAX_HISTORY);
    const { isSet } = await $.state.set(repliesRef, next, { ifVersion: held.version });
    if (isSet) return;
  }
}

// The reply a transcript row draws, by its id, or else by an ending only one answer has.
function replyFor(replies: Reply[], id: string, text: string): Reply | undefined {
  const byId = replies.find((r) => r.id !== "" && r.id === id);
  if (byId) return byId;
  const end = text.trimEnd();
  // The longest ending this row can fill; one answer must match it, not two.
  const k = TAILS.find((size) => end.length >= size);
  if (k === undefined) return undefined;
  const mark = fingerprint(end.slice(-k));
  const hits = replies.filter((r) => r.tails.includes(mark));
  return hits.length === 1 ? hits[0] : undefined;
}

// What OpenViking put in front of Claude for one answer: the memories recalled
// for the prompt (unless auto-recall is off) and what Claude's own reads and
// searches returned. Nothing is guessed from the answer's wording.
function consultedOf(turn: Turn, recallHidden: boolean) {
  const byScore = (a: RecallItem, b: RecallItem) => b.score - a.score;
  const items = recallHidden ? [] : turn.items.filter((it) => !isEmptyDoc(it));
  const recalled = new Set(items.map((it) => it.uri));
  // Relevance of what Claude found: a file it chose to open ranks first, a search
  // result by the score the search gave it.
  const relevance = new Map<string, number>();
  for (const l of turn.lookups ?? []) {
    if (l.isError || l.kind !== "read") continue;
    for (const u of l.uris) {
      if (!IS_URI.test(u) || recalled.has(u)) continue;
      const r = OPEN_TOOLS.has(l.tool) ? 1 : (l.scores?.[u] ?? 0);
      relevance.set(u, Math.max(relevance.get(u) ?? 0, r));
    }
  }
  const found = [...relevance.keys()];
  const strong = items.filter((it) => isStrong(it)).sort(byScore);
  const weak = items.filter((it) => !isStrong(it)).sort(byScore);
  // Everything consulted, most relevant first.
  const ranked = [
    ...items.map((it) => ({
      uri: it.uri,
      from: "recall" as const,
      score: it.score,
      isWeak: !isStrong(it),
    })),
    ...found.map((u) => ({
      uri: u,
      from: "lookup" as const,
      score: relevance.get(u) ?? 0,
      isWeak: false,
    })),
  ].sort((a, b) => Number(a.isWeak) - Number(b.isWeak) || b.score - a.score);
  // Of what was consulted: how much is about the person, and how much Claude
  // opened in full (a read, not just a search hit or a recalled summary).
  const opened = new Set(
    (turn.lookups ?? []).filter((l) => !l.isError && OPEN_TOOLS.has(l.tool)).flatMap((l) => l.uris),
  );
  const all = ranked.map((r) => r.uri);
  const byCategory = { prefs: 0, history: 0, work: 0, docs: 0, skill: 0 };
  for (const u of all) byCategory[categoryOf(u)] += 1;
  const openedFull = all.filter((u) => opened.has(u)).length;
  return {
    strong,
    weak,
    found,
    ranked,
    byCategory,
    openedFull,
    total: strong.length + weak.length + found.length,
  };
}

// "1 preference · 1 from your history · 6 work memory · 3 team docs · 1 skill · 2 opened in full", zero groups left out.
function breakdown(
  t: Strings,
  c: { byCategory: Record<Category, number>; openedFull: number },
): string {
  const parts = [
    c.byCategory.prefs ? t.catPrefs(c.byCategory.prefs) : "",
    c.byCategory.history ? t.catHistory(c.byCategory.history) : "",
    c.byCategory.work ? t.catWork(c.byCategory.work) : "",
    c.byCategory.docs ? t.catDocs(c.byCategory.docs) : "",
    c.byCategory.skill ? t.catSkills(c.byCategory.skill) : "",
    c.openedFull ? t.openedFull(c.openedFull) : "",
  ].filter(Boolean);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function consultedUris(turn: Turn, recallHidden: boolean): string[] {
  const c = consultedOf(turn, recallHidden);
  return [...c.strong, ...c.weak].map((it) => it.uri).concat(c.found);
}

// Change one turn, the current one and its copy in the history alike. Tool
// calls run in parallel, so each write is checked against the version read.
async function editTurn($: EngineInterface, n: number, fn: (turn: Turn) => Turn) {
  for (let i = 0; i < 10; i++) {
    const held = await $.state.get(turnRef);
    if (!held.value || held.value.n !== n) break;
    const { isSet } = await $.state.set(turnRef, fn(held.value), { ifVersion: held.version });
    if (isSet) break;
  }
  for (let i = 0; i < 10; i++) {
    const held = await $.state.get(historyRef);
    const list = held.value ?? [];
    const { isSet } = await $.state.set(
      historyRef,
      list.map((h) => (h.n === n ? fn(h) : h)),
      { ifVersion: held.version },
    );
    if (isSet) break;
  }
}

async function unmuteAll($: EngineInterface) {
  await $.state.set(mutedRef, []);
  await saveSession($);
}

// ---------- files ----------

async function readFileAt(
  $: EngineInterface,
  path: string,
): Promise<{ text: string; mtimeMs: number } | null> {
  try {
    const info = await $.fs.stat(path);
    if (info.kind !== "file" || info.size > 4 * 1024 * 1024) return null;
    return { text: await $.fs.read(path), mtimeMs: info.mtimeMs };
  } catch {
    return null;
  }
}

// openviking-memory mirrors the startup context to ~/.openviking/last_inject.md
// and keeps its state snapshots under $OPENVIKING_HOME/state (default ~/.openviking/state).
async function stateDir($: EngineInterface) {
  const home = (await $.env.get("HOME")) ?? "";
  const ov = ((await $.env.get("OPENVIKING_HOME")) ?? "").trim().replace(/^~(?=$|\/)/, home);
  return `${ov || `${home}/.openviking`}/state`;
}

// One of openviking-memory's state snapshots, if it is about this session and,
// given `since`, written no earlier: an older one belongs to an earlier prompt.
async function readState(
  $: EngineInterface,
  name: string,
  sessionId: string,
  since?: number,
): Promise<Record<string, unknown> | null> {
  const file = await readFileAt($, `${await stateDir($)}/${name}`);
  if (!file) return null;
  try {
    const r = JSON.parse(file.text) as unknown;
    if (!r || typeof r !== "object") return null;
    const rec = r as Record<string, unknown>;
    if (rec.cc_session_id !== sessionId) return null;
    if (since !== undefined && typeof rec.ts === "number" && rec.ts < since) return null;
    return rec;
  } catch {
    return null;
  }
}

type InstallRecord = {
  scope?: string;
  projectPath?: string;
  installPath?: string;
  version?: string;
};

// The openviking-memory install Claude Code runs hooks from, as its plugin
// registry records it: an install scoped to this project wins over the user-wide one.
async function memoryPlugin(
  $: EngineInterface,
  cwd: string,
): Promise<{ path: string; version: string }> {
  const registry = await readFileAt(
    $,
    `${await $.env.get("HOME")}/.claude/plugins/installed_plugins.json`,
  );
  let plugins: Record<string, unknown> = {};
  try {
    plugins = registry
      ? ((JSON.parse(registry.text) as { plugins?: Record<string, unknown> }).plugins ?? {})
      : {};
  } catch {
    throw new Error("Claude Code's plugin registry is unreadable");
  }
  const records = Object.entries(plugins)
    .filter(([id]) => id.startsWith("openviking-memory@"))
    .flatMap(([, v]) => (Array.isArray(v) ? v : [v]) as InstallRecord[]);
  const inProject = (r: InstallRecord) =>
    !!r.projectPath && (cwd === r.projectPath || cwd.startsWith(`${r.projectPath}/`));
  const record = records.find(inProject) ?? records.find((r) => r.scope === "user") ?? records[0];
  if (!record?.installPath) throw new Error("OpenViking memory plugin not installed");
  return { path: record.installPath, version: record.version ?? "?" };
}

async function loadLastInject($: EngineInterface) {
  const file = await readFileAt($, `${await $.env.get("HOME")}/.openviking/last_inject.md`);
  const parsed = file ? parseStartup(file.text, new Date(file.mtimeMs).toISOString()) : null;
  if (parsed) await $.state.set(injectRef, parsed);
}

// openviking-memory keeps only snapshots of the latest recall and capture, no
// history, so the totals across sessions are ov-usage's own, kept since install.
type LifetimeStore = { recalls: number; commitsBySession: Record<string, number> };

async function readLifetimeStore($: EngineInterface): Promise<LifetimeStore> {
  const v = (await $.store.get("lifetime")) as Partial<LifetimeStore> | undefined;
  return {
    recalls: typeof v?.recalls === "number" ? v.recalls : 0,
    commitsBySession:
      v?.commitsBySession && typeof v.commitsBySession === "object" ? v.commitsBySession : {},
  };
}

function lifetimeOf(s: LifetimeStore): Lifetime {
  const counts = Object.values(s.commitsBySession);
  return {
    recalls: s.recalls,
    commits: counts.reduce((a, b) => a + b, 0),
    conversations: counts.filter((c) => c > 0).length,
  };
}

async function loadLifetime($: EngineInterface) {
  await $.state.set(lifetimeRef, lifetimeOf(await readLifetimeStore($)));
}

// Adds a prompt recall gave memories to, and takes the commit count of this
// session's last capture (cumulative per session, so the highest seen wins).
async function countLifetime($: EngineInterface, sessionId: string, isRecalled: boolean) {
  const s = await readLifetimeStore($);
  if (isRecalled) s.recalls += 1;
  const capture = await readState($, "last-capture.json", sessionId);
  const commits = typeof capture?.commit_count === "number" ? capture.commit_count : 0;
  if (commits > (s.commitsBySession[sessionId] ?? 0)) s.commitsBySession[sessionId] = commits;
  await $.store.set("lifetime", s);
  await $.state.set(lifetimeRef, lifetimeOf(s));
}

// Runs OpenViking's own config loader so file values, env overrides and defaults all count.
// The script prints only the keys below; the API key leaves as a boolean.
// A failure is printed as { error } rather than a stack, so the pane can say what went wrong.
const SETTINGS_SCRIPT = `
try {
  const { loadConfig } = await import(process.env.OV_PLUGIN_SCRIPTS + '/config.mjs')
  if (typeof loadConfig !== 'function') throw new Error('its config.mjs has no loadConfig')
  const c = loadConfig(process.cwd())
  console.log(JSON.stringify({
    server: c.baseUrl || '', apiKeySet: Boolean(c.apiKey), autoRecall: c.autoRecall !== false,
    scoreThreshold: c.scoreThreshold, recallLimit: c.recallLimit, recallTokenBudget: c.recallTokenBudget,
    recallPeerScope: c.recallPeerScope, autoCapture: c.autoCapture !== false,
    commitTurnThreshold: c.commitTurnThreshold, startupInject: !c.noAutoInject,
    profileTokenBudget: c.profileTokenBudget, mcpEnabled: c.mcpEnabled !== false, error: null,
  }))
} catch (err) {
  console.log(JSON.stringify({ error: String(err && err.message || err).split('\\n')[0] }))
}
`;

// The loader's last output line, checked before it is trusted.
function settingsFrom(stdout: string): OvSettings {
  let r: unknown;
  try {
    r = JSON.parse(stdout.trim().split("\n").at(-1) ?? "");
  } catch {
    throw new Error("its config loader printed no settings");
  }
  const rec = (r && typeof r === "object" ? r : {}) as Partial<OvSettings>;
  if (typeof rec.error === "string" && rec.error) throw new Error(rec.error);
  if (typeof rec.autoRecall !== "boolean" || typeof rec.autoCapture !== "boolean") {
    throw new Error("its config loader returned settings in an unknown shape");
  }
  return { ...EMPTY_SETTINGS, ...rec, error: null };
}

async function loadSettings($: EngineInterface) {
  let version = "";
  try {
    const cwd = await $.session.cwd();
    const plugin = await memoryPlugin($, cwd);
    version = plugin.version;
    const run = await $.process.run(["node", "--input-type=module", "-e", SETTINGS_SCRIPT], {
      cwd,
      env: { OV_PLUGIN_SCRIPTS: `${plugin.path}/scripts` },
      timeoutMs: 15000,
    });
    if (run.exitCode !== 0)
      throw new Error(run.stderr.split("\n").find((l) => l.trim()) || `exit ${run.exitCode}`);
    await $.state.set(settingsRef, settingsFrom(run.stdout));
  } catch (err) {
    const message = String((err as Error).message ?? err);
    const error = (version ? `openviking-memory ${version}: ${message}` : message).slice(0, 160);
    const { value: prev = null } = await $.state.get(settingsRef);
    if (!prev) await $.state.set(settingsRef, { ...EMPTY_SETTINGS, error });
  }
}

const EMPTY_SETTINGS: OvSettings = {
  server: "",
  apiKeySet: false,
  autoRecall: false,
  scoreThreshold: 0,
  recallLimit: 0,
  recallTokenBudget: 0,
  recallPeerScope: "",
  autoCapture: false,
  commitTurnThreshold: 0,
  startupInject: false,
  profileTokenBudget: 0,
  mcpEnabled: false,
  error: null,
};

// ---------- persistence ----------
// $.state is wiped when the session's process restarts; $.store survives it.
// Each session's pane data is saved under its id and restored on resume.

const MAX_SAVED_SESSIONS = 30;

type Snapshot = {
  lookups: Lookup[];
  turn: Turn | null;
  turnCount: number;
  seen: Seen[];
  muted: string[];
  history?: Turn[];
  replies?: Reply[];
  opening?: Opening | null;
  savedAt: number;
};

async function saveSession($: EngineInterface) {
  const id = await $.session.id();
  if (!id) return;
  const { value: turnCount = 0 } = await $.state.get(turnCountRef);
  const snapshot: Snapshot = {
    lookups: await getLookups($),
    turn: await getTurn($),
    turnCount,
    seen: await getSeen($),
    muted: await getMuted($),
    history: (await $.state.get(historyRef)).value ?? [],
    replies: (await $.state.get(repliesRef)).value ?? [],
    opening: (await $.state.get(openingRef)).value ?? null,
    savedAt: await $.clock.now(),
  };
  await $.store.set(`session:${id}`, snapshot);
  await $.store.set("lastSave", { id, version: VERSION, at: snapshot.savedAt });
  const index = ((await $.store.get("sessions")) as string[] | undefined) ?? [];
  const next = [...index.filter((k) => k !== id), id];
  for (const old of next.slice(0, Math.max(0, next.length - MAX_SAVED_SESSIONS))) {
    await $.store.delete(`session:${old}`);
  }
  await $.store.set("sessions", next.slice(-MAX_SAVED_SESSIONS));
}

// /openviking-usage clear: forget every saved session and this one's history.
async function clearStored($: EngineInterface) {
  for (const key of await $.store.keys()) {
    if (
      key.startsWith("session:") ||
      key === "sessions" ||
      key === "lastSave" ||
      key === "lifetime"
    )
      await $.store.delete(key);
  }
  await $.state.set(lookupsRef, []);
  await $.state.set(turnRef, null);
  await $.state.set(turnCountRef, 0);
  await $.state.set(seenRef, []);
  await $.state.set(mutedRef, []);
  await $.state.set(historyRef, []);
  await $.state.set(viewTurnRef, null);
  await $.state.set(repliesRef, []);
  await $.state.set(openingRef, null);
}

async function restoreSession($: EngineInterface) {
  // A hot reload keeps $.state; only an empty session needs restoring.
  const { value: turnCount = 0 } = await $.state.get(turnCountRef);
  if (turnCount > 0) return;
  const id = await $.session.id();
  const snap = id ? ((await $.store.get(`session:${id}`)) as Snapshot | undefined) : undefined;
  if (snap && typeof snap === "object") {
    await $.state.set(lookupsRef, snap.lookups ?? []);
    await $.state.set(turnRef, snap.turn ?? null);
    await $.state.set(turnCountRef, snap.turnCount ?? 0);
    await $.state.set(seenRef, snap.seen ?? []);
    await $.state.set(mutedRef, snap.muted ?? []);
    await $.state.set(historyRef, snap.history ?? []);
    await $.state.set(repliesRef, snap.replies ?? []);
    await $.state.set(openingRef, snap.opening ?? null);
  }
  // View toggles are global preferences.
  const settings = await $.store.get("ui:showSettings");
  if (typeof settings === "boolean") await $.state.set(showSettingsRef, settings);
  const layout = await $.store.get("layout");
  if (layout === "pane" || layout === "inline") await $.state.set(layoutRef, layout);
  const detail = await $.store.get("cardDetail");
  if (detail === "low" || detail === "high") await $.state.set(cardDetailRef, detail);
}

async function getLang($: EngineInterface): Promise<Lang> {
  const { value: pref = "system" } = await $.state.get(langPrefRef);
  if (pref !== "system") return pref;
  const { value: sys = "en" } = await $.state.get(sysLangRef);
  return sys;
}

async function setLang($: EngineInterface, pref: "en" | "zh" | "system") {
  await $.state.set(langPrefRef, pref);
  await $.store.set("langPref", pref);
  await refreshStatus($);
}

// Saved choice from earlier sessions, and what "System" means on this machine:
// LANG first, then the macOS language list.
async function loadLang($: EngineInterface) {
  const saved = await $.store.get("langPref");
  if (saved === "en" || saved === "zh" || saved === "system") await $.state.set(langPrefRef, saved);
  let sys: Lang = /^zh/i.test((await $.env.get("LANG")) ?? "") ? "zh" : "en";
  if (sys === "en") {
    try {
      const run = await $.process.run(["defaults", "read", "-g", "AppleLanguages"], {
        timeoutMs: 3000,
      });
      const first = /"?([A-Za-z-]+)"?/.exec(run.stdout.replace(/[()\s,]+/, ""))?.[1] ?? "";
      if (run.exitCode === 0 && /^zh/i.test(first)) sys = "zh";
    } catch {
      // Not macOS, or no language list: keep English.
    }
  }
  await $.state.set(sysLangRef, sys);
}

// The status line, on the cards' terms: what this answer consulted, by group
// (★ preferences ◷ history ◆ work memory ▤ team docs ⚙ skills), then the
// session's unique sources and successful write-backs. Before the first
// answer it says how many memories the startup context brought.
const STATUS_ICONS: [Category, string][] = [
  ["prefs", "★"],
  ["history", "◷"],
  ["work", "◆"],
  ["docs", "▤"],
  ["skill", "⚙"],
];

async function refreshStatus($: EngineInterface) {
  const t = STRINGS[await getLang($)];
  const turn = await getTurn($);
  const x = await sessionTotals($);
  if (!turn) {
    $.ui.status(x.hasInject ? `OV · ${t.statusReady(x.files)}` : undefined);
    return;
  }
  const c = consultedOf(turn, await isRecallHidden($));
  const groups = STATUS_ICONS.filter(([k]) => c.byCategory[k] > 0)
    .map(([k, icon]) => `${icon}${c.byCategory[k]}`)
    .join(" ");
  const parts = [
    c.total ? `${t.statusThis} ✓${c.total}${groups ? ` (${groups})` : ""}` : t.statusNone,
    x.used ? t.statusSession(x.used) : "",
    x.saved ? t.statusSaved(x.saved) : "",
  ].filter(Boolean);
  $.ui.status(`OV · ${parts.join(" · ")}`);
}

async function onStartupContext($: EngineInterface, contexts: readonly string[] | undefined) {
  const text = (contexts ?? []).find((c) => c.includes("<openviking-context"));
  const parsed = text ? parseStartup(text, new Date(await $.clock.now()).toISOString()) : null;
  if (!parsed) return;
  await $.state.set(injectRef, parsed);
  $.ui.toast(STRINGS[await getLang($)].injectedToast(parsed.source));
}

async function recordTurn(
  $: EngineInterface,
  prompt: string,
  sessionId: string,
  contexts: readonly string[] | undefined,
  mutedHits: number,
  startedAt: number,
): Promise<Turn> {
  const { value: prev = 0 } = await $.state.get(turnCountRef);
  const n = prev + 1;
  await $.state.set(turnCountRef, n);

  const block = (contexts ?? []).find(
    (c) =>
      c.includes("<openviking-context") &&
      !/source="(startup|resume|compact|skill-experience)"/.test(c),
  );
  const raw = block ? parseRecall(block) : [];

  let reason = block ? "ok" : "none";
  let latencyMs: number | null = null;
  let tokensUsed: number | null = null;
  const last = await readState($, "last-recall.json", sessionId, startedAt);
  if (last) {
    if (typeof last.reason === "string") reason = last.reason;
    latencyMs = typeof last.latency_ms === "number" ? last.latency_ms : null;
    tokensUsed = typeof last.tokens_used === "number" ? last.tokens_used : null;
  }

  const seen = await getSeen($);
  const items: RecallItem[] = raw.map((it) => ({
    ...it,
    firstTurn: seen.find((s) => s.uri === it.uri)?.turn ?? n,
  }));
  const fresh = items
    .filter((it) => it.firstTurn === n)
    .map((it) => ({ uri: it.uri, turn: n, tokens: it.tokens }));
  if (fresh.length) await $.state.set(seenRef, [...seen, ...fresh]);

  const turn: Turn = {
    n,
    query: redact(prompt.replace(/\s+/g, " ")).slice(0, 120),
    at: new Date(await $.clock.now()).toISOString(),
    latencyMs,
    tokensUsed,
    items,
    mutedHits,
    lookups: [],
    reason,
  };
  await $.state.set(turnRef, turn);
  const { value: opening = null } = await $.state.get(openingRef);
  if (!opening)
    await $.state.set(openingRef, { id: "", text: fingerprint(prompt.trim().slice(0, HEAD)) });
  const { value: history = [] } = await $.state.get(historyRef);
  await $.state.set(historyRef, [...history, turn].slice(-MAX_HISTORY));
  await $.state.set(viewTurnRef, null);
  return turn;
}

// ---------- cards in the conversation ----------

// One source as a card row: its kind's icon and name; at high detail, its full
// URI under it. Copies of the same document (one name, several URIs) share a row.
function cardRow(
  $: EngineInterface,
  e: ResolveInput,
  key: string,
  uris: string[],
  extra: string,
  isDim: boolean,
  isHigh: boolean,
) {
  const { Box, Text } = $.ui.resolve(e);
  const uri = uris[0] ?? "";
  const kind = kindOf(uri);
  return (
    <Box key={key} flexDirection="column">
      <Text wrap="wrap" dimColor={isDim}>
        <Text color={isDim ? undefined : kind.color}>
          {"  "}
          {kind.icon}{" "}
        </Text>
        {dateOf(uri)}
        {shortName(uri)}
        {uris.length > 1 && <Text dimColor> ×{uris.length}</Text>}
        <Text dimColor>{extra}</Text>
      </Text>
      {isHigh &&
        uris.map((u) => (
          <Text key={`${key}-${u}`} dimColor wrap="wrap">
            {"    "}
            {u}
          </Text>
        ))}
    </Box>
  );
}

// What one answer referred to, drawn under the answer. Auto-recall shows only
// when the answer used something it recalled; less relevant items wait behind
// [show]. An answer that used nothing from OpenViking and looked nothing up
// gets no card.
const CARD_TOP = 10;

async function answerCard($: EngineInterface, e: ResolveInput, turn: Turn) {
  const { Box, Text, Button } = $.ui.resolve(e);
  const t = STRINGS[await getLang($)];
  const { value: detail = "low" } = await $.state.get(cardDetailRef);
  const { value: showAll = false } = await $.state.get(showAllCardRef);
  const recallHidden = await isRecallHidden($);
  const c = consultedOf(turn, recallHidden);
  const lookups = turn.lookups ?? [];
  if (c.total === 0 && lookups.length === 0) return null;
  // One row per document name, ranked by its best source; top 10 unless expanded.
  const groups: {
    name: string;
    uris: string[];
    from: "recall" | "lookup";
    score: number;
    isWeak: boolean;
  }[] = [];
  for (const r of c.ranked) {
    const name = `${dateOf(r.uri)}${shortName(r.uri)}`;
    const g = groups.find((x) => x.name === name);
    if (g) g.uris.push(r.uri);
    else groups.push({ name, uris: [r.uri], from: r.from, score: r.score, isWeak: r.isWeak });
  }
  // Less relevant recalls always wait behind "+N more", with anything past the top 10.
  const top = groups.filter((g) => !g.isWeak).slice(0, CARD_TOP);
  const hidden = groups.length - top.length;
  const shown = showAll ? groups : top;
  const label = (g: (typeof groups)[number]) =>
    ` · ${g.from === "recall" ? t.viaRecall : t.viaLookup}${g.from === "recall" && g.score > 0 ? ` · ${g.score.toFixed(2)}` : ""}`;
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={ACCENT} paddingX={1} marginTop={1}>
      <Text wrap="wrap">
        <Text bold color={ACCENT}>
          {t.cardAnswer}
        </Text>
        {!recallHidden && turn.latencyMs != null && turn.reason === "ok" && (
          <Text dimColor> · {(turn.latencyMs / 1000).toFixed(1)}s</Text>
        )}
      </Text>
      {c.total > 0 && (
        <Box flexDirection="column">
          <Text wrap="wrap">
            <Text bold color="green">
              {t.usedSection(c.total)}
            </Text>
            <Text dimColor>{breakdown(t, c)}</Text>
          </Text>
          {shown.map((g) =>
            cardRow($, e, `card-src-${g.uris[0]}`, g.uris, label(g), g.isWeak, detail === "high"),
          )}
          {hidden > 0 && (
            <Button
              key="toggle-card-more"
              label={showAll ? t.fewer : t.moreAnswers(hidden)}
              plain
              onPress={() => toggleCardMore($)}
            />
          )}
        </Box>
      )}
      {lookups.length > 0 && (
        <Box flexDirection="column">
          <Text bold>{t.lookedUp}</Text>
          {lookups.map((l) => {
            const d = describeLookup(t, l);
            return (
              <Text key={`card-lookup-${l.id}`} wrap="wrap">
                {"  "}
                {d.icon} {d.verb} {d.what}
                {d.count !== null && <Text dimColor> · {d.count}</Text>}
                {l.isError && <Text color="red"> · {t.failed}</Text>}
              </Text>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

// All sessions and this session, drawn above the conversation's first prompt.
async function overviewCard($: EngineInterface, e: ResolveInput) {
  const { Box, Text } = $.ui.resolve(e);
  const t = STRINGS[await getLang($)];
  const x = await sessionTotals($);
  const recallHidden = await isRecallHidden($);
  const all = [
    x.cfg && !x.cfg.error
      ? `${t.recallState(x.cfg.autoRecall)} · ${t.captureState(x.cfg.autoCapture)}`
      : "",
    !recallHidden && x.life && x.life.recalls > 0 ? t.promptsWithMemory(x.life.recalls) : "",
    x.life && x.life.commits > 0 ? t.updatesFrom(x.life.commits, x.life.conversations) : "",
    x.hasInject ? t.startupLine(x.files, x.role) : "",
  ].filter(Boolean);
  // What was used comes first: that is OpenViking's value in this session.
  const session = [
    x.used > 0 ? t.usedLine(x.used) : "",
    !recallHidden && x.sessionRecalled > 0
      ? t.recalledAcross(x.sessionRecalled, Math.max(1, x.sessionPrompts))
      : "",
    x.saved > 0 ? t.savedLine(x.saved) : "",
  ].filter(Boolean);
  const section = (key: string, title: string, lines: string[]) => (
    <Box key={key} flexDirection="column">
      <Text bold dimColor>
        {title}
      </Text>
      {lines.map((l, i) => (
        <Text key={`${key}-${i}`} wrap="wrap">
          {"  "}
          {l}
        </Text>
      ))}
    </Box>
  );
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={ACCENT}
      paddingX={1}
      marginBottom={1}
    >
      <Text bold color={ACCENT}>
        {t.cardOverview}
      </Text>
      {all.length > 0 && section("card-all", t.allSessions, all)}
      {session.length > 0 && section("card-session", t.thisConversation, session)}
      {all.length === 0 && session.length === 0 && <Text dimColor>{t.waiting}</Text>}
    </Box>
  );
}

// ---------- hooks ----------

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    await loadLang($);
    await restoreSession($);
    await $.command.register({
      name: "openviking-usage",
      description: "Show what OpenViking gave Claude: this turn, this session, and since install",
      argumentHint:
        "[history | answer N | latest | details | weak | settings | show sidebar|conversation | card low|high | unmute | clear | lang en|zh|system]",
    });
    await saveSession($);
    if (!(await getInject($))) await loadLastInject($);
    await loadLifetime($);
    await loadSettings($);
    await refreshStatus($);
    // In "conversation" layout the details come as cards, so the pane waits to be asked for.
    const { value: layout = "pane" } = await $.state.get(layoutRef);
    if (layout === "pane") void $.ui.open({ id: PANE, title: TITLE });

    return next(e);
  });

  // Every pane button also works as an argument, since clicks in a restored
  // desktop pane can be dropped before they reach the mod.
  on("command.run", { command: "openviking-usage" }, async ($, e) => {
    await loadLastInject($);
    await loadLifetime($);
    await loadSettings($);
    await refreshStatus($);
    const [verb = "", arg = ""] = e.args.trim().toLowerCase().split(/\s+/);
    const keys: Record<string, string> = {
      history: "toggle-history",
      details: "toggle-details",
      weak: "toggle-weak",
      settings: "toggle-settings",
      latest: "back-latest",
      unmute: "unmute-all",
    };
    let done = "Opened the OV-Usage sidebar.";
    if (verb === "answer" && /^\d+$/.test(arg)) {
      await handlePress($, `turn-${arg}`);
      done = `Showing answer #${arg}.`;
    } else if (verb === "clear") {
      await clearStored($);
      done = "Cleared OV-Usage's saved answers and session data.";
    } else if (verb === "show" && ["sidebar", "pane", "conversation", "inline"].includes(arg)) {
      const layout = arg === "sidebar" || arg === "pane" ? "pane" : "inline";
      await setLayout($, layout);
      done =
        layout === "pane"
          ? "OV-Usage now shows details in the sidebar."
          : "OV-Usage now shows cards in the conversation: one under each answer, and one with totals above the first prompt.";
      // Switching to cards needs no pane; the command's own reply says what changed.
      if (layout === "inline") return { text: done };
    } else if (verb === "card" && (arg === "low" || arg === "high")) {
      await setCardDetail($, arg);
      done =
        arg === "low"
          ? "Cards show source titles only."
          : "Cards show source titles and their viking:// URIs.";
      return { text: done };
    } else if (verb === "lang" && ["en", "zh", "system"].includes(arg)) {
      await handlePress($, `lang-${arg}`);
      done = `Language: ${arg}.`;
    } else if (keys[verb]) {
      await handlePress($, keys[verb] as string);
      done = `Toggled ${verb}.`;
    } else if (verb !== "") {
      done =
        "Usage: /openviking-usage [history | answer N | latest | details | weak | settings | show sidebar|conversation | card low|high | unmute | clear | lang en|zh|system]";
    }
    await $.ui.open({ id: PANE, title: TITLE, focus: true });

    return { text: done };
  });

  on("classic.SessionStart", async ($, e, next) => {
    const res = await next(e);
    await onStartupContext($, res.additionalContext);
    await refreshStatus($);

    return res;
  });

  // Per-prompt recall: record it, and drop anything the person muted this session.
  on("classic.UserPromptSubmit", async ($, e, next) => {
    const startedAt = await $.clock.now();
    const res = await next(e);
    const { contexts, hits } = stripMuted(res.additionalContext, await getMuted($));
    const turn = await recordTurn($, e.prompt, e.session_id, contexts, hits, startedAt);
    await saveSession($);
    await countLifetime($, e.session_id, turn.items.length > 0);
    // Not awaited: the config loader spawns node. A late failure (session ended) is harmless.
    void loadSettings($).catch(() => {});
    await refreshStatus($);

    return hits ? { ...res, additionalContext: contexts ? [...contexts] : undefined } : res;
  });

  on("turn.complete", async ($, e, next) => {
    const done = await next(e);
    const answer = done.text || e.answer;
    if (answer) await noteReply($, "", answer);
    // A capture that committed during this turn shows in the totals now.
    const sessionId = await $.session.id();
    if (sessionId) await countLifetime($, sessionId, false);
    await refreshStatus($);

    return done;
  });

  // Rows of the main conversation: the first prompt carries the overview card,
  // the text block that ends each answer carries that answer's card.
  on("session.append", async ($, e, next) => {
    const res = await next(e);
    if (res.deny !== undefined || e.agentId) return res;
    if (e.door === "prompt" && e.message.type === "user") {
      const { value: opening = null } = await $.state.get(openingRef);
      const text = (res.message.content ?? [])
        .map((b) =>
          b && typeof b === "object" && "type" in b && b.type === "text"
            ? String((b as { text?: string }).text ?? "")
            : "",
        )
        .join("");
      if (!opening)
        await $.state.set(openingRef, {
          id: res.uuid,
          text: fingerprint(text.trim().slice(0, HEAD)),
        });
      else if (!opening.id) await $.state.set(openingRef, { ...opening, id: res.uuid });
    } else if (e.door === "response" && e.message.type === "assistant") {
      const text = (res.message.content ?? [])
        .map((b) =>
          b && typeof b === "object" && "type" in b && b.type === "text"
            ? String((b as { text?: string }).text ?? "")
            : "",
        )
        .join("");
      if (text.trim()) await noteReply($, res.uuid, text);
    }
    return res;
  });

  on("ui.render", { component: "AssistantMessage" }, async ($, e, next) => {
    const { value: layout = "pane" } = await $.state.get(layoutRef);
    if (layout !== "inline") return next(e);
    const { value: replies = [] } = await $.state.get(repliesRef);
    const hit = replyFor(replies, e.requestId, e.props.text);
    if (!hit) return next(e);
    const { value: history = [] } = await $.state.get(historyRef);
    const turn = history.find((h) => h.n === hit.n);
    if (!turn) return next(e);
    const card = await answerCard($, e, turn);
    if (card === null) return next(e);
    const { Box } = $.ui.resolve(e);
    return (
      <Box flexDirection="column">
        {await next(e)}
        {card}
      </Box>
    );
  });

  on("ui.render", { component: "UserMessage" }, async ($, e, next) => {
    const { value: layout = "pane" } = await $.state.get(layoutRef);
    if (layout !== "inline") return next(e);
    const { value: opening = null } = await $.state.get(openingRef);
    const isOpening =
      opening !== null &&
      (opening.id === e.requestId ||
        (opening.text !== "" && fingerprint(e.props.text.trim().slice(0, HEAD)) === opening.text));
    if (!isOpening) return next(e);
    const { Box } = $.ui.resolve(e);
    return (
      <Box flexDirection="column">
        {await overviewCard($, e)}
        {await next(e)}
      </Box>
    );
  });

  // Every lookup or write Claude makes against OpenViking.
  on("tool.call", async ($, e, next) => {
    const mcp = OV_TOOL.exec(e.tool);
    const cmd =
      e.tool === "Bash" ? String((e as unknown as { command?: string }).command ?? "") : "";
    // Only a real `ov` / `openviking` CLI call counts; a command that merely
    // mentions a viking:// path (writing docs, grepping, scripts) is not a lookup.
    const bash = cmd !== "" && OV_CLI.test(cmd);
    if (!mcp && !bash) return next(e);

    const args = e as unknown as Record<string, unknown>;
    const name = mcp?.[1] ?? "ov cli";
    const uriList = Array.isArray(args.uris) ? args.uris.join(" ") : undefined;
    // A shell command is never stored; only the viking:// URIs it names.
    const cmdUris = bash ? [...new Set(cmd.match(URI) ?? [])].join(" ") : "";
    const target = redact(
      String(mcp ? (args.uri ?? uriList ?? args.path ?? args.target ?? "") : cmdUris),
    );
    const one: Lookup = {
      id: e.tool_use_id,
      tool: name,
      kind:
        WRITE_TOOLS.has(name) || /\bov\s+(add|write|remember|rm|mv|edit)\b/.test(cmd)
          ? "write"
          : "read",
      query: redact(
        String(
          mcp ? (args.query ?? args.uri ?? uriList ?? args.pattern ?? args.path ?? "") : cmdUris,
        ),
      ).slice(0, 80),
      target,
      uris: [],
      isDone: false,
      isError: false,
    };
    await editLookups($, (list) => [...list, one].slice(-100));
    await refreshStatus($);

    const ran = await next(e);
    const text = "text" in ran && typeof ran.text === "string" ? ran.text : "";
    const uris = urisIn(text).slice(0, 20);
    const scores: Record<string, number> = {};
    for (const line of text.split("\n")) {
      const pct = /\[[\w-]+\s+(\d{1,3})%\]/.exec(line)?.[1];
      const uri = pct ? URI_LINE.exec(line)?.[1] : undefined;
      if (uri && uris.includes(uri)) scores[uri] = Number(pct) / 100;
    }
    const isError = ran.deny !== undefined || ran.isError === true;
    await editLookups($, (list) =>
      list.map((l) => (l.id === one.id ? { ...l, uris, isDone: true, isError } : l)),
    );
    // What this answer referred to: a read names its files, a search its results.
    const turn = await getTurn($);
    if (turn && !QUIET_TOOLS.has(name)) {
      const named = [...new Set(target.match(URI) ?? [])];
      const ref: TurnLookup = {
        id: one.id,
        tool: name,
        kind: one.kind,
        query: one.query,
        uris: (OPEN_TOOLS.has(name) || one.kind === "write") && named.length ? named : uris,
        scores,
        isError,
      };
      await editTurn($, turn.n, (t) => ({ ...t, lookups: [...(t.lookups ?? []), ref] }));
    }
    await refreshStatus($);
    await saveSession($);

    return ran;
  });

  on("ui.press", async ($, e, next) => {
    if (e.plugin !== "ov-usage") return next(e);
    return (await handlePress($, e.element)) ? { element: e.element } : next(e);
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e);
    try {
      const inj = await getInject($);
      const list = await getLookups($);
      const seen = await getSeen($);
      const latest = await getTurn($);
      const { value: history = [] } = await $.state.get(historyRef);
      const { value: viewN = null } = await $.state.get(viewTurnRef);
      const { value: showHistory = false } = await $.state.get(showHistoryRef);
      // The answer the pane shows: the latest, or one picked from the history.
      const turn = (viewN !== null ? history.find((h) => h.n === viewN) : undefined) ?? latest;
      const isPast = turn !== null && latest !== null && turn.n !== latest.n;
      const clock = (iso: string) => {
        const d = new Date(iso);
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      };
      const {
        sessionRecalled,
        sessionAboutYou,
        sessionPrompts,
        opened,
        saved,
        used,
        files,
        role,
        life,
      } = await sessionTotals($);
      const muted = await getMuted($);
      const { value: layout = "pane" } = await $.state.get(layoutRef);
      const { value: cardDetail = "low" } = await $.state.get(cardDetailRef);
      const { value: showWeak = false } = await $.state.get(showWeakRef);
      const { value: showDetails = false } = await $.state.get(showDetailsRef);
      const { value: cfg = null } = await $.state.get(settingsRef);
      const { value: showSettings = false } = await $.state.get(showSettingsRef);
      const { value: langPref = "system" } = await $.state.get(langPrefRef);
      const t = STRINGS[await getLang($)];

      const items = (turn?.items ?? []).filter((it) => !isEmptyDoc(it));
      const emptyCount = (turn?.items ?? []).length - items.length;
      const repeats = items.filter((it) => it.firstTurn !== turn?.n).length;
      const lookups = turn?.lookups ?? [];
      // Consulted: everything OpenViking put in front of Claude for this answer.
      const recallHidden = cfg !== null && !cfg.error && !cfg.autoRecall;
      const c = turn
        ? consultedOf(turn, recallHidden)
        : {
            strong: [],
            weak: [],
            found: [],
            ranked: [],
            byCategory: { prefs: 0, history: 0, work: 0, docs: 0, skill: 0 },
            openedFull: 0,
            total: 0,
          };
      const consultedIn = (past: Turn) => consultedOf(past, recallHidden).total;
      // [details] adds recall's diagnostics: why nothing came back, repeats, empty documents.
      const showDiag = showDetails && !recallHidden;
      // Earlier answers: newest first, without the one on screen; three unless expanded.
      const EARLIER_DEFAULT = 3;
      const earlier = [...history].reverse().filter((past) => turn === null || past.n !== turn.n);
      const earlierShown = showHistory ? earlier : earlier.slice(0, EARLIER_DEFAULT);

      // One heading style for every section inside the boxes.
      const heading = (text: string, extra?: string) => (
        <Text wrap="wrap">
          <Text bold color={ACCENT}>
            {text}
          </Text>
          {extra && <Text dimColor>{extra}</Text>}
        </Text>
      );

      const row = (it: RecallItem) => {
        const kind = kindOf(it.uri);
        const isOpened = isExpanded(it.uri, list);
        const title = titleOf(it.uri);
        const g = gist(it.summary);
        return (
          <Box key={`item-${it.uri}`} flexDirection="column" marginTop={1}>
            <Text wrap="wrap">
              <Text color={kind.color} bold>
                {kind.icon} {t.kinds[kind.label]}
              </Text>
              {it.score > 0 && <Text dimColor> · {it.score.toFixed(2)}</Text>}
              {isOpened && <Text color="green"> · {t.opened}</Text>}
            </Text>
            <Text bold wrap="wrap">
              {"  "}
              {dateOf(it.uri)}
              {title ?? (g || decodeURIComponent(it.uri.split("/").slice(-2).join("/")))}
            </Text>
            {title !== null && g !== "" && !g.includes(title) && (
              <Text dimColor wrap="wrap">
                {"  "}
                {g}
              </Text>
            )}
            {showDetails && (
              <Box flexWrap="wrap" columnGap={1} marginLeft={2}>
                <Text dimColor wrap="wrap">
                  {it.layer} · ~{it.tokens} tokens · {it.uri}
                </Text>
                <Button
                  key={`mute-${it.uri}`}
                  label={t.mute}
                  plain
                  onPress={() => muteUri($, it.uri)}
                />
              </Box>
            )}
          </Box>
        );
      };

      const short = shortName;

      const refRow = (uri: string) => {
        const kind = kindOf(uri);
        return (
          <Box key={`ref-${uri}`} flexDirection="column" marginTop={1}>
            <Text wrap="wrap">
              <Text color={kind.color} bold>
                {kind.icon} {t.kinds[kind.label]}
              </Text>
              <Text dimColor> · {t.viaLookup}</Text>
            </Text>
            <Text bold wrap="wrap">
              {"  "}
              {dateOf(uri)}
              {short(uri)}
            </Text>
            {showDetails && (
              <Text dimColor wrap="wrap">
                {"  "}
                {uri}
              </Text>
            )}
          </Box>
        );
      };

      const lookupRow = (l: TurnLookup) => {
        const { icon, verb, what, count } = describeLookup(t, l);
        return (
          <Box key={`lookup-${l.id}`} flexDirection="column">
            <Text wrap="wrap">
              <Text bold>
                {icon} {verb}{" "}
              </Text>
              <Text>{what}</Text>
              {count !== null && <Text dimColor> · {count}</Text>}
              {l.isError && <Text color="red"> · {t.failed}</Text>}
            </Text>
            {showDetails && (
              <Text dimColor wrap="wrap">
                {"  "}
                {l.tool}
                {l.uris.length ? ` · ${l.uris.slice(0, 5).join(" ")}` : ""}
              </Text>
            )}
          </Box>
        );
      };

      const settingRow = (label: string, isOn: boolean | null, rest: string) => (
        <Text wrap="wrap">
          <Text bold>{label} </Text>
          {isOn !== null && <Text color={isOn ? "green" : "yellow"}>{isOn ? t.on : t.off}</Text>}
          <Text dimColor>{rest}</Text>
        </Text>
      );

      return (
        <Box flexDirection="column">
          {/* ======== all sessions ======== */}
          <Text bold dimColor>
            {t.allSessions}
          </Text>
          <Box flexDirection="column" borderStyle="round" borderDimColor paddingX={1}>
            {cfg && (
              <Box flexWrap="wrap" columnGap={2}>
                <Button
                  key="toggle-settings"
                  label={`[${showSettings ? "▾" : "▸"} ${t.settings}]`}
                  plain
                  onPress={() => toggle($, "settings")}
                />
                {!cfg.error && (
                  <Text dimColor>
                    {t.recallState(cfg.autoRecall)} · {t.captureState(cfg.autoCapture)}
                  </Text>
                )}
              </Box>
            )}
            {cfg && showSettings && (
              <Box flexDirection="column" marginLeft={2} marginBottom={1}>
                {cfg.error ? (
                  <Text dimColor wrap="wrap">
                    {t.configError(cfg.error)}
                  </Text>
                ) : (
                  <Box flexDirection="column">
                    {settingRow(
                      t.rowRecall,
                      cfg.autoRecall,
                      cfg.autoRecall ? t.recallDetail(cfg.scoreThreshold, cfg.recallLimit) : "",
                    )}
                    {settingRow(
                      t.rowCapture,
                      cfg.autoCapture,
                      cfg.autoCapture ? t.captureOn(cfg.commitTurnThreshold) : t.captureOff,
                    )}
                    {settingRow(
                      t.rowStartup,
                      cfg.startupInject,
                      cfg.startupInject
                        ? t.startupDetail(cfg.profileTokenBudget.toLocaleString())
                        : "",
                    )}
                    {settingRow(t.rowTools, cfg.mcpEnabled, "")}
                    <Text wrap="wrap">
                      <Text bold>{t.rowServer} </Text>
                      <Text dimColor>
                        {cfg.server
                          .replace(/^[a-z]+:\/\//i, "")
                          .replace(/^[^@/]*@/, "")
                          .replace(/[/?#].*$/, "") || t.notSet}
                      </Text>
                      <Text color={cfg.apiKeySet ? "green" : "red"}>
                        {cfg.apiKeySet ? t.apiKeySet : t.noApiKey}
                      </Text>
                    </Text>
                    <Box flexWrap="wrap" columnGap={2}>
                      <Text bold>{t.rowLayout}</Text>
                      <Button
                        key="layout-pane"
                        label={layout === "pane" ? `[● ${t.layoutPane}]` : `[${t.layoutPane}]`}
                        plain
                        onPress={() => setLayout($, "pane")}
                      />
                      <Button
                        key="layout-inline"
                        label={
                          layout === "inline" ? `[● ${t.layoutInline}]` : `[${t.layoutInline}]`
                        }
                        plain
                        onPress={() => setLayout($, "inline")}
                      />
                    </Box>
                    <Box flexWrap="wrap" columnGap={2}>
                      <Text bold>{t.rowCardDetail}</Text>
                      <Button
                        key="detail-low"
                        label={cardDetail === "low" ? `[● ${t.detailLow}]` : `[${t.detailLow}]`}
                        plain
                        onPress={() => setCardDetail($, "low")}
                      />
                      <Button
                        key="detail-high"
                        label={cardDetail === "high" ? `[● ${t.detailHigh}]` : `[${t.detailHigh}]`}
                        plain
                        onPress={() => setCardDetail($, "high")}
                      />
                    </Box>
                    <Box flexWrap="wrap" columnGap={2}>
                      <Text bold>{t.rowLanguage}</Text>
                      <Button
                        key="lang-en"
                        label={langPref === "en" ? "[● EN]" : "[EN]"}
                        plain
                        onPress={() => setLang($, "en")}
                      />
                      <Button
                        key="lang-zh"
                        label={langPref === "zh" ? "[● 中文]" : "[中文]"}
                        plain
                        onPress={() => setLang($, "zh")}
                      />
                      <Button
                        key="lang-system"
                        label={langPref === "system" ? `[● ${t.system}]` : `[${t.system}]`}
                        plain
                        onPress={() => setLang($, "system")}
                      />
                    </Box>
                    <Text dimColor wrap="wrap">
                      {t.changeIn}
                    </Text>
                  </Box>
                )}
              </Box>
            )}
            {!recallHidden && life && life.recalls > 0 && (
              <Text wrap="wrap">{t.promptsWithMemory(life.recalls)}</Text>
            )}
            {life && life.commits > 0 && (
              <Text wrap="wrap">{t.updatesFrom(life.commits, life.conversations)}</Text>
            )}
            {inj && <Text wrap="wrap">{t.startupLine(files, role)}</Text>}
          </Box>

          {/* ======== this conversation ======== */}
          <Box marginTop={1}>
            <Text bold dimColor>
              {t.thisConversation}
            </Text>
          </Box>
          <Box flexDirection="column" borderStyle="round" borderColor={ACCENT} paddingX={1}>
            {(used > 0 || (!recallHidden && sessionRecalled > 0) || saved > 0) && (
              <Box flexDirection="column" marginBottom={1}>
                {heading(t.thisSession)}
                {used > 0 && <Text wrap="wrap">{t.usedLine(used)}</Text>}
                {!recallHidden && sessionRecalled > 0 && (
                  <Text dimColor={used > 0} wrap="wrap">
                    {t.recalledAcross(sessionRecalled, Math.max(1, sessionPrompts))}
                  </Text>
                )}
                {saved > 0 && <Text wrap="wrap">{t.savedLine(saved)}</Text>}
                {showDetails && sessionAboutYou > 0 && (
                  <Text dimColor wrap="wrap">
                    {t.aboutYouLine(sessionAboutYou)}
                  </Text>
                )}
                {showDetails && opened > 0 && (
                  <Text dimColor wrap="wrap">
                    {t.openedLine(opened)}
                  </Text>
                )}
              </Box>
            )}
            {earlier.length > 0 && (
              <Box flexDirection="column" marginBottom={1}>
                {heading(t.earlierTitle)}
                {earlierShown.map((past) => {
                  const q = past.query.length > 28 ? `${past.query.slice(0, 28)}…` : past.query;
                  return (
                    <Box key={`row-${past.n}`} flexWrap="wrap" columnGap={1}>
                      <Button
                        key={`turn-${past.n}`}
                        label={`[#${past.n}]`}
                        plain
                        onPress={() =>
                          viewTurn($, latest !== null && past.n === latest.n ? null : past.n)
                        }
                      />
                      <Text dimColor wrap="truncate-end">
                        {clock(past.at)} “{q}”
                        {consultedIn(past) > 0 ? (
                          <Text color="green"> ✓{consultedIn(past)}</Text>
                        ) : (
                          ""
                        )}
                      </Text>
                    </Box>
                  );
                })}
                {earlier.length > EARLIER_DEFAULT && (
                  <Button
                    key="toggle-history"
                    label={showHistory ? t.fewer : t.moreAnswers(earlier.length - EARLIER_DEFAULT)}
                    plain
                    onPress={() => toggle($, "history")}
                  />
                )}
              </Box>
            )}

            <Box flexWrap="wrap" columnGap={2}>
              {heading(
                isPast && turn ? t.answerN(turn.n, clock(turn.at)) : t.thisAnswer,
                !recallHidden && turn?.latencyMs != null && turn.reason === "ok"
                  ? ` · ${(turn.latencyMs / 1000).toFixed(1)}s`
                  : undefined,
              )}
              {isPast && (
                <Button
                  key="back-latest"
                  label={t.latestBtn}
                  plain
                  onPress={() => viewTurn($, null)}
                />
              )}
              {turn && (
                <Button
                  key="toggle-details"
                  label={showDetails ? t.hideDetails : t.details}
                  plain
                  onPress={() => toggle($, "details")}
                />
              )}
            </Box>
            {turn && turn.query !== "" && (
              <Text dimColor wrap="truncate-end">
                “{turn.query}”
              </Text>
            )}
            {!turn && <Text dimColor>{t.waiting}</Text>}
            {c.total > 0 && (
              <Box flexDirection="column" marginTop={1}>
                <Text wrap="wrap">
                  <Text bold color="green">
                    {t.usedSection(c.total)}
                  </Text>
                  <Text dimColor>{breakdown(t, c)}</Text>
                </Text>
                {c.strong.map(row)}
                {c.found.map(refRow)}
                {c.weak.length > 0 && (
                  <Box flexWrap="wrap" columnGap={2} marginTop={1}>
                    <Text dimColor>{t.lowerMatches(c.weak.length)}</Text>
                    <Button
                      key="toggle-weak"
                      label={showWeak ? t.hide : t.show}
                      plain
                      onPress={() => toggle($, "weak")}
                    />
                    {showWeak && (
                      <Button
                        key="mute-weak"
                        label={t.muteThese}
                        plain
                        onPress={() =>
                          muteMany(
                            $,
                            c.weak.map((it) => it.uri),
                          )
                        }
                      />
                    )}
                  </Box>
                )}
                {showWeak && c.weak.map(row)}
              </Box>
            )}
            {turn && c.total === 0 && lookups.length === 0 && (
              <Text dimColor wrap="wrap">
                {t.nothingUsed}
              </Text>
            )}
            {lookups.length > 0 && (
              <Box flexDirection="column" marginTop={1}>
                <Text bold>{t.lookedUp}</Text>
                {lookups.map(lookupRow)}
              </Box>
            )}

            {((showDiag &&
              turn !== null &&
              (turn.items.length === 0 || repeats > 0 || emptyCount > 0)) ||
              muted.length > 0) && (
              <Box flexDirection="column" marginTop={1}>
                {showDiag && turn !== null && turn.items.length === 0 && (
                  <Text dimColor wrap="wrap">
                    {t.reasons[turn.reason] ?? t.nothingRecalled}
                  </Text>
                )}
                {showDiag && repeats > 0 && <Text dimColor>{t.alreadyInContext(repeats)}</Text>}
                {showDiag && emptyCount > 0 && <Text dimColor>{t.emptyIgnored(emptyCount)}</Text>}
                {muted.length > 0 && (
                  <Box flexWrap="wrap" columnGap={2}>
                    <Text dimColor>{t.muted(muted.length, turn?.mutedHits ?? 0)}</Text>
                    <Button
                      key="unmute-all"
                      label={t.unmuteAll}
                      plain
                      onPress={() => unmuteAll($)}
                    />
                  </Box>
                )}
              </Box>
            )}
          </Box>
          <Box marginTop={2}>
            <Text dimColor>OV-Usage {VERSION}</Text>
          </Box>
        </Box>
      );
    } catch (err) {
      return (
        <Box flexDirection="column">
          <Text color="red" wrap="wrap">
            OV-Usage {VERSION}: the pane failed to draw
          </Text>
          <Text dimColor wrap="wrap">
            {String((err as Error)?.message ?? err).slice(0, 300)}
          </Text>
        </Box>
      );
    }
  });
};
