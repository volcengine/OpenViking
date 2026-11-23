const MAX_AGE_MS = 30_000;
const MAX_ENTRIES = 12;
const MAX_BODY_LENGTH = 128 * 1024;

export function createPreviewBodyCache() {
  const entries = new Map<string, { body: string; storedAt: number }>();
  return {
    forFile(uri: string, updatedAt: string | undefined) {
      const key = JSON.stringify([uri, updatedAt]);
      return {
        read() {
          const entry = entries.get(key);
          if (entry && Date.now() - entry.storedAt < MAX_AGE_MS)
            return entry.body;
          entries.delete(key);
        },
        write(body: string) {
          if (body.length > MAX_BODY_LENGTH) return;
          entries.delete(key);
          if (entries.size >= MAX_ENTRIES) {
            const oldest = entries.keys().next().value;
            if (oldest !== undefined) entries.delete(oldest);
          }
          entries.set(key, { body, storedAt: Date.now() });
        },
      };
    },
  };
}

export type CachedPreviewBody = ReturnType<
  ReturnType<typeof createPreviewBodyCache>['forFile']
>;
