import { Socket } from "node:net";

type ChildLike = {
  killed?: boolean;
  exitCode?: number | null;
  signalCode?: string | null;
  off?: (event: string, listener: (...args: any[]) => void) => void;
  once: (event: string, listener: (...args: any[]) => void) => void;
};

export function waitForHealth(baseUrl: string, timeoutMs: number, intervalMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (Date.now() > deadline) {
        reject(new Error(`OpenViking health check timeout at ${baseUrl}`));
        return;
      }
      fetch(`${baseUrl}/health`)
        .then((r) => r.json())
        .then((body: { status?: string }) => {
          if (body?.status === "ok") {
            resolve();
            return;
          }
          setTimeout(tick, intervalMs);
        })
        .catch(() => setTimeout(tick, intervalMs));
    };
    tick();
  });
}

export function waitForHealthOrExit(
  baseUrl: string,
  timeoutMs: number,
  intervalMs: number,
  child: ChildLike,
): Promise<void> {
  const exited =
    child.killed || child.exitCode !== null || child.signalCode !== null;
  if (exited) {
    return Promise.reject(
      new Error(
        `OpenViking subprocess exited before health check ` +
          `(code=${child.exitCode}, signal=${child.signalCode})`,
      ),
    );
  }

  return new Promise((resolve, reject) => {
    let settled = false;

    const cleanup = () => {
      child.off?.("error", onError);
      child.off?.("exit", onExit);
    };

    const finishResolve = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolve();
    };

    const finishReject = (err: unknown) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(err instanceof Error ? err : new Error(String(err)));
    };

    const onError = (err: Error) => {
      finishReject(err);
    };

    const onExit = (code: number | null, signal: string | null) => {
      finishReject(
        new Error(
          `OpenViking subprocess exited before health check ` +
            `(code=${code}, signal=${signal})`,
        ),
      );
    };

    child.once("error", onError);
    child.once("exit", onExit);
    waitForHealth(baseUrl, timeoutMs, intervalMs).then(finishResolve, finishReject);
  });
}

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

export function quickTcpProbe(host: string, port: number, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new Socket();
    let done = false;
    const finish = (ok: boolean) => {
      if (done) {
        return;
      }
      done = true;
      socket.destroy();
      resolve(ok);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    try {
      socket.connect(port, host);
    } catch {
      finish(false);
    }
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

export interface ProcessLogger {
  info?: (msg: string) => void;
  warn?: (msg: string) => void;
}
