export function extractBranchCapturePayloads(
  branch: any[],
  syncedEntryCount?: number,
  cfg?: Record<string, any>,
): {
  payloads: any[];
  nextEntryCount: number;
  observedEntryCount: number;
  resetWatermark: boolean;
};
