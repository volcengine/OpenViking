import { readManifestVersion } from "./shared/credentials.mjs";
import { buildPluginConfig } from "./shared/plugin-config.mjs";

/** The version the User-Agent reports, read from the manifest the gate checks. */
export const EXTENSION_VERSION = readManifestVersion(new URL("./package.json", import.meta.url));

export interface OVConfig {
  enabled: boolean;
  endpoint: string;
  apiKey: string;
  account: string;
  user: string;
  /** `trusted` or `api_key`; only the former puts the identity on the wire. */
  authMode: string;
  sendIdentityHeaders: boolean;
  peerId: string;
  /** The pre-git workspace id, when it differs — recall still reaches it. */
  legacyPeerId: string;
  userAgent: string;
  harness: string;
  workspacePeer: boolean;
  recallPeerScope: "actor" | "all";
  recallQueryExpansion: "auto" | "off";
  recallQueryExpansionConfigured: boolean;
  autoCapture: boolean;
  recallTokenBudget: number;
  recallMaxContentChars: number;
  recallPreferAbstract: boolean;
  recallLimit: number;
  recallLimitConfigured: boolean;
  recallLedger: boolean;
  scoreThreshold: number;
  minQueryLength: number;
  profileTokenBudget: number;
  resumeContextBudget: number;
  commitTokenThreshold: number;
  commitKeepRecentCount: number;
  takeoverEnabled: boolean;
  takeoverTokenThreshold: number;
  takeoverKeepRecentTurns: number;
  takeoverOverviewBudget: number;
  takeoverOverviewPollMs: number;
  takeoverOverviewPollMax: number;
  captureToolResults: boolean;
  captureMode: "semantic" | "keyword";
  captureMaxLength: number;
  captureToolMaxChars: number;
  captureAssistantTurns: boolean;
  /** Kept as this extension's original spelling; projected onto the shared one. */
  bypassPatterns: string[];
  bypassSession: boolean;
  bypassSessionPatterns: string[];
  logLevel: "silent" | "error" | "info";
  debugLogPath: string;
}

/**
 * Load the extension's configuration.
 *
 * The extension used to keep a `config.json` beside itself. It shipped with the
 * extension holding exactly the code defaults, so nothing could tell an
 * operator's choice from the factory setting. Every knob is declared in
 * `shared/config-schema.mjs` now and resolved from the same layers as every
 * other harness: env → the workspace file → `ovcli.conf`'s `plugin.pi` →
 * `ovcli.conf`'s `plugin` → defaults.
 */
export function loadConfig(cwd: string = process.cwd()): OVConfig {
  const config = buildPluginConfig("pi", { cwd, version: EXTENSION_VERSION, deriveEffectivePeer: true });

  return {
    ...config,
    // `bypassSessionPatterns` is the name the shared matcher reads and every
    // other harness spells; `bypassPatterns` was this extension's own and is
    // still accepted. Both hold the same list so either can be inspected.
    bypassPatterns: config.bypassSessionPatterns,
    // OPENVIKING_DEBUG_LOG is the shared spelling and lands in the schema;
    // OV_DEBUG_LOG is pi's older name, kept working so existing setups log.
    debugLogPath: config.debugLogPath || String(process.env.OV_DEBUG_LOG || "").trim(),
    // The whole resolution, not just the id: `legacyPeerId` is what lets recall
    // under `actor` scope still reach memories written before the git-derived
    // peer replaced the path-derived one.
    peerId: config.effectivePeer.peerId,
  } as OVConfig;
}
