import type { EngineInterface, Register, ResolveInput } from "claude-code";
import type {
  Inject,
  Lifetime,
  Lookup,
  Opening,
  RecallItem,
  Reply,
  Seen,
  Turn,
  TurnLookup,
} from "../types";
import { STRINGS } from "./strings";
import type { Lang } from "./strings";
import {
  HEAD,
  ROLE_LINE,
  TAILS,
  URI,
  URI_LINE,
  fingerprint,
  parseRecall,
  parseStartup,
  redact,
  replyFor,
  stripMuted,
  urisIn,
} from "./parse";
import {
  OPEN_TOOLS,
  QUIET_TOOLS,
  classifyToolCall,
  breakdown,
  consultedOf,
  consultedUris,
  dateOf,
  describeLookup,
  gist,
  isEmptyDoc,
  isExpanded,
  isStrong,
  kindOf,
  shortName,
  titleOf,
} from "./sources";
import type { Category } from "./sources";
import { EMPTY_SETTINGS, SETTINGS_SCRIPT, lifetimeOf, settingsFrom } from "./openviking";
import type { InstallRecord, LifetimeStore } from "./openviking";

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

async function readLifetimeStore($: EngineInterface): Promise<LifetimeStore> {
  const v = (await $.store.get("lifetime")) as Partial<LifetimeStore> | undefined;
  return {
    recalls: typeof v?.recalls === "number" ? v.recalls : 0,
    commitsBySession:
      v?.commitsBySession && typeof v.commitsBySession === "object" ? v.commitsBySession : {},
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
  history: Turn[];
  replies: Reply[];
  opening: Opening | null;
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
  const lookups = turn.lookups;
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
    const cmd = e.tool === "Bash" ? e.command : "";
    const call = classifyToolCall(e.tool, cmd);
    if (!call) return next(e);

    // OpenViking's MCP tool names depend on the server's name in each install, so
    // their arguments are read by name rather than through one declared type.
    const args: Record<string, unknown> = { ...e };
    const name = call.name;
    const uriList = Array.isArray(args.uris) ? args.uris.join(" ") : undefined;
    // A shell command is never stored; only the viking:// URIs it names.
    const cmdUris = call.isCli ? [...new Set(cmd.match(URI) ?? [])].join(" ") : "";
    const target = redact(
      String(call.isCli ? cmdUris : (args.uri ?? uriList ?? args.path ?? args.target ?? "")),
    );
    const one: Lookup = {
      id: e.tool_use_id,
      tool: name,
      kind: call.kind,
      query: redact(
        String(
          call.isCli
            ? cmdUris
            : (args.query ?? args.uri ?? uriList ?? args.pattern ?? args.path ?? ""),
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
      await editTurn($, turn.n, (t) => ({ ...t, lookups: [...t.lookups, ref] }));
    }
    await refreshStatus($);
    await saveSession($);

    return ran;
  });

  on("ui.press", async ($, e, next) => {
    if (e.plugin !== "ov-usage") return next(e);
    return (await handlePress($, e.element)) ? { element: e.element } : next(e);
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => renderPane($, e));
};

// The sidebar: all sessions, this conversation, and the answer it shows.
async function renderPane($: EngineInterface, e: ResolveInput) {
  const { Box, Text, Button } = $.ui.resolve(e);
  try {
    const inj = await getInject($);
    const list = await getLookups($);

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
                    cfg.autoCapture ? t.captureOn(cfg.commitTokenThreshold) : t.captureOff,
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
                      label={layout === "inline" ? `[● ${t.layoutInline}]` : `[${t.layoutInline}]`}
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
                  <Button key="unmute-all" label={t.unmuteAll} plain onPress={() => unmuteAll($)} />
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
}
