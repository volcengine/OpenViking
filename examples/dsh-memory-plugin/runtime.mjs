import { createHash } from "node:crypto";
import { join } from "node:path";
import { OpenVikingClient } from "./client.mjs";
import { stateDir } from "./shared/workspace-identity.mjs";
import { createRecallCompressor } from "./compressor.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { buildRecallBlock, isRecallEnabled } from "./shared/recall-core.mjs";
import { formatSessionHistoryPointer } from "./shared/session-history-pointer.mjs";
import {
  createPendingQueue,
} from "./shared/pending-queue.mjs";
import { isRetryableFailure } from "./shared/retryable.mjs";
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
    this.config = config;
    this.connection = { client, pending: createPendingQueue(config.connectionScope), drainHealth: null };
    this.connections = new Map([[config.connectionScope || "", this.connection]]);
    this.logger = logger;
    this.resolveSessionConfig = resolveSessionConfig;
    this.states = new Map();
    this.drainTimer = null;
    this.drainRunning = false;
    this.drainPromise = Promise.resolve();
  }

  stateFor(session) {
    let state = this.states.get(session.id);
    if (state) return state;
    const cwd = session.header?.cwd || process.cwd();
    const resolved = this.resolveSessionConfig?.(cwd, String(session.id));
    const config = { ...this.config, ...resolved };
    const scope = config.connectionScope || "";
    let connection = this.connections.get(scope);
    if (!connection) {
      connection = { client: new OpenVikingClient(config), pending: createPendingQueue(scope), drainHealth: null };
      this.connections.set(scope, connection);
    }
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
      config: { ...config, peerId: peer.peerId, legacyPeerId: peer.legacyPeerId },
      connection,
      ready: false,
      profileBlock: "",
      profileDelivered: false,
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
    const health = await state.connection.client.healthResult();
    this.reportWriteResult(state, "health", health, "/health");
    if (!health.ok) {
      state.initializationRetryable = isRetryableFailure(health);
      return state;
    }
    const ensured = await state.connection.client.ensureSessionResult(
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
      await this.replayPendingQueue({}, state.connection);
    }
    await this.refreshPendingState(state);
    const profile = await buildProfileBlock(
      (path, init, options) => state.connection.client.fetchJSON(path, init, options),
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

  async profileMessage(agent) {
    const state = await this.initialize(agent);
    if (!state.ready || !state.profileBlock || state.profileDelivered) return null;
    if (hasStartupProfile(agent)) {
      state.profileDelivered = true;
      return null;
    }
    state.profileDelivered = true;
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
      (path, init, options) => state.connection.client.fetchJSON(path, init, options),
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
      const response = await state.connection.client.addMessage(
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
    this.enqueueWrite(state, async () => {
      if (state.hasPendingWrites) return;
      if (!state.ready && !(await this.ensureState(state)).ready) return;
      if (!boundary) {
        const metadata = await state.connection.client.getSession(
          state.ovSessionId,
          state.config.peerId,
        );
        if (Number(metadata?.pending_tokens || 0) < state.config.commitTokenThreshold) return;
      }
      const response = await state.connection.client.commitSession(
        state.ovSessionId,
        state.config.peerId,
      );
      const stage = boundary ? "compaction_commit" : "commit";
      this.reportWriteResult(state, stage, response, "/api/v1/sessions/{session_id}/commit");
      this.log(stage, {
        sessionId: state.ovSessionId,
        ok: response.ok,
        trace_id: response.result?.trace_id || response.traceId,
        error: response.ok ? undefined : response.error?.message || response.error?.code,
      });
      if (isRetryableFailure(response)) {
        await this.enqueueFinalCommit(state, { keep_recent_count: 0 });
      }
    });
  }

  dispose(session) {
    const state = this.states.get(session.id);
    if (!state) return;
    if (state.disposing) return state.disposing;
    state.disposing = (async () => {
      this.enqueueWrite(state, async () => {
        if (!isCaptureEnabled(state.config)) return;
        const commitPayload = { keep_recent_count: 0 };
        if (state.hasPendingWrites) {
          await this.enqueueFinalCommit(state, commitPayload);
          return;
        }
        if (!state.ready && !(await this.ensureState(state)).ready) return;
        const response = await state.connection.client.commitSession(
          state.ovSessionId,
          state.config.peerId,
          { timeoutMs: Math.min(3000, Number(state.config.requestTimeoutMs) || 3000) },
        );
        this.reportWriteResult(state, "shutdown_commit", response, "/api/v1/sessions/{session_id}/commit");
        this.log("shutdown_commit", {
          sessionId: state.ovSessionId,
          ok: response.ok,
          trace_id: response.result?.trace_id || response.traceId,
        });
        if (isRetryableFailure(response)) {
          await this.enqueueFinalCommit(state, commitPayload);
        }
      });
      try {
        await state.writes;
      } finally {
        if (this.states.get(session.id) === state) this.states.delete(session.id);
      }
    })();
    return state.disposing;
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

  async enqueuePending(state, type, payload) {
    const createdAt = Math.max(Date.now(), state.pendingCreatedAt + 1);
    state.pendingCreatedAt = createdAt;
    const result = await state.connection.pending.enqueue(type, state.ovSessionId, payload, { createdAt });
    if (result.ok) {
      // Log the latch transition only: every message that follows while the
      // latch holds takes the cheap enqueue path, so this fires once per
      // outage, not once per message.
      if (!state.hasPendingWrites) {
        this.log("pending_latched", { sessionId: state.ovSessionId, type });
      }
      state.hasPendingWrites = true;
    }
    if (!result.ok) {
      this.log("pending_enqueue_error", {
        sessionId: state.ovSessionId,
        type,
        error: result.error,
      });
    }
    return result;
  }

  async enqueueFinalCommit(state, payload) {
    await this.removePendingCommits(state);
    return this.enqueuePending(state, "commitSession", payload);
  }

  async enqueuePendingMessage(state, payload) {
    const result = await this.enqueuePending(state, "addMessage", payload);
    if (result.ok) await this.removePendingCommits(state);
    return result;
  }

  async removePendingCommits(state) {
    const pending = await state.connection.pending.listPending();
    for (const item of pending) {
      if (
        item.entry?.type === "commitSession"
        && item.entry.sessionId === state.ovSessionId
      ) {
        await state.connection.pending.dequeue(item.filename);
      }
    }
  }

  async refreshPendingState(state) {
    const wasPending = state.hasPendingWrites;
    const pending = (await state.connection.pending.listPending()).filter(
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
   * Replay a connection's pending queue through its own client. The session-start
   * path calls it without options and keeps consuming retries; the drainer
   * passes consumeRetries:false so transient failures stay retryable.
   */
  async replayPendingQueue(options = {}, connection = this.connection) {
    await connection.pending.replayPending(
      (path, init) => connection.client.fetchJSON(path, init),
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
      // Retain bindings after session disposal so their queued writes can drain.
      for (const connection of this.connections.values()) {
        const pending = await connection.pending.listPending();
        if (pending.length === 0) continue;
        const health = await connection.client.healthResult();
        if (!health.ok) {
          // Only the flip into the outage is logged, not every 60s probe.
          if (connection.drainHealth !== false) {
            this.log("drain_health_down", { status: health.status || 0 });
          }
          connection.drainHealth = false;
        } else {
          if (connection.drainHealth === false) {
            this.log("drain_health_restored", {});
          }
          connection.drainHealth = true;
          await this.replayPendingQueue({ consumeRetries: false }, connection);
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

function hasStartupProfile(agent) {
  const session = agent.session;
  const ownEvents = typeof session?.ownEvents === "function"
    ? session.ownEvents()
    : (session?.events || []).slice(session?.header?.seedLength ?? 0);
  const inHistory = ownEvents.some(event => (
    event?.type === "user/message" && isStartupProfile(event.data)
  ));
  if (inHistory) return true;
  return [agent.inbox?.nextTurn, agent.inbox?.nextStep].some(messages => (
    (messages || []).some(isStartupProfile)
  ));
}

function isStartupProfile(message) {
  return isOpenVikingPluginMessage(message)
    && message.source.form === "instructions";
}
