import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { buildRecallBlock } from "./shared/recall-core.mjs";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import {
  dequeue,
  enqueue,
  listPending,
  replayPending,
} from "./shared/pending-queue.mjs";
import { isRetryableFailure } from "./shared/retryable.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";
import {
  captureEvent,
  OPENVIKING_PLUGIN_SOURCE,
  promptText,
} from "./capture.mjs";

export class OpenVikingRuntime {
  constructor(client, config, logger = console) {
    this.client = client;
    this.config = config;
    this.logger = logger;
    this.states = new Map();
    this.drainTimer = null;
    this.drainRunning = false;
    this.drainPromise = Promise.resolve();
    this.drainHealth = null;
  }

  stateFor(session) {
    let state = this.states.get(session.id);
    if (state) return state;
    const cwd = session.header?.cwd || process.cwd();
    const peer = resolveEffectivePeerId({
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
      ovSessionId: deriveHarnessSessionId("dsh-", String(session.id)),
      config: { ...this.config, peerId: peer.peerId, legacyPeerId: peer.legacyPeerId },
      ready: false,
      profileBlock: "",
      profileDelivered: false,
      toolNames: new Map(),
      writes: Promise.resolve(),
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
      state.initializationRetryable = isRetryableFailure(ensured);
      return state;
    }
    // Replay is a write, so it stays behind the same toggle: a backlog queued
    // while capture was on waits for a session that still writes.
    if (state.config.syncTurns) {
      await this.replayPendingQueue();
    }
    await this.refreshPendingState(state);
    const profile = await buildProfileBlock(
      (path, init, options) => this.client.fetchJSON(path, init, options),
      state.config.profileTokenBudget,
      state.config.peerId,
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
    return pluginMessage(state.profileBlock, "instructions");
  }

  async recallMessage(agent, messages) {
    const state = await this.initialize(agent);
    if (!state.ready) return null;
    const query = promptText(messages);
    if (query.length < state.config.minQueryLength) return null;
    const block = await buildRecallBlock(
      (path, init, options) => this.client.fetchJSON(path, init, options),
      state.config,
      query,
      {
        actorPeerId: state.config.peerId,
        legacyPeerId: state.config.legacyPeerId,
        sessionId: state.ovSessionId,
        log: (stage, data) => this.log(stage, data),
      },
    );
    return block ? pluginMessage(block, "recall") : null;
  }

  capture(session, event) {
    const state = this.stateFor(session);
    if (!state.config.syncTurns) return;
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
      if (isRetryableFailure(response)) {
        await this.enqueuePendingMessage(state, payload);
      }
    });
  }

  maybeCommit(session, event) {
    if (event.type !== "turn/end") return;
    const state = this.stateFor(session);
    if (!state.config.syncTurns) return;
    this.enqueueWrite(state, async () => {
      if (state.hasPendingWrites) return;
      if (!state.ready && !(await this.ensureState(state)).ready) return;
      const metadata = await this.client.getSession(
        state.ovSessionId,
        state.config.peerId,
      );
      if (Number(metadata?.pending_tokens || 0) < state.config.commitTokenThreshold) return;
      const response = await this.client.commitSession(
        state.ovSessionId,
        state.config.peerId,
      );
      this.log("commit", {
        sessionId: state.ovSessionId,
        ok: response.ok,
        trace_id: response.result?.trace_id || response.traceId,
        error: response.ok ? undefined : response.error?.message || response.error?.code,
      });
      if (isRetryableFailure(response)) {
        await this.enqueueFinalCommit(state, {
          keep_recent_count: state.config.commitKeepRecentCount,
        });
      }
    });
  }

  dispose(session) {
    const state = this.states.get(session.id);
    if (!state) return;
    if (state.disposing) return state.disposing;
    state.disposing = (async () => {
      this.enqueueWrite(state, async () => {
        if (!state.config.syncTurns) return;
        const commitPayload = {
          keep_recent_count: state.config.commitKeepRecentCount,
        };
        if (state.hasPendingWrites) {
          await this.enqueueFinalCommit(state, commitPayload);
          return;
        }
        if (!state.ready && !(await this.ensureState(state)).ready) return;
        const response = await this.client.commitSession(
          state.ovSessionId,
          state.config.peerId,
          { timeoutMs: Math.min(3000, Number(state.config.requestTimeoutMs) || 3000) },
        );
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
      .then(operation)
      .catch(error => this.log("write_error", {
        sessionId: state.ovSessionId,
        error: error instanceof Error ? error.message : String(error),
      }));
  }

  async enqueuePending(state, type, payload) {
    const createdAt = Math.max(Date.now(), state.pendingCreatedAt + 1);
    state.pendingCreatedAt = createdAt;
    const result = await enqueue(type, state.ovSessionId, payload, { createdAt });
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
    const pending = await listPending();
    for (const item of pending) {
      if (
        item.entry?.type === "commitSession"
        && item.entry.sessionId === state.ovSessionId
      ) {
        await dequeue(item.filename);
      }
    }
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

function pluginMessage(content, form) {
  // dsh's own constructor: identity, normalization, and any future Message
  // invariants come from the pinned peer instead of a hand-built object.
  return createUserMessage({
    content: [{ type: "text", text: content }],
    source: {
      kind: "plugin",
      plugin: OPENVIKING_PLUGIN_SOURCE,
      form,
    },
  });
}

function hasStartupProfile(agent) {
  const session = agent.session;
  const ownEvents = (session?.events || []).slice(session?.header?.seedLength ?? 0);
  const inHistory = ownEvents.some(event => (
    event?.type === "user/message" && isStartupProfile(event.data)
  ));
  if (inHistory) return true;
  return [agent.inbox?.nextTurn, agent.inbox?.nextStep].some(messages => (
    (messages || []).some(isStartupProfile)
  ));
}

function isStartupProfile(message) {
  return message?.source?.kind === "plugin"
    && message.source.plugin === OPENVIKING_PLUGIN_SOURCE
    && message.source.form === "instructions";
}
