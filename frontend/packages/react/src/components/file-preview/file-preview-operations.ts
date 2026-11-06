export async function performPreviewOperation(
  execute: (() => Promise<void>) | undefined,
  notify: (() => void) | undefined,
) {
  if (!execute) return;
  try {
    await execute();
  } catch {
    return;
  }
  notify?.();
}
