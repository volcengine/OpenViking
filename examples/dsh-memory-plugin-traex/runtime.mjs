import { randomUUID } from "node:crypto";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { buildRecallBlock } from "./shared/recall-core.mjs";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { enqueue, replayPending } from "./shared/pending-queue.mjs";
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
  }

  stateFor(session) {
    let state = this.states.get(session.id);
    if (state) return state;
    const cwd = session.header?.cwd || process.cwd();
    const peerId = resolveEffectivePeerId({
      cfg: {
        peerId: this.config.explicitPeerId,
        workspacePeer: this.config.workspacePeer,
      },
      cwd,
    }).peerId;
    state = {
      dshSessionId: String(session.id),
      ovSessionId: deriveHarnessSessionId("dsh-", String(session.id)),
      config: { ...this.config, peerId },
      ready: false,
      profileBlock: "",
      profileDelivered: false,
      toolNames: new Map(),
      writes: Promise.resolve(),
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
    if (!await this.client.health()) return state;
    if (!await this.client.ensureSession(state.ovSessionId, state.config.peerId)) return state;
    await replayPending(
      (path, init) => this.client.fetchJSON(path, init),
      (stage, data) => this.log(stage, data),
    );
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
      if (!state.ready && !(await this.ensureState(state)).ready) {
        await enqueue("addMessage", state.ovSessionId, payload);
        return;
      }
      const response = await this.client.addMessage(
        state.ovSessionId,
        payload,
        state.config.peerId,
      );
      if (!response.ok) await enqueue("addMessage", state.ovSessionId, payload);
    });
  }

  maybeCommit(session, event) {
    if (event.type !== "turn/end") return;
    const state = this.stateFor(session);
    this.enqueueWrite(state, async () => {
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
      if (!response.ok) {
        await enqueue("commitSession", state.ovSessionId, {
          keep_recent_count: state.config.commitKeepRecentCount,
        });
      }
    });
  }

  dispose(session) {
    const state = this.states.get(session.id);
    if (!state) return;
    this.enqueueWrite(state, async () => {
      if (!state.ready) return;
      const response = await this.client.commitSession(
        state.ovSessionId,
        state.config.peerId,
      );
      this.log("shutdown_commit", {
        sessionId: state.ovSessionId,
        ok: response.ok,
        trace_id: response.result?.trace_id || response.traceId,
      });
    });
    void state.writes.finally(() => {
      if (this.states.get(session.id) === state) this.states.delete(session.id);
    });
  }

  enqueueWrite(state, operation) {
    state.writes = state.writes
      .then(operation)
      .catch(error => this.log("write_error", {
        sessionId: state.ovSessionId,
        error: error instanceof Error ? error.message : String(error),
      }));
  }

  async flush() {
    await Promise.all([...this.states.values()].map(state => state.writes));
  }

  log(stage, data) {
    this.logger?.debug?.(`[openviking:dsh] ${stage} ${JSON.stringify(data)}`);
  }
}

function pluginMessage(content, form) {
  return {
    id: randomUUID(),
    role: "user",
    content: [{ type: "text", text: content }],
    source: {
      kind: "plugin",
      plugin: OPENVIKING_PLUGIN_SOURCE,
      form,
    },
  };
}
