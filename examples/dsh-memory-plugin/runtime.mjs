import { createHash } from "node:crypto";
import { join } from "node:path";
import { stateDir } from "./shared/workspace-identity.mjs";
import { createRecallCompressor } from "./compressor.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { buildRecallBlock, isRecallEnabled } from "./shared/recall-core.mjs";
import { formatSessionHistoryPointer } from "./shared/session-history-pointer.mjs";
import {
  commitSession,
  enqueue,
  enqueueCommitAfterWrites,
  listPending,
  replayPending,
} from "./shared/pending-queue.mjs";
import { isRetryableFailure } from "./shared/retryable.mjs";
import { commitsOnCompaction, isBypassed, thresholdCommitDue } from "./shared/session-model.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";
import {
  captureEvent,
  isOpenVikingPluginMessage,
  pluginMessage,
  promptText,
} from "./capture.mjs";
import { deriveDshSessionId, historySessionIds } from "./session-id.mjs";

export class OpenVikingRuntime {
  constructor(client, config, logger = console, resolveSessionConfig = null) {
    this.client = client;
    this.config = config;
    this.logger = logger;
    this.resolveSessionConfig = resolveSessionConfig;
    this.states = new Map();
    this.sessionConfigs = new WeakMap();
    this.drainTimer = null;
    this.drainRunning = false;
    this.drainPromise = Promise.resolve();
    this.drainHealth = null;
  }

  // Resolved once per session object: the bypass check runs on every event.
  sessionConfig(session) {
    let resolved = this.sessionConfigs.get(session);
    if (resolved === undefined) {
      resolved = this.resolveSessionConfig?.(session.header?.cwd || process.cwd()) ?? null;
      this.sessionConfigs.set(session, resolved);
    }
    return resolved;
  }

  isBypassed(session) {
    return isBypassed(this.sessionConfig(session) ?? this.config, {
      sessionId: String(session.id),
      cwd: session.header?.cwd || process.cwd(),
    });
  }

  stateFor(session) {
    let state = this.states.get(session.id);
    if (state) return state;
    const cwd = session.header?.cwd || process.cwd();
    const resolved = this.sessionConfig(session);
    const peer = resolved?.effectivePeer ?? resolveEffectivePeerId({
      cfg: {
        peerId: this.config.explicitPeerId,
        peerSource: this.config.peerSource,
        workspacePeer: this.config.workspacePeer,
        harness: this.config.harness,
      },
      cwd,
    });
    state = {
      dshSessionId: String(session.id),
      ovSessionId: deriveDshSessionId(session.id),
      config: { ...this.config, ...resolved, peerId: peer.peerId, legacyPeerId: peer.legacyPeerId },
      ready: false,
      profileBlock: "",
      historyPointer: null,
      toolNames: new Map(),
      writes: Promise.resolve(),
      warnedFailures: new Set(),
      initializationRetryable: false,
      hasPendingWrites: false,
      pendingCreatedAt: 0,
      disposing: null,
    };
    this.states.set(session.id, state);
    return state;
  }

  async initialize(agent) {
    const state = this.stateFor(agent.session);
    return this.ensureState(state);
  }

  async ensureState(state) {
    if (state.ready) return state;
    if (state.initializing) return state.initializing;
    state.initializing = this.initializeState(state).finally(() => {
      state.initializing = null;
    });
    return state.initializing;
  }

  async initializeState(state) {
    state.initializationRetryable = false;
    const health = await this.client.healthResult();
    this.reportWriteResult(state, "health", health, "/health");
    if (!health.ok) {
      state.initializationRetryable = isRetryableFailure(health);
      return state;
    }
    const ensured = await this.client.ensureSessionResult(
      state.ovSessionId,
      state.config.peerId,
    );
    if (
      !ensured.ok
      && !(ensured.status === 409 && ensured.error?.code === "ALREADY_EXISTS")
    ) {
      this.reportWriteResult(state, "ensure_session", ensured, "/api/v1/sessions");
      state.initializationRetryable = isRetryableFailure(ensured);
      return state;
    }
    state.warnedFailures.delete("ensure_session");
    // Replay is a write, so it stays behind the same toggle: a backlog queued
    // while capture was on waits for a session that still writes.
    if (isCaptureEnabled(state.config)) {
      await this.replayPendingQueue();
    }
    await this.refreshPendingState(state);
    const profile = state.config.noAutoInject ? null : await buildProfileBlock(
      (path, init, options) => this.client.fetchJSON(path, init, options),
      state.config.profileTokenBudget,
      state.config.peerId,
      state.config,
    );
    state.profileBlock = profile?.block
      ? [
          '<openviking-context source="profile">',
          profile.block,
          "</openviking-context>",
        ].join("\n")
      : "";
    state.ready = true;
    return state;
  }

  // Sent whenever the model would not see it: at session start, and again after
  // a compaction summarizes it away. `batch` is the step's claimed messages.
  async profileMessage(agent, batch = []) {
    const state = await this.initialize(agent);
    if (!state.ready || !state.profileBlock || hasStartupProfile(agent, batch)) return null;
    return pluginMessage(state.profileBlock, { form: "instructions" });
  }

  async recallMessage(agent, messages, signal) {
    const state = await this.initialize(agent);
    if (!state.ready || !isRecallEnabled(state.config)) return null;
    const query = promptText(messages);
    if (!query || query.length < state.config.minQueryLength) return null;
    if (signal?.aborted) return null;
    const runCompressor = ["client", "auto"].includes(state.config.recallRewrite)
      ? createRecallCompressor(agent, state.config, signal) : null;
    const cacheId = createHash("sha256").update(JSON.stringify([
      state.config.baseUrl, state.config.account, state.config.user, state.config.apiKey,
      state.config.peerId, state.ovSessionId, agent.options?.provider,
      state.config.recallCompressModel || agent.options?.model,
      state.config.recallCompressThinking,
    ])).digest("hex");
    const block = await buildRecallBlock(
      (path, init, options) => this.client.fetchJSON(path, init, options),
      state.config,
      query,
      {
        runCompressor,
        localCompressorAvailable: Boolean(runCompressor),
        digestCachePath: join(stateDir(), `dsh-recall-${cacheId}.json`),
        actorPeerId: state.config.peerId,
        legacyPeerId: state.config.legacyPeerId,
        sessionId: state.ovSessionId,
        excludeUris: state.config.recallExcludeUris,
        log: (stage, data) => this.log(stage, data),
      },
    );
    return block && !signal?.aborted ? pluginMessage(block, { form: "recall" }) : null;
  }

  // A finished compaction or a resume leaves the session-history note for the
  // agent's next step.
  markHistoryPointer(session, event) {
    this.stateFor(session).historyPointer = event;
  }

  historyPointerMessage(agent, sessions) {
    const state = this.stateFor(agent.session);
    const event = state.historyPointer;
    if (!event) return null;
    state.historyPointer = null;
    if (!state.config.sessionHistoryPointer || !isCaptureEnabled(state.config)) return null;
    return pluginMessage(formatSessionHistoryPointer(historySessionIds(agent.session, sessions), { event }), {
      form: "session-history",
    });
  }

  capture(session, event) {
    const state = this.stateFor(session);
    if (!isCaptureEnabled(state.config)) return;
    const payload = captureEvent(event, state.config, state.toolNames);
    if (!payload) return;
    this.enqueueWrite(state, async () => {
      if (state.hasPendingWrites) {
        await this.enqueuePendingMessage(state, payload);
        return;
      }
      if (!state.ready && !(await this.ensureState(state)).ready) {
        if (state.initializationRetryable) {
          await this.enqueuePendingMessage(state, payload);
        }
        return;
      }
      if (state.hasPendingWrites) {
        await this.enqueuePendingMessage(state, payload);
        return;
      }
      const response = await this.client.addMessage(
        state.ovSessionId,
        payload,
        state.config.peerId,
      );
      this.reportWriteResult(state, "capture", response, "/api/v1/sessions/{session_id}/messages");
      if (isRetryableFailure(response)) {
        await this.enqueuePendingMessage(state, payload);
      }
    });
  }

  maybeCommit(session, event) {
    // DSH appends compaction/start before it summarizes and rewrites history,
    // so commit there regardless of the threshold, like PreCompact elsewhere.
    const boundary = event.type === "compaction/start";
    if (event.type !== "turn/end" && !boundary) return;
    const state = this.stateFor(session);
    if (!isCaptureEnabled(state.config)) return;
    if (boundary && !commitsOnCompaction(state.config)) return;
    this.enqueueWrite(state, async () => {
      // The server cannot count queued messages toward the threshold, so that
      // check waits for the backlog to drain; a compaction commit queues behind it.
      if (state.hasPendingWrites && !boundary) return;
      if (!state.hasPendingWrites && !state.ready && !(await this.ensureState(state)).ready) return;
      if (!boundary) {
        const metadata = await this.client.getSession(
          state.ovSessionId,
          state.config.peerId,
        );
        const due = await thresholdCommitDue(
          (path, init) => this.client.fetchJSON(path, init, { actorPeerId: state.config.peerId }),
          state.ovSessionId,
          state.config,
          metadata,
        );
        if (!due) return;
      }
      await this.commit(state, boundary ? "compaction_commit" : "commit");
    });
  }

  dispose(session) {
    const state = this.states.get(session.id);
    if (!state) return;
    if (state.disposing) return state.disposing;
    state.disposing = (async () => {
      this.enqueueWrite(state, async () => {
        if (!isCaptureEnabled(state.config)) return;
        if (!state.hasPendingWrites && !state.ready && !(await this.ensureState(state)).ready) return;
        await this.commit(state, "shutdown_commit", Math.min(3000, Number(state.config.requestTimeoutMs) || 3000));
      });
      try {
        await state.writes;
      } finally {
        if (this.states.get(session.id) === state) this.states.delete(session.id);
      }
    })();
    return state.disposing;
  }

  // While the session has queued writes the commit is queued behind them
  // without a send: the drainer owns replay, and the shared commitSession would
  // replay the whole queue inline under this session's actor peer.
  async commit(state, stage, timeoutMs = 30000) {
    const body = { keep_recent_count: 0 };
    if (state.hasPendingWrites) {
      this.notePendingCommit(state, await enqueueCommitAfterWrites(state.ovSessionId, body));
      return;
    }
    const response = await commitSession(
      (path, init) => this.client.fetchJSON(path, init, { timeoutMs, actorPeerId: state.config.peerId }),
      state.ovSessionId,
      body,
      { log: (name, data) => this.log(name, data) },
    );
    if (response.deferred) {
      this.notePendingCommit(state, { ok: response.pendingQueued });
      return;
    }
    this.reportWriteResult(state, stage, response, "/api/v1/sessions/{session_id}/commit");
    this.log(stage, {
      sessionId: state.ovSessionId,
      ok: response.ok,
      trace_id: response.result?.trace_id || response.traceId,
      error: response.ok ? undefined : response.error?.message || response.error?.code,
    });
    if (response.pendingQueued || response.pendingEnqueueFailed) {
      this.notePendingCommit(state, { ok: Boolean(response.pendingQueued) });
    }
  }

  notePendingCommit(state, result) {
    // A commit queued behind writes is stamped one past the latest of them;
    // messages queued after it must sort after it.
    if (result.ok) state.pendingCreatedAt = Math.max(state.pendingCreatedAt + 1, Date.now());
    this.notePending(state, "commitSession", result);
  }

  async disposeAll() {
    await Promise.all([...this.states.values()].map(state => this.dispose({
      id: state.dshSessionId,
    })));
  }

  enqueueWrite(state, operation) {
    state.writes = state.writes
      .then(async () => {
        await operation();
        state.warnedFailures.delete("write_error");
      })
      .catch(error => {
        this.log("write_error", {
          sessionId: state.ovSessionId,
          error: error instanceof Error ? error.message : String(error),
        });
        this.warnOnce(state, "write_error", {});
      });
  }

  reportWriteResult(state, stage, response, path) {
    if (response.ok) {
      state.warnedFailures.delete(stage);
    } else if (!isRetryableFailure(response)) {
      // Error messages/bodies may echo captured content or credentials. Keep
      // only a bounded code token and a static endpoint template in warnings.
      const code = response.error?.code;
      this.warnOnce(state, stage, {
        path,
        status: Number(response.status) || 0,
        code: typeof code === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(code) ? code : undefined,
      });
    }
  }

  warnOnce(state, stage, data) {
    if (state.warnedFailures.has(stage)) return;
    state.warnedFailures.add(stage);
    this.logger?.warn?.(`[openviking:dsh] ${stage} ${JSON.stringify({
      sessionId: state.ovSessionId,
      ...data,
    })}`);
  }

  async enqueuePendingMessage(state, payload) {
    const createdAt = Math.max(Date.now(), state.pendingCreatedAt + 1);
    state.pendingCreatedAt = createdAt;
    const result = await enqueue("addMessage", state.ovSessionId, payload, { createdAt });
    this.notePending(state, "addMessage", result);
    return result;
  }

  notePending(state, type, result) {
    if (!result.ok) {
      this.log("pending_enqueue_error", {
        sessionId: state.ovSessionId,
        type,
        error: result.error,
      });
      return;
    }
    // Log the latch transition only: every message that follows while the
    // latch holds takes the cheap enqueue path, so this fires once per
    // outage, not once per message.
    if (!state.hasPendingWrites) {
      this.log("pending_latched", { sessionId: state.ovSessionId, type });
    }
    state.hasPendingWrites = true;
  }

  async refreshPendingState(state) {
    const wasPending = state.hasPendingWrites;
    const pending = (await listPending()).filter(
      item => item.entry?.sessionId === state.ovSessionId,
    );
    state.hasPendingWrites = pending.length > 0;
    state.pendingCreatedAt = pending.reduce(
      (latest, item) => Math.max(latest, Number(item.entry?.createdAt || 0)),
      state.pendingCreatedAt,
    );
    // Only the flip back to direct sends is logged: the recovery moment that
    // proves the drainer worked, once per outage.
    if (wasPending && !state.hasPendingWrites) {
      this.log("pending_cleared", { sessionId: state.ovSessionId });
    }
  }

  /**
   * Replay the pending queue through this runtime's client. The session-start
   * path calls it without options and keeps consuming retries; the drainer
   * passes consumeRetries:false so transient failures stay retryable.
   */
  async replayPendingQueue(options = {}) {
    await replayPending(
      (path, init) => this.client.fetchJSON(path, init),
      (stage, data) => this.log(stage, data),
      options,
    );
  }

  /**
   * One drainer tick, following the session-start replay flow: probe health
   * first and only replay when the server answers. Replays run without
   * consuming retry budgets, then every session's latch is re-derived from
   * the queue. An empty queue clears the latches with zero HTTP traffic, so
   * once a transient write failure recovers, capture and commit resume on
   * their own without restarting the long-lived dsh process.
   */
  async drainTick() {
    if (this.drainRunning) return;
    this.drainRunning = true;
    try {
      const pending = await listPending();
      if (pending.length > 0) {
        const health = await this.client.healthResult();
        if (!health.ok) {
          // Only the flip into the outage is logged, not every 60s probe.
          if (this.drainHealth !== false) {
            this.log("drain_health_down", { status: health.status || 0 });
          }
          this.drainHealth = false;
        } else {
          if (this.drainHealth === false) {
            this.log("drain_health_restored", {});
          }
          this.drainHealth = true;
          await this.replayPendingQueue({ consumeRetries: false });
        }
      }
      for (const state of this.states.values()) {
        await this.refreshPendingState(state);
      }
    } finally {
      this.drainRunning = false;
    }
  }

  /**
   * Start the background drainer. The interval is fixed per process; each tick
   * is single-flight, so a slow replay run never overlaps the next one.
   */
  startDrainer() {
    if (this.drainTimer) return this.drainTimer;
    const parsed = parseInt(process.env.OPENVIKING_PENDING_DRAIN_INTERVAL_MS || "", 10);
    const intervalMs = Number.isFinite(parsed) && parsed > 0 ? parsed : 60000;
    this.drainTimer = setInterval(() => {
      this.drainPromise = this.drainTick().catch(error => this.log("drain_error", {
        error: error instanceof Error ? error.message : String(error),
      }));
    }, intervalMs);
    this.drainTimer.unref?.();
    return this.drainTimer;
  }

  stopDrainer() {
    if (!this.drainTimer) return;
    clearInterval(this.drainTimer);
    this.drainTimer = null;
  }

  async flush(session) {
    const state = this.states.get(session.id);
    if (state) await state.writes;
  }

  log(stage, data) {
    this.logger?.debug?.(`[openviking:dsh] ${stage} ${JSON.stringify(data)}`);
  }
}

// The history the model keeps: a compaction replaces summarized messages here
// but never removes their events from the log.
function hasStartupProfile(agent, batch) {
  const history = agent.session?.deriveMessages?.() ?? [];
  return [history, batch, agent.inbox?.nextTurn, agent.inbox?.nextStep].some(messages => (
    (messages || []).some(isStartupProfile)
  ));
}

function isStartupProfile(message) {
  return isOpenVikingPluginMessage(message)
    && message.source.form === "instructions";
}
