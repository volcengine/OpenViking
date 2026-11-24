import { createHash } from "node:crypto";
import { join } from "node:path";
import { stateDir } from "./shared/workspace-identity.mjs";
import { createRecallCompressor } from "./compressor.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { buildProfileBlock, buildSkillCatalog } from "./shared/profile-inject.mjs";
import { buildRecallBlock, isRecallEnabled } from "./shared/recall-core.mjs";
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
  isOpenVikingPluginMessage,
  pluginMessage,
  promptText,
} from "./capture.mjs";
import { deriveDshSessionId } from "./session-id.mjs";

const CATALOG_FORM = "catalog";
const CATALOG_TAG = "openviking-skills";
// The host keeps its own skill catalog, whose refresh text says it replaces
// every earlier "available-skills" list. A separate tag keeps the two apart,
// so a local catalog update cannot read as retiring the OpenViking one.
const CATALOG_USAGE_LINE =
  "  This complete catalog of OpenViking skills replaces every earlier <openviking-skills> list in this session. These skills live in OpenViking, not in local files, and are separate from the host's own skill list; before following one, read <dir>/<name>/SKILL.md with the OpenViking read tool.";
const DEFAULT_CATALOG_REFRESH_MS = 300000;
// A server that reports no skills at all has to say so: staying quiet would
// leave the last catalog the model saw standing, names included, after every
// one of those skills was deleted. The text is constant, so its digest is too:
// the model is told once, and a later catalog replaces it like any other.
const EMPTY_CATALOG_LINE =
  "  No OpenViking skills are currently available. Any earlier <openviking-skills> list in this session is out of date; use the OpenViking skill search to look for one.";

export class OpenVikingRuntime {
  constructor(client, config, logger = console, resolveSessionConfig = null) {
    this.client = client;
    this.config = config;
    this.logger = logger;
    this.resolveSessionConfig = resolveSessionConfig;
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
    const resolved = this.resolveSessionConfig?.(cwd);
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
      catalog: null,
      catalogBuiltAt: 0,
      catalogRefresh: null,
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
    // The skill catalog is deliberately not part of this block: a compaction
    // drops the whole block, and the catalog needs its own visibility check
    // and refresh cycle (catalogMessage/ensureCatalog).
    const profile = await buildProfileBlock(
      (path, init, options) => this.client.fetchJSON(path, init, options),
      state.config.profileTokenBudget,
      state.config.peerId,
      { sessionStartMaxBytes: state.config.sessionStartMaxBytes },
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

  /**
   * Deliver the profile until it is in the context the model actually keeps.
   *
   * A DSH compaction replaces the messages it summarizes without erasing their
   * events, so "was it ever sent?" cannot be answered from the session log.
   * deriveMessages() is the surviving history; when the profile is no longer in
   * it (compaction, or a restored session whose tail was summarized), it is
   * sent again. Nothing here needs to survive a process restart: the injected
   * message carries its own identity.
   */
  async profileMessage(agent, batch = []) {
    const state = await this.initialize(agent);
    if (!state.ready || !state.profileBlock) return null;
    if (hasDeliveredProfile(agent, batch)) return null;
    return pluginMessage(state.profileBlock, { form: "instructions" });
  }

  /**
   * Deliver the OpenViking skill catalog as its own message.
   *
   * The catalog is the model's only index of the user's skills, so it must
   * outlive a compaction and follow changes: when the visible catalog is gone,
   * or its digest differs from the one just built, this sends a complete
   * replacement — including the "no skills" replacement, because dropping a
   * skill is a change and silence would leave the deleted name in front of the
   * model. The digest rides in the message source, which the session log
   * keeps, so a restart re-derives the same answer instead of re-injecting.
   */
  async catalogMessage(agent, batch = []) {
    const state = await this.initialize(agent);
    if (!state.ready || !isSkillCatalogEnabled(state.config)) return null;
    const shown = latestCatalogMessage(agent, batch);
    // With a catalog already in front of the model, a due refresh runs behind
    // it: the step may use the list it has instead of waiting on the skills
    // endpoint, and a change lands on a later step.
    const catalog = await this.ensureCatalog(state, { wait: shown === undefined });
    if (!catalog) return null;
    if (catalog.count === 0 && shown === undefined) return null;
    if (shown?.source?.digest === catalog.digest) return null;
    return pluginMessage(catalog.content, {
      form: CATALOG_FORM,
      digest: catalog.digest,
      count: catalog.count,
    });
  }

  /**
   * Build the catalog at most once per refresh interval. A skill created in a
   * long-lived session reaches the model on the next interval, and an
   * unchanged catalog costs one request per interval rather than one per step.
   *
   * `wait: false` returns the catalog in hand while a single refresh runs in
   * the background; the caller that has no catalog to show must wait.
   */
  async ensureCatalog(state, { wait = true } = {}) {
    const refreshMs = Number(state.config.skillCatalogRefreshMs);
    const interval = Number.isFinite(refreshMs) && refreshMs >= 0
      ? refreshMs
      : DEFAULT_CATALOG_REFRESH_MS;
    const due = !state.catalogBuiltAt || Date.now() - state.catalogBuiltAt >= interval;
    if (!due) return state.catalog;
    state.catalogRefresh ??= this.refreshCatalog(state).finally(() => {
      state.catalogRefresh = null;
    });
    if (!wait) return state.catalog;
    await state.catalogRefresh;
    return state.catalog;
  }

  async refreshCatalog(state) {
    const built = await buildSkillCatalog(
      (path, init, options) => this.client.fetchJSON(path, init, options),
      state.config.skillCatalogTokenBudget,
      state.config.peerId,
      { tag: CATALOG_TAG, usageLine: CATALOG_USAGE_LINE },
    );
    // A failed read keeps the last good catalog: an unreachable server is not
    // the same answer as "this user has no skills". Without one there is
    // nothing to keep, so the next step retries instead of waiting out an
    // interval; with one, the retry waits.
    if (!built) {
      state.catalogBuiltAt = state.catalog ? Date.now() : 0;
      return;
    }
    state.catalogBuiltAt = Date.now();
    const inner = built.block || `<${CATALOG_TAG}>\n${EMPTY_CATALOG_LINE}\n</${CATALOG_TAG}>`;
    state.catalog = {
      content: [
        '<openviking-context source="catalog">',
        inner,
        "</openviking-context>",
      ].join("\n"),
      digest: createHash("sha256").update(inner).digest("hex"),
      count: built.count,
    };
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
    this.enqueueWrite(state, async () => {
      if (state.hasPendingWrites) return;
      if (!state.ready && !(await this.ensureState(state)).ready) return;
      if (!boundary) {
        const metadata = await this.client.getSession(
          state.ovSessionId,
          state.config.peerId,
        );
        if (Number(metadata?.pending_tokens || 0) < state.config.commitTokenThreshold) return;
      }
      const response = await this.client.commitSession(
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
        const response = await this.client.commitSession(
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

function hasDeliveredProfile(agent, batch) {
  return (batch || []).some(isStartupProfile)
    || pendingPluginMessage(agent, isStartupProfile)
    || Boolean(lastVisiblePluginMessage(agent.session, isStartupProfile));
}

/**
 * The catalog the model is looking at, newest first: this step's batch, then
 * whatever is queued for it, then the visible history.
 *
 * Comparing against the newest one is what makes removals work. An earlier
 * catalog with the same entries can still be in the history (A, then A+B, then
 * back to A again), so "a catalog with this digest exists somewhere" would read
 * as delivered while the newest message still lists the deleted skill.
 */
function latestCatalogMessage(agent, batch) {
  return (batch || []).findLast(isCatalogMessage)
    ?? [...(agent.inbox?.nextStep || [])].findLast(isCatalogMessage)
    ?? [...(agent.inbox?.nextTurn || [])].findLast(isCatalogMessage)
    ?? lastVisiblePluginMessage(agent.session, isCatalogMessage);
}

/**
 * Walk the history the model keeps, newest first. DSH compacts by replacing a
 * span of the surface, so deriveMessages() leaves out exactly what a compaction
 * dropped; the raw event log, which still holds it, cannot answer this.
 *
 * Every DSH version this plugin accepts (peer floor 0.1.0-rc.6) has
 * deriveMessages; the guard only keeps a host that violates that contract from
 * throwing inside the pre-step waterfall.
 */
function lastVisiblePluginMessage(session, match) {
  if (typeof session?.deriveMessages !== "function") return undefined;
  const messages = session.deriveMessages();
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (match(messages[index])) return messages[index];
  }
  return undefined;
}

/** Messages injected for this step but not yet appended to the session. */
function pendingPluginMessage(agent, match) {
  return [agent.inbox?.nextTurn, agent.inbox?.nextStep].some(messages => (
    (messages || []).some(match)
  ));
}

function isSkillCatalogEnabled(config) {
  return config.skillCatalog !== false && Number(config.skillCatalogTokenBudget) > 0;
}

function isStartupProfile(message) {
  return isOpenVikingPluginMessage(message)
    && message.source.form === "instructions";
}

function isCatalogMessage(message) {
  return isOpenVikingPluginMessage(message)
    && message.source.form === CATALOG_FORM;
}
