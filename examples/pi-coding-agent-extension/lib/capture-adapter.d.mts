export function extractBranchCapturePayloads(
  branch: any[],
  syncedEntryCount?: number,
  cfg?: Record<string, any>,
  syncedEntryId?: string,
): {
  payloads: any[];
  payloadEntryIds: (string | null)[];
  nextEntryCount: number;
  nextEntryId: string;
  observedEntryCount: number;
  resetWatermark: boolean;
};
