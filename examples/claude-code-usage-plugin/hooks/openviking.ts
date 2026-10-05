// The formats of what OV-Usage reads from openviking-memory: its settings, as its
// config loader reports them, its plugin registry record, and the totals across
// sessions OV-Usage keeps. Reading the files stays in register.tsx, with the hooks.

import type { Lifetime, OvSettings } from "../types";

export type InstallRecord = {
  scope?: string;
  projectPath?: string;
  installPath?: string;
  version?: string;
};

// openviking-memory keeps only snapshots of the latest recall and capture, no
// history, so the totals across sessions are ov-usage's own, kept since install.
export type LifetimeStore = { recalls: number; commitsBySession: Record<string, number> };

export function lifetimeOf(s: LifetimeStore): Lifetime {
  const counts = Object.values(s.commitsBySession);
  return {
    recalls: s.recalls,
    commits: counts.reduce((a, b) => a + b, 0),
    conversations: counts.filter((c) => c > 0).length,
  };
}

// Runs OpenViking's own config loader so file values, env overrides and defaults all count.
// The script prints only the keys below; the API key leaves as a boolean.
// A failure is printed as { error } rather than a stack, so the pane can say what went wrong.
export const SETTINGS_SCRIPT = `
try {
  const { loadConfig } = await import(process.env.OV_PLUGIN_SCRIPTS + '/config.mjs')
  if (typeof loadConfig !== 'function') throw new Error('its config.mjs has no loadConfig')
  const c = loadConfig(process.cwd())
  console.log(JSON.stringify({
    server: c.baseUrl || '', apiKeySet: Boolean(c.apiKey), autoRecall: c.autoRecall !== false,
    scoreThreshold: c.scoreThreshold, recallLimit: c.recallLimit, recallTokenBudget: c.recallTokenBudget,
    recallPeerScope: c.recallPeerScope, autoCapture: c.autoCapture !== false,
    commitTokenThreshold: c.commitTokenThreshold, startupInject: !c.noAutoInject,
    profileTokenBudget: c.profileTokenBudget, mcpEnabled: c.mcpEnabled !== false, error: null,
  }))
} catch (err) {
  console.log(JSON.stringify({ error: String(err && err.message || err).split('\\n')[0] }))
}
`;

// The loader's last output line, checked before it is trusted.
export function settingsFrom(stdout: string): OvSettings {
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

export const EMPTY_SETTINGS: OvSettings = {
  server: "",
  apiKeySet: false,
  autoRecall: false,
  scoreThreshold: 0,
  recallLimit: 0,
  recallTokenBudget: 0,
  recallPeerScope: "",
  autoCapture: false,
  commitTokenThreshold: 0,
  startupInject: false,
  profileTokenBudget: 0,
  mcpEnabled: false,
  error: null,
};
