export const SEAL_PATH: "/api/v1/privacy-configs/seal";
export const RESOLVE_PATH: "/api/v1/privacy-configs/resolve";
export const MAX_TEXTS: number;
export const BATCH_BYTES: number;
export const MAX_TEXT_BYTES: number;
export const MIN_LENGTH: number;
/** The session text, given to the model while sealing is on. */
export const SESSION_TEXT: string;

/** The one transport the core uses; rejects when no answer arrived. */
export type SealPost = (path: string, body: unknown) => Promise<{ status: number; json: unknown }>;

export type SealSpan = { start: number; end: number; reference: string; temporary: boolean };

export type SealedText = {
  /** The text to use: the rewritten one, or the original when nothing was replaced. */
  text: string;
  changed: boolean;
  /** Distinct temporary references in this text, in order of first appearance. */
  temporary: string[];
  spans: SealSpan[];
};

/** Why sealing cannot be done. */
export type SealReason = "seal_failed" | "server_too_old" | "version_unknown";

export class SealError extends Error {
  constructor(message: string, options?: { status?: number; reason?: SealReason });
  status?: number;
  reason: SealReason;
}

/** What the plugin knows of the server's support for sealing. */
export type SealSupport = { supported: boolean; required?: string; actual?: string | null };

export type SealPolicy = {
  /** The SealError of a server without the capability, or null; asked until it is null. */
  unavailable(): Promise<SealError | null>;
  /** `post` behind `unavailable()`: no call while the server cannot seal. */
  guard(post: SealPost): SealPost;
};

export function createSealPolicy(options?: {
  check?: () => SealSupport | SealError | Promise<SealSupport | SealError>;
}): SealPolicy;
export function whyNotSealed(error: unknown): string;
/** What the user can do about a SealError: upgrade or reach the server, or turn sealing off. */
export function stopHint(error: unknown): string;

export function sealTexts(
  post: SealPost,
  items: readonly { text: string; enroll?: boolean }[],
): Promise<{ texts: SealedText[]; temporary: string[] }>;

export function sealValue<T>(
  post: SealPost,
  value: T,
  options?: { enroll?: boolean },
): Promise<{ value: T; temporary: string[]; sealed: number }>;

export function unsealValue<T>(
  post: SealPost,
  value: T,
  options?: { skip?: boolean },
): Promise<{ value: T; replaced: number }>;

export function sealNote(references: readonly string[]): string;
export function withheld(reason?: string): string;
export function isOpenVikingTool(name: unknown, ownNames?: readonly string[]): boolean;
export function containsOvPrivacy(text: unknown): boolean;
export function isCanonicalReference(text: unknown): boolean;
