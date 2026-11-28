export const SEAL_TIMEOUT_MS: number;
export const AGENT_TOOLS: ReadonlySet<string>;
export const SESSION_TEXT: string;

// The shared core's types, declared here: Claude Code's copies of the shared
// modules carry no declarations.
export type SealReason = "seal_failed" | "server_too_old" | "version_unknown";
interface SealError extends Error {
  status?: number;
  reason: SealReason;
}
export type SealSupport = { supported: boolean; required?: string; actual?: string | null };
export type SealPolicy = {
  unavailable(): Promise<SealError | null>;
  guard(post: SealPost): SealPost;
};
export function createSealPolicy(options?: {
  check?: () => SealSupport | SealError | Promise<SealSupport | SealError>;
}): SealPolicy;

export type SealPost = (path: string, body: unknown) => Promise<{ status: number; json: unknown }>;

export type SealServer = { baseUrl: string; headers: Record<string, string> };

export type SealSetting =
  | { sealing: "on"; enrollToolSecrets?: boolean; server?: SealServer; support?: SealSupport }
  | { sealing: "off" };

export type SealingMode = "on" | "off" | "unknown";

export type SealDecision =
  | { kind: "pass" }
  | { kind: "rewrite"; text: string; context: string[] }
  | { kind: "drop"; reason: string };

export function sealNote(references: readonly string[]): string;
export function withheld(reason?: string): string;
export function withheldAttachment(why: string): string;
export function sealFailureMessage(why: unknown): string;
export function unsealFailureMessage(why: unknown): string;
export function settingUnknown(): SealError;
export function envTurnsOff(envOverride: string | undefined): boolean;
export function sealingMode(setting: SealSetting | undefined, envOverride: string | undefined): SealingMode;
export function isSealable(prompt: { text: string; command?: unknown; isMeta?: unknown }): boolean;
export function serverPost(
  fetch: (url: string, init: { method: string; headers: Record<string, string>; body: string }) => Promise<{ status: number; text: string }>,
  server: SealServer | undefined,
  options?: { timeoutMs?: number },
): SealPost;
export function sealPrompt(
  prompt: { text: string; command?: unknown; isMeta?: unknown },
  options: { post: SealPost },
): Promise<SealDecision>;
export function unsealCall<T extends { tool: string }>(
  post: SealPost,
  input: T,
): Promise<{ call: T; replaced: number; deny?: undefined } | { deny: string; call?: undefined; replaced?: undefined }>;
export function sealToolResult<T>(
  post: SealPost,
  outcome: T,
  options?: { enroll?: boolean },
): Promise<T | { result: unknown; context?: string[] } | { deny: string }>;
export function sealAttachment(post: SealPost, text: string): Promise<string>;
