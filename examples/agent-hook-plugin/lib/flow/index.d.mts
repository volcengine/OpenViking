import type { Host, Outcome, Output } from "./host.d.mts";

export type { EventContext, EventRead, Host, Outcome, Output, Stage, Turn } from "./host.d.mts";

export function stageOf(host: Host, event: string): string;
export function isWriteStage(stage: string): boolean;
export function mayDetach(host: Host, event: string, cfg: Record<string, any>): boolean | "always";
export function runStage(
  host: Host,
  event: string,
  payload?: Record<string, any>,
  options?: { detached?: boolean; cfg?: Record<string, any>; outcome?: Outcome },
): Promise<Output>;
export function toolCall(host: Host, payload: Record<string, any>, event?: string): Output;
