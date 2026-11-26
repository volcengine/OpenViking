export type ServerFeature = "working_memory_opt_in";

export const SERVER_FEATURES: Readonly<Record<ServerFeature, string>>;

export function requiredVersion(feature: ServerFeature): string;
export function supports(feature: ServerFeature, version: string | null | undefined): boolean;

export type FeatureStatus = {
  feature: ServerFeature;
  supported: boolean;
  required: string;
  actual: string | null;
};
export function featureStatus(feature: ServerFeature, version: string | null | undefined): FeatureStatus;

export function healthVersion(
  fetchJSON: (path: string, init?: RequestInit, options?: { timeoutMs?: number }) => Promise<any>,
  options?: { timeoutMs?: number },
): Promise<string | null>;
