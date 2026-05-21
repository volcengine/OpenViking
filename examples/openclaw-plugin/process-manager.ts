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

export async function quickHealthCheck(baseUrl: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}/health`, {
      method: "GET",
      signal: controller.signal,
    });
    if (!response.ok) {
      return false;
    }
    const body = (await response.json().catch(() => ({}))) as { status?: string };
    return body.status === "ok";
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function quickRecallPrecheck(
  baseUrl: string,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  const healthOk = await quickHealthCheck(baseUrl, 500);
  if (healthOk) {
    return { ok: true };
  }
  return { ok: false, reason: "health check failed" };
}
