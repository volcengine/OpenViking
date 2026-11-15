/**
 * The reader's file identity across processes.
 *
 * The fingerprint window must be a fixed length carried in the persisted state.
 * If it were re-derived from the file's current size, every append to a small
 * wire would change the hash and look like a replacement.
 */

import assert from "node:assert/strict";
import { appendFile, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { captureSession, ovSessionIdFor } from "../../scripts/capture-session.mjs";
import { loadConfig } from "../../scripts/config.mjs";
import { makeAgentFetchJSON } from "../../scripts/shared/agent-hook-runtime.mjs";
import { emptyReaderState, readWireRecords } from "../../scripts/wire-reader.mjs";
import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import { metadata, simpleTurn, wireText } from "../fixtures/wire-fixtures.mjs";

const SESSION = "session_prefix";

test("a small wire that grows is not mistaken for a replacement", async () => {
  const dir = await mkdtemp(join(tmpdir(), "kc-prefix-"));
  const file = join(dir, "wire.jsonl");
  try {
    // Small enough that the whole file is inside the fingerprint window.
    await writeFile(file, wireText([metadata(), ...simpleTurn(0)]), "utf8");
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(first.status, "ok");
    // The window is the file's size on the first read, and it stays that size
    // afterwards, which is what makes a later append comparable.
    assert.equal(first.state.prefixLength, first.state.fileSize);

    // The state a hook would persist and reload.
    const persisted = {
      ...emptyReaderState({ wirePath: file }),
      scanOffset: first.state.scanOffset,
      line: first.state.line,
      fileIdentity: first.state.fileIdentity,
      prefixHash: first.state.prefixHash,
      prefixLength: first.state.prefixLength,
    };

    // Append one more turn: the file grows, the window does not.
    await appendFile(file, wireText(simpleTurn(1)), "utf8");
    const second = await readWireRecords(file, persisted);
    assert.equal(second.status, "ok");
    assert.equal(second.reset, undefined, "growth is not a replacement");
    assert.equal(second.records.length > 0, true);
    assert.equal(second.records.every((entry) => entry.line > first.state.line), true);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("a capture pass persists the fingerprint window, so the next pass does not reset", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const { sessionDir } = await writeSession(home, { sessionId: SESSION, wireTextFor: () => wireText([metadata()]) });
      const wirePath = join(sessionDir, "agents", "main", "wire.jsonl");
      const run = async (state) => {
        const cfg = loadConfig(home, { env: { ...process.env, ...fakeOvEnv(server), KIMI_CODE_HOME: home } });
        const { fetchJSON } = makeAgentFetchJSON(cfg, home, { defaultTimeoutMs: 5000 });
        return captureSession({
          cfg, input: {}, cwd: home, log: () => {}, logError: () => {},
          deadline: Date.now() + 60000, fetchJSON, sessionId: SESSION, state, mode: "normal", now: Date.now(),
        });
      };

      await appendFile(wirePath, wireText(simpleTurn(0)), "utf8");
      const first = await run({ agents: {}, startSource: "startup" });
      assert.ok(first.state.agents.main.prefixLength, "the window length is part of the state");
      assert.equal(first.state.agents.main.resets || 0, 0);

      await appendFile(wirePath, wireText(simpleTurn(1)), "utf8");
      const second = await run(first.state);
      assert.equal(second.state.agents.main.resets || 0, 0, "appending must not count as a reset");
      assert.equal(server.messages(ovSessionIdFor(SESSION, "main")).length, 4);
    });
  } finally {
    await server.close();
  }
});

test("a genuine replacement is still detected with the window persisted", async () => {
  const dir = await mkdtemp(join(tmpdir(), "kc-prefix-2-"));
  const file = join(dir, "wire.jsonl");
  try {
    await writeFile(file, wireText([metadata(), ...simpleTurn(0)]), "utf8");
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    const persisted = { ...first.state };

    // Different first bytes, same length class: a different file.
    await writeFile(file, wireText([metadata({ created_at: 1 }), ...simpleTurn(9)]), "utf8");
    const second = await readWireRecords(file, persisted);
    assert.equal(second.reset, true);
    const original = await readFile(file, "utf8");
    assert.match(original, /"turnId":9/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
