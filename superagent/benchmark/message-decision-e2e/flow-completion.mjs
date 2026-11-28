const MINUTE_MS = 60_000;
const DEFAULT_IDLE_MS = 5 * MINUTE_MS;
const DEFAULT_POLL_MS = 1_000;

export async function waitForFlowCompletion({
  eventIds,
  readDecisionIds,
  readLastOutboxAtMs,
  injectedAtMs,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  log = console.log,
  idleMs = DEFAULT_IDLE_MS,
  pollIntervalMs = DEFAULT_POLL_MS,
}) {
  const expected = new Set(eventIds);
  let activityAtMs = null;
  let loggedMinute = 0;

  while (true) {
    const decisions = readDecisionIds();
    const decided = [...expected].filter((eventId) => decisions.has(eventId)).length;
    if (decided === expected.size) {
      return { reason: 'all-decided', decided, expected: expected.size };
    }

    const lastOutboxAtMs = readLastOutboxAtMs();
    const latestActivityAtMs = Math.max(injectedAtMs, lastOutboxAtMs ?? injectedAtMs);
    if (latestActivityAtMs !== activityAtMs) {
      activityAtMs = latestActivityAtMs;
      loggedMinute = 0;
    }

    const elapsedMs = Math.max(0, now() - activityAtMs);
    const elapsedMinutes = Math.min(Math.floor(elapsedMs / MINUTE_MS), Math.ceil(idleMs / MINUTE_MS));
    while (loggedMinute < elapsedMinutes) {
      loggedMinute += 1;
      log(`decision wait: ${decided}/${expected.size} decided, no new outbox for ${loggedMinute}/${Math.ceil(idleMs / MINUTE_MS)} minutes`);
    }
    if (elapsedMs >= idleMs) {
      return { reason: 'outbox-idle', decided, expected: expected.size, idleMs: elapsedMs };
    }

    await sleep(Math.min(pollIntervalMs, idleMs - elapsedMs));
  }
}
