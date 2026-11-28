import type { Host } from "./shared/flow/host.d.mts";

export const SESSION_PREFIX: "pi-";
export const HISTORY_TOOL: "openviking_session_history";
export const VIKING_URI_TOOL_HINTS: Record<string, { tool: string; example: (uri: string, input?: any) => string }>;
export function readPiToolEvent(event: any): { toolName: string; input: Record<string, any> };
export function ovSessionIdFor(piSessionId: string): string;
declare const pi: Host;
export default pi;
