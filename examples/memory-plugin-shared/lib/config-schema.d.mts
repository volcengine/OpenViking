export interface Knob {
  name: string;
  type: "bool" | "int" | "number" | "enum" | "string" | "template" | "list";
  default: any;
  harness?: Record<string, any>;
  min?: number;
  max?: number;
  values?: string[];
  env?: string;
  aliases?: string[];
  workspace?: string;
  sendOnlyWhenConfigured?: boolean;
  capability: string;
}

export const CAPABILITIES: string[];
export const KNOBS: readonly Knob[];
export const KNOB_BY_NAME: Map<string, Knob>;
export const KNOB_BY_KEY: Map<string, Knob>;
export const WORKSPACE_KNOB_MAP: Record<string, string>;
export const WORKSPACE_ENUMS: Record<string, string[] | null>;
export const WORKSPACE_RANGES: Record<string, { min: number; max: number; integer: boolean }>;
export const HARNESS_KEYS: Record<string, string>;
export const HARNESS_CONFIG_KEYS: Set<string>;

export function pluginConfigKeys(): Set<string>;
export function harnessKey(harness: string): string;
export function knobDefault(knob: Knob, harness?: string): any;
export function coerceKnobValue(knob: Knob, raw: unknown, fallback: any): any;
export function resolveKnobs(options?: {
  harness?: string;
  layers?: Array<{ name?: string; data?: Record<string, any> }>;
  env?: Record<string, string | undefined>;
}): { settings: Record<string, any>; configured: Set<string>; sources: Record<string, string> };
