export async function contentDigest(content: string): Promise<string> {
  const bytes = new TextEncoder().encode(content);
  const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}
