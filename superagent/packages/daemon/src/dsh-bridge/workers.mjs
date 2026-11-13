// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Worker sessions live beside the router in the same DSH profile. This module
// is deliberately an execution adapter, not a second task manager: TaskManager
// in the daemon owns task state, slots, inputs and reports. The bridge owns only
// DSH sessions, per-turn authority and the native turn boundary.
import { callTool, listTools } from '../actions/client.mjs';
import { DshBridgeError } from './handlers.mjs';

export const DSH_WORKER_METHODS = Object.freeze([
  'superagent/worker/ensure', 'superagent/worker/submit',
  'superagent/worker/wait', 'superagent/worker/cancel',
]);
export const DSH_WORKER_PRESET = 'ov-superagent-worker';
const WORK_TOOLS = Object.freeze([
  'bash', 'pwsh', 'read', 'read_image', 'write', 'edit', 'grep', 'find', 'ls',
  'job_output', 'job_list', 'job_kill', 'skill', 'todo', 'todo_read', 'todo_write',
]);

function invalid(message) { return new DshBridgeError(-32602, message); }
function text(value) { return typeof value === 'string' ? value.trim() : ''; }
function required(value, name) {
  const result = text(value);
  if (!result) throw invalid(`${name} must be a non-empty string`);
  return result;
}
function endpoint(authority) {
  const socketPath = process.env.PERSONAL_AGENT_SOCKET;
  if (!socketPath) throw new DshBridgeError(-32603, 'worker has no Core action socket');
  return { socketPath, authority, client: 'dsh-worker' };
}
function usageOf(usage) {
  if (!usage) return null;
  return {
    inputTokens: Number(usage.inputTokens ?? 0) + Number(usage.cacheReadTokens ?? 0),
    cachedInputTokens: Number(usage.cacheReadTokens ?? 0),
    outputTokens: Number(usage.outputTokens ?? 0),
    reasoningTokens: Number(usage.reasoningTokens ?? 0),
  };
}
function messageText(event) {
  if (event?.type !== 'assistant/message') return '';
  return (event.data?.message?.content ?? []).filter(block => block?.type === 'text')
    .map(block => String(block.text ?? '')).join('\n').trim();
}
function requestIdOf(message) {
  return message?.source?.kind === 'ov-superagent-worker' ? message.source.requestId : null;
}

export function createWorkerHandlers({
  ctx, agents, createUserMessage,
  listWorkerTools = listTools, callWorkerTool = callTool, workerEndpoint = endpoint,
  scopeParentOf = (agent) => agent.ctx,
}) {
  const states = new Map();
  const byTask = new Map();
  const waiters = new Set();

  function agentFor(sessionId) {
    const agent = agents.get(sessionId);
    if (!agent) throw invalid(`worker session not loaded: ${sessionId}`);
    return agent;
  }

  function settle(state, result) {
    if (!state || state.settled) return;
    state.settled = true;
    state.authority = '';
    state.result = result;
    for (const waiter of state.waiters.splice(0)) waiter.resolve(result);
  }

  function killJobs(state) {
    const jobs = ctx.get?.('jobs');
    if (!jobs || !state) return;
    for (const job of jobs.list(state.sessionId)) {
      if (job.owner !== state.sessionId || !['running', 'stopping'].includes(job.status)) continue;
      try { jobs.kill(job.id, state.sessionId, 'Worker turn ended'); } catch {}
    }
  }

  function onEvent(session, event) {
    const state = states.get(session.id);
    if (!state || state.agent.session !== session) return;
    if (event.type === 'assistant/message') {
      const text = messageText(event);
      if (text) state.text = text;
      const usage = usageOf(event.data?.usage);
      if (usage) {
        const current = state.usage ?? { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0 };
        for (const key of Object.keys(current)) current[key] += usage[key];
        state.usage = current;
      }
    }
    if (event.type === 'turn/end') state.ended = event.data ?? {};
  }

  function onStatus({ agent, status }) {
    const state = states.get(agent.id);
    if (!state || state.agent !== agent || status !== 'idle' || !state.submitted || state.settled) return;
    killJobs(state);
    const reason = state.ended?.reason;
    const failed = state.error ?? (() => {
      if (!reason || ['completed', 'max-tokens', 'end_turn'].includes(reason.kind)) return null;
      if (reason.kind === 'aborted') return state.cancelled ? null : `aborted: ${reason.reason?.kind ?? 'unknown'}`;
      if (reason.kind === 'error') return reason.error?.message ?? reason.error?.code ?? 'dsh turn failed';
      return reason.kind || 'dsh turn failed';
    })();
    settle(state, {
      text: state.text,
      usage: state.usage ?? { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 },
      nativeSession: state.sessionId,
      error: state.cancelled ? 'stopped' : failed,
      transient: false,
    });
  }

  function onError({ agent, error }) {
    const state = states.get(agent.id);
    if (!state || state.agent !== agent) return;
    state.error = error instanceof Error ? error.message : String(error);
  }

  ctx.on?.('session/event', onEvent);
  ctx.on?.('agent/status', onStatus);
  ctx.on?.('agent/error', onError);

  async function decorate(state, policy) {
    const { agent } = state;
    const tools = await listWorkerTools(workerEndpoint(state.authority));
    state.disposers.push(agent.ctx.systemPrompt.section({
      name: 'ov-superagent-worker-policy', order: 9999, interpolate: false, text: policy,
    }));
    state.disposers.push(agent.ctx.tools.restrict({
      allow: WORK_TOOLS.filter(name => agent.ctx.tools.get(name, scopeParentOf(agent))),
    }));
    for (const definition of tools) {
      state.disposers.push(agent.ctx.tools.register({
        name: definition.name,
        description: definition.description,
        parameters: definition.inputSchema,
        output: {
          schema: { type: 'object', additionalProperties: false, properties: { text: { type: 'string' } }, required: ['text'] },
          render: (_args, value) => [{ type: 'text', text: value.text }],
        },
        execute: async (args, execution) => {
          if (!state.authority) throw new Error('This worker turn has ended; task tools are no longer authorized.');
          const result = await callWorkerTool(workerEndpoint(state.authority), definition.name, args, { signal: execution.signal });
          return { text: result.text };
        },
      }));
    }
  }

  async function ensure(params) {
    const taskId = required(params.taskId, 'taskId');
    const sessionId = required(params.sessionId, 'sessionId');
    const cwd = required(params.cwd, 'cwd');
    const authority = required(params.authority, 'authority');
    const policy = required(params.policy, 'policy');
    if (params.turn !== undefined && (!Number.isSafeInteger(params.turn) || params.turn < 1)) {
      throw invalid('turn must be a positive integer');
    }
    let state = states.get(sessionId);
    if (!state || agents.get(sessionId) !== state.agent) {
      const previous = byTask.get(taskId);
      if (previous && previous !== state) {
        previous.authority = '';
        settle(previous, { text: previous.text, nativeSession: previous.sessionId, error: 'worker session replaced', transient: false });
      }
      state = {
        taskId, sessionId, agent: null, authority, waiters: [], disposers: [],
        text: '', usage: null, submitted: false, settled: false, result: null, requestIds: new Map(),
      };
      const setup = async (_agentCtx, agent) => {
        state.agent = agent;
        await decorate(state, policy);
      };
      const persistence = ctx.get?.('sessionPersistence');
      const stored = persistence ? await persistence.stat(sessionId).catch(() => null) : null;
      const handle = stored
        ? await agents.resume({ resumeSessionId: sessionId, setup })
        : await agents.create({ sessionId, meta: { cwd, agentPreset: DSH_WORKER_PRESET }, setup });
      state.agent = handle.agent;
      states.set(sessionId, state);
      byTask.set(taskId, state);
    }
    state.authority = authority;
    state.taskId = taskId;
    byTask.set(taskId, state);
    if (params.model !== undefined || params.effort !== undefined) {
      const provider = required(params.provider, 'provider');
      const model = required(params.model, 'model');
      const selection = { provider, model, ...(params.effort ? { reasoningEffort: params.effort } : {}) };
      await ctx.get('llm').resolveCallConfig(selection);
      state.agent.model.select(selection);
    }
    return { sessionId, preset: DSH_WORKER_PRESET };
  }

  function alreadySubmitted(agent, requestId) {
    for (const event of agent.session.snapshotEvents()) {
      if (event.type === 'user/message' && requestIdOf(event.data) === requestId) return event.data.id;
    }
    for (const message of [...agent.inbox.nextTurn, ...agent.inbox.nextStep]) {
      if (requestIdOf(message) === requestId) return message.id;
    }
    return null;
  }

  async function submit(params) {
    const sessionId = required(params.sessionId, 'sessionId');
    const requestId = required(params.requestId, 'requestId');
    const prompt = required(params.prompt, 'prompt');
    const state = states.get(sessionId);
    if (!state || state.agent !== agentFor(sessionId)) throw invalid('worker/ensure is required before submit');
    const existing = state.requestIds.get(requestId) ?? alreadySubmitted(state.agent, requestId);
    if (existing) return { messageId: existing, reused: true };
    if (state.submitted && !state.settled) throw invalid('worker already has a running turn');
    state.text = '';
    state.usage = null;
    state.error = null;
    state.ended = null;
    state.result = null;
    state.settled = false;
    state.cancelled = false;
    const message = createUserMessage({
      content: [{ type: 'text', text: prompt }],
      source: { kind: 'ov-superagent-worker', requestId },
    });
    state.agent.followup(message);
    state.requestIds.set(requestId, message.id);
    state.submitted = true;
    await ctx.get?.('sessionPersistence')?.flush();
    return { messageId: message.id, reused: false };
  }

  async function wait(params) {
    const sessionId = required(params.sessionId, 'sessionId');
    const state = states.get(sessionId);
    if (!state || !state.submitted) throw invalid('worker/submit is required before wait');
    if (state.result) return state.result;
    return new Promise((resolve, reject) => state.waiters.push({ resolve, reject }));
  }

  async function cancel(params) {
    const sessionId = required(params.sessionId, 'sessionId');
    const state = states.get(sessionId);
    if (!state) return { cancelled: false };
    state.cancelled = true;
    state.authority = '';
    killJobs(state);
    state.agent.cancel({ kind: 'user' }, { keepInbox: false });
    if (state.agent.status === 'idle') settle(state, {
      text: state.text, usage: state.usage, nativeSession: sessionId, error: 'stopped', transient: false,
    });
    return { cancelled: true };
  }

  return {
    'superagent/worker/ensure': ensure,
    'superagent/worker/submit': submit,
    'superagent/worker/wait': wait,
    'superagent/worker/cancel': cancel,
  };
}
