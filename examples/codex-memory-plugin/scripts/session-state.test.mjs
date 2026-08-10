import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, readlink, rename, rm, writeFile } from "node:fs/promises";
import { hostname, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import {
  clearState,
  listStates,
  loadState,
  saveState,
  withStateTransaction,
} from "./session-state.mjs";

const execFileAsync = promisify(execFile);
const STATE_MODULE_URL = new URL("./session-state.mjs", import.meta.url).href;

async function useTemporaryStateDir(t) {
  const previous = process.env.OPENVIKING_CODEX_STATE_DIR;
  const dir = await mkdtemp(join(tmpdir(), "openviking-codex-state-"));
  process.env.OPENVIKING_CODEX_STATE_DIR = dir;
  t.after(async () => {
    if (previous === undefined) delete process.env.OPENVIKING_CODEX_STATE_DIR;
    else process.env.OPENVIKING_CODEX_STATE_DIR = previous;
    await rm(dir, { recursive: true, force: true });
  });
  return dir;
}

async function waitForFile(path, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await readFile(path);
      return;
    } catch { /* worker has not acquired the lock yet */ }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${path}`);
}

function transactionWorkerSource() {
  return `
    import { writeFile } from "node:fs/promises";
    import { withStateTransaction } from ${JSON.stringify(STATE_MODULE_URL)};
    await withStateTransaction(process.env.SESSION_ID, async ({ state, save }) => {
      if (process.env.MARKER) await writeFile(process.env.MARKER, "locked");
      const observed = state.capturedTurnCount;
      await new Promise((resolve) => setTimeout(resolve, Number(process.env.DELAY_MS || 0)));
      state.capturedTurnCount = observed + 1;
      await save();
    });
  `;
}

async function installLockOwner(dir, sessionId, owner) {
  const token = owner.token;
  const root = join(dir, `${sessionId}.json.lock`);
  await mkdir(root);
  await writeFile(join(root, "available"), "");
  await writeFile(join(root, `claim-${token}.json`), JSON.stringify(owner));
  await rename(join(root, "available"), join(root, `owner-${token}`));
  return root;
}

async function lockEntries(path) {
  return (await readdir(path)).sort();
}

async function currentLinuxLockIdentity() {
  if (process.platform !== "linux") return {};
  let machineId = "";
  for (const path of ["/etc/machine-id", "/var/lib/dbus/machine-id"]) {
    try {
      machineId = (await readFile(path, "utf-8")).trim();
      if (machineId) break;
    } catch { /* try the next machine-id location */ }
  }
  const rawStat = await readFile("/proc/self/stat", "utf-8");
  const namespacePid = rawStat.slice(0, rawStat.indexOf(" "));
  const startTime = rawStat.slice(rawStat.lastIndexOf(")") + 1).trim().split(/\s+/)[19];
  const bootId = (await readFile("/proc/sys/kernel/random/boot_id", "utf-8")).trim();
  return {
    machineIdentity: machineId ? `linux:${machineId}` : null,
    pidNamespaceIdentity: await readlink("/proc/self/ns/pid"),
    bootId,
    processStartIdentity: `linux:${bootId}:${namespacePid}:${startTime}`,
  };
}

test("serializes same-session transactions across Node processes", async (t) => {
  const dir = await useTemporaryStateDir(t);
  const marker = join(dir, "first.locked");
  const baseEnv = {
    ...process.env,
    OPENVIKING_CODEX_STATE_DIR: dir,
    SESSION_ID: "shared-session",
  };

  const first = execFileAsync(process.execPath, ["--input-type=module", "-e", transactionWorkerSource()], {
    env: { ...baseEnv, MARKER: marker, DELAY_MS: "150" },
  });
  await waitForFile(marker);
  const second = execFileAsync(process.execPath, ["--input-type=module", "-e", transactionWorkerSource()], {
    env: { ...baseEnv, DELAY_MS: "0" },
  });
  await Promise.all([first, second]);

  const state = await loadState("shared-session");
  assert.equal(state.capturedTurnCount, 2);
  assert.equal(state.revision, 2);
  assert.deepEqual((await readdir(dir)).filter((name) => name.includes(".tmp")), []);
  assert.deepEqual(await lockEntries(join(dir, "shared-session.json.lock")), ["available", "revision"]);
});

test("clear waits for an in-flight transaction and cannot be undone by a stale save", async (t) => {
  const dir = await useTemporaryStateDir(t);
  const marker = join(dir, "writer.locked");
  const writer = execFileAsync(process.execPath, ["--input-type=module", "-e", transactionWorkerSource()], {
    env: {
      ...process.env,
      OPENVIKING_CODEX_STATE_DIR: dir,
      SESSION_ID: "clear-race",
      MARKER: marker,
      DELAY_MS: "150",
    },
  });
  await waitForFile(marker);

  const clearing = clearState("clear-race");
  await Promise.all([writer, clearing]);
  assert.equal((await loadState("clear-race")).capturedTurnCount, 0);
  assert.equal((await readdir(dir)).includes("clear-race.json"), false);
  assert.deepEqual(await lockEntries(join(dir, "clear-race.json.lock")), ["available", "revision"]);
});

test("recovers only the newest complete session-matching temp file", async (t) => {
  const dir = await useTemporaryStateDir(t);
  await writeFile(join(dir, "recover.json"), JSON.stringify({
    codexSessionId: "recover",
    capturedTurnCount: 1,
    revision: 1,
    lastUpdatedAt: 500,
  }));
  await writeFile(join(dir, "recover.json.tmp"), JSON.stringify({
    codexSessionId: "recover",
    capturedTurnCount: 3,
    revision: 1,
    lastUpdatedAt: 1_000,
  }));
  await writeFile(join(dir, "recover.json.tmp-123-complete"), JSON.stringify({
    codexSessionId: "recover",
    capturedTurnCount: 7,
    revision: 2,
    lastUpdatedAt: 200,
  }));
  await writeFile(join(dir, "recover.json.tmp-123-wrong-session"), JSON.stringify({
    codexSessionId: "other",
    capturedTurnCount: 99,
    lastUpdatedAt: 300,
  }));
  await writeFile(join(dir, "recover.json.tmp-123-truncated"), "{");

  const recovered = await loadState("recover");
  assert.equal(recovered.capturedTurnCount, 7);
  assert.equal(JSON.parse(await readFile(join(dir, "recover.json"), "utf-8")).capturedTurnCount, 7);
  assert.deepEqual((await readdir(dir)).filter((name) => name.startsWith("recover.json.tmp")), []);
});

test("listStates recovers a complete temp even when no final file exists", async (t) => {
  const dir = await useTemporaryStateDir(t);
  await writeFile(join(dir, "orphan.json.tmp-456-complete"), JSON.stringify({
    codexSessionId: "orphan",
    ovSessionId: "cx-orphan",
    capturedTurnCount: 4,
    lastUpdatedAt: Date.now() - 60_000,
  }));
  await writeFile(join(dir, "broken.json.tmp-456-incomplete"), "{");

  const states = await listStates();
  assert.deepEqual(states.map((state) => state.codexSessionId), ["orphan"]);
  assert.equal(states[0].capturedTurnCount, 4);
  assert.equal(JSON.parse(await readFile(join(dir, "orphan.json"), "utf-8")).ovSessionId, "cx-orphan");
  assert.equal((await readdir(dir)).some((name) => name.startsWith("orphan.json.tmp")), false);
});

test("listStates quickly omits a busy session instead of consuming the hook deadline", async (t) => {
  const dir = await useTemporaryStateDir(t);
  await writeFile(join(dir, "busy-scan.json"), JSON.stringify({
    codexSessionId: "busy-scan",
    capturedTurnCount: 4,
    revision: 1,
    lastUpdatedAt: Date.now(),
  }));
  const path = await installLockOwner(dir, "busy-scan", {
    token: "live-scan-owner",
    pid: process.pid,
    hostname: hostname(),
    // A legacy/missing identity is deliberately non-reclaimable on Linux.
    processStartIdentity: null,
    acquiredAt: Date.now(),
  });

  const startedAt = Date.now();
  const states = await listStates();
  const elapsedMs = Date.now() - startedAt;

  assert.deepEqual(states, []);
  assert.ok(elapsedMs < 1_500, `busy scan took ${elapsedMs}ms`);
  assert.ok((await readdir(path)).includes("owner-live-scan-owner"));
});

test("reclaims a lock whose same-Linux-PID-namespace owner process is gone", async (t) => {
  if (process.platform !== "linux") {
    t.skip("automatic dead-owner recovery is Linux-specific");
    return;
  }
  const dir = await useTemporaryStateDir(t);
  const linuxIdentity = await currentLinuxLockIdentity();
  const path = await installLockOwner(dir, "stale", {
    token: "abandoned",
    pid: 2_147_483_647,
    hostname: hostname(),
    machineIdentity: linuxIdentity.machineIdentity,
    pidNamespaceIdentity: linuxIdentity.pidNamespaceIdentity,
    processStartIdentity: process.platform === "linux"
      ? `linux:${linuxIdentity.bootId}:2147483647:1`
      : null,
    acquiredAt: Date.now(),
  });

  await saveState({ codexSessionId: "stale", capturedTurnCount: 1 });
  assert.equal((await loadState("stale")).capturedTurnCount, 1);
  assert.deepEqual(await lockEntries(path), ["available", "revision"]);
});

test("does not steal a live lock and times out with a stable error code", async (t) => {
  const dir = await useTemporaryStateDir(t);
  const path = await installLockOwner(dir, "live", {
    token: "live-owner",
    pid: process.pid,
    hostname: hostname(),
    processStartIdentity: null,
    acquiredAt: Date.now(),
  });

  await assert.rejects(
    withStateTransaction("live", async () => {}, {
      lockTimeoutMs: 30,
      retryDelayMs: 5,
    }),
    (error) => error?.code === "OPENVIKING_STATE_LOCK_TIMEOUT",
  );
  assert.ok((await readdir(path)).includes("owner-live-owner"));
});

test("uses process start identity to reclaim a reused Linux PID safely", async (t) => {
  if (process.platform !== "linux") {
    t.skip("automatic dead-owner recovery is Linux-specific");
    return;
  }
  const dir = await useTemporaryStateDir(t);
  const linuxIdentity = await currentLinuxLockIdentity();
  const path = await installLockOwner(dir, "pid-reuse", {
    token: "old-process",
    pid: process.pid,
    hostname: hostname(),
    machineIdentity: linuxIdentity.machineIdentity,
    pidNamespaceIdentity: linuxIdentity.pidNamespaceIdentity,
    processStartIdentity: "linux:not-the-current-boot:1:1",
    acquiredAt: Date.now(),
  });

  await withStateTransaction("pid-reuse", async ({ state, save }) => {
    state.capturedTurnCount = 1;
    await save();
  });
  assert.equal((await loadState("pid-reuse")).capturedTurnCount, 1);
  assert.deepEqual(await lockEntries(path), ["available", "revision"]);
});

test("never auto-reclaims a Linux owner from a missing or different PID namespace", async (t) => {
  if (process.platform !== "linux") {
    t.skip("PID namespace identity is Linux-specific");
    return;
  }
  const dir = await useTemporaryStateDir(t);
  const linuxIdentity = await currentLinuxLockIdentity();
  for (const [sessionId, pidNamespaceIdentity] of [
    ["legacy-namespace", null],
    ["different-namespace", "pid:[different-namespace]"],
  ]) {
    const path = await installLockOwner(dir, sessionId, {
      token: "unfenced-owner",
      pid: 2_147_483_647,
      hostname: hostname(),
      machineIdentity: linuxIdentity.machineIdentity,
      pidNamespaceIdentity,
      processStartIdentity: `linux:${linuxIdentity.bootId}:2147483647:1`,
      acquiredAt: 1,
    });

    await assert.rejects(
      withStateTransaction(sessionId, async () => {}, {
        lockTimeoutMs: 30,
        retryDelayMs: 5,
      }),
      (error) => error?.code === "OPENVIKING_STATE_LOCK_TIMEOUT",
    );
    assert.ok((await readdir(path)).includes("owner-unfenced-owner"));
  }
});

test("never timeout-reclaims another host's owner without server fencing", async (t) => {
  const dir = await useTemporaryStateDir(t);
  const path = await installLockOwner(dir, "remote", {
    token: "remote-owner",
    pid: 2_147_483_647,
    hostname: "another-host.example",
    processStartIdentity: "1",
    acquiredAt: 1,
  });

  await assert.rejects(
    withStateTransaction("remote", async () => {}, { lockTimeoutMs: 30, retryDelayMs: 5 }),
    (error) => error?.code === "OPENVIKING_STATE_LOCK_TIMEOUT",
  );
  assert.ok((await readdir(path)).includes("owner-remote-owner"));
});

test("prunes only positively-dead ownerless claims after acquiring the baton", async (t) => {
  if (process.platform !== "linux") {
    t.skip("automatic dead-claim cleanup is Linux-specific");
    return;
  }
  const dir = await useTemporaryStateDir(t);
  const sessionId = "orphan-claims";
  await loadState(sessionId);
  const path = join(dir, `${sessionId}.json.lock`);
  const linuxIdentity = await currentLinuxLockIdentity();
  await writeFile(join(path, "claim-dead-contender.json"), JSON.stringify({
    token: "dead-contender",
    hostname: hostname(),
    machineIdentity: linuxIdentity.machineIdentity,
    pidNamespaceIdentity: linuxIdentity.pidNamespaceIdentity,
    processStartIdentity: `linux:${linuxIdentity.bootId}:2147483647:1`,
  }));
  await writeFile(join(path, "claim-live-contender.json"), JSON.stringify({
    token: "live-contender",
    hostname: hostname(),
    machineIdentity: linuxIdentity.machineIdentity,
    pidNamespaceIdentity: linuxIdentity.pidNamespaceIdentity,
    processStartIdentity: linuxIdentity.processStartIdentity,
  }));
  await writeFile(join(path, "claim-remote-contender.json"), JSON.stringify({
    token: "remote-contender",
    hostname: "another-host.example",
    machineIdentity: linuxIdentity.machineIdentity,
    pidNamespaceIdentity: linuxIdentity.pidNamespaceIdentity,
    processStartIdentity: `linux:${linuxIdentity.bootId}:2147483647:1`,
  }));

  await loadState(sessionId);
  const files = await lockEntries(path);
  assert.equal(files.includes("claim-dead-contender.json"), false);
  assert.equal(files.includes("claim-live-contender.json"), true);
  assert.equal(files.includes("claim-remote-contender.json"), true);
});

test("standalone save rejects a stale revision instead of overwriting newer state", async (t) => {
  await useTemporaryStateDir(t);
  const stale = await loadState("cas");
  await saveState({ ...stale, capturedTurnCount: 1 });

  await assert.rejects(
    saveState({ ...stale, capturedTurnCount: 99 }),
    (error) => error?.code === "OPENVIKING_STALE_STATE_SAVE",
  );
  const current = await loadState("cas");
  assert.equal(current.capturedTurnCount, 1);
  assert.equal(current.revision, 1);
});

test("a transaction cannot write another session while holding the wrong lock", async (t) => {
  const dir = await useTemporaryStateDir(t);
  await assert.rejects(
    withStateTransaction("session-a", async ({ save }) => save({
      codexSessionId: "session-b",
      capturedTurnCount: 99,
    })),
    (error) => error?.code === "OPENVIKING_STATE_SESSION_MISMATCH",
  );
  assert.equal((await readdir(dir)).includes("session-b.json"), false);
  assert.equal((await loadState("session-a")).capturedTurnCount, 0);
});

test("revision remains monotonic across clear and state recreation", async (t) => {
  await useTemporaryStateDir(t);
  const initial = await loadState("generation");
  const first = await saveState({ ...initial, capturedTurnCount: 1 });
  assert.equal(first.revision, 1);

  await clearState("generation");
  const cleared = await loadState("generation");
  assert.equal(cleared.revision, 2);
  assert.equal(cleared.capturedTurnCount, 0);

  const recreated = await saveState({ ...cleared, capturedTurnCount: 1 });
  assert.equal(recreated.revision, 3);
});

test("a persisted clear tombstone suppresses an old final after a crash", async (t) => {
  const dir = await useTemporaryStateDir(t);
  await loadState("tombstone"); // initializes the permanent lock root
  await writeFile(join(dir, "tombstone.json"), JSON.stringify({
    codexSessionId: "tombstone",
    capturedTurnCount: 8,
    revision: 4,
    lastUpdatedAt: Date.now(),
  }));
  await writeFile(join(dir, "tombstone.json.lock", "revision"), "5");

  const state = await loadState("tombstone");
  assert.equal(state.revision, 5);
  assert.equal(state.capturedTurnCount, 0);
  assert.equal((await readdir(dir)).includes("tombstone.json"), false);
});

test("different session ids remain independently concurrent", async (t) => {
  await useTemporaryStateDir(t);
  let releaseFirst;
  let signalFirst;
  const firstEntered = new Promise((resolve) => { signalFirst = resolve; });
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const first = withStateTransaction("session-a", async () => {
    signalFirst();
    await firstGate;
  });
  await firstEntered;

  let secondEntered = false;
  await withStateTransaction("session-b", async () => { secondEntered = true; });
  assert.equal(secondEntered, true);
  releaseFirst();
  await first;
});
