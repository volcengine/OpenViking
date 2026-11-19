import type { OpenVikingClient } from "./client.js";

export function withTimeout<T>(promise: Promise<T>, timeoutMs: number, timeoutMessage: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

export async function quickRecallPrecheck(
  client: OpenVikingClient,
  agentId?: string,
): Promise<{ ok: true; serverVersion?: string } | { ok: false; reason: string }> {
  try {
    const health = await client.healthCheck(500, agentId);
    return { ok: true, serverVersion: health?.version };
  } catch {
    return { ok: false, reason: "health check failed" };
  }
}
