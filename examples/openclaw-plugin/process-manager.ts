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

export async function quickHealthCheck(
  client: OpenVikingClient,
  timeoutMs: number,
): Promise<boolean> {
  try {
    await client.healthCheck(timeoutMs);
    return true;
  } catch {
    return false;
  }
}

export async function quickRecallPrecheck(
  client: OpenVikingClient,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  const healthOk = await quickHealthCheck(client, 500);
  if (healthOk) {
    return { ok: true };
  }
  return { ok: false, reason: "health check failed" };
}
