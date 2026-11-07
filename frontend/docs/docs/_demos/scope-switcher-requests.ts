// Demo-only API adapters. Replace these three functions with your own APIs,
// mapping the response to { id, label } and forwarding AbortSignal.
async function mockOptions(prefix: string, signal: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, 450);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });
  return Array.from({ length: 12 }, (_, index) => ({
    id: `${prefix}-${index + 1}`,
    label: `${prefix} ${index + 1}`,
  }));
}
export const listLibraries = (_parent: string, signal: AbortSignal) =>
  mockOptions('Library', signal);
export const listSpaces = (libraryId: string, signal: AbortSignal) =>
  mockOptions(`${libraryId} / Space`, signal);
export const listUsers = (scope: string, signal: AbortSignal) =>
  mockOptions(`${scope} / User`, signal);
