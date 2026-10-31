// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Durable runtime host. One process owns one pi-durable Harness over SQLite;
// the daemon speaks JSON lines on stdin/stdout and never shares the model loop
// or tool execution process. Chat replies go through the Core agent tools this
// host registers from <dataDir>/actions.sock, exactly like the MCP adapter, and
// local work goes through pi-durable's coding tools (bash, read, write, edit).
//
// The protocol (version 2) is documented in docs/pi-durable-runtime.md.

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
import { BACKGROUND_CONTEXT } from '@earendil-works/chord/context';
import { createModels, Type } from '@earendil-works/pi-ai';
import {
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from '@earendil-works/pi-ai/providers/faux';
import {
  Harness,
  LiveDoc,
  createRegistry,
  defineExtension,
  defineTool,
  section,
} from '@earendil-works/pi-durable';
import { openNodeSqliteStorage } from '@earendil-works/pi-durable/storage/sqlite/node';
import { NodeExecutionEnv } from '@earendil-works/pi-durable/env/node';
import { CodingTools } from '@earendil-works/pi-durable/tools';
import { availableChatModels, loadDurableModels } from './durable-models.mjs';
import { actionEndpoint, callTool, listTools } from './actions/client.mjs';
import { DURABLE_PROTOCOL } from './durable-runtime.mjs';

const context = BACKGROUND_CONTEXT;
const MAX_INLINE_IMAGE_BYTES = 10_000_000;
const EFFORTS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

function packageVersion() {
  try {
    const entry = fileURLToPath(import.meta.resolve('@earendil-works/pi-durable'));
    const manifest = JSON.parse(fs.readFileSync(path.join(path.dirname(entry), '..', 'package.json'), 'utf8'));
    return String(manifest.version);
  } catch {
    return '';
  }
}

const VERSION = packageVersion();
let outputClosed = false;

function send(message) {
  if (outputClosed) return;
  try {
    process.stdout.write(`${JSON.stringify(message)}\n`);
  } catch {
    outputClosed = true;
  }
}

function log(detail) {
  try { process.stderr.write(`${JSON.stringify(detail)}\n`); } catch {}
}

function safeError(error) {
  return error instanceof Error ? error.message : String(error);
}

class Host {
  constructor() {
    this.harness = null;
    this.conversation = null;
    this.watchHandle = null;
    this.models = null;
    this.faux = null;
    this.policy = '';
    this.workspace = process.cwd();
    this.dataDir = '';
    this.agent = null;
    this.resolvedModel = '';
    // The only authoritative copy of this handle lives in the daemon's meta
    // table; the host receives it at handshake and reports any replacement back
    // in the command acknowledgement. Nothing is persisted here.
    this.nativeSessionId = null;
    this.sessionKey = '';
    this.lastEntryId = 0;
    this.busy = false;
    this.waitingIdle = false;
    this.pending = new Set();
    this.failures = [];
    this.turnSeq = 0;
    this.closing = false;
  }

  storagePath() {
    return path.join(this.dataDir, 'durable', 'session.sqlite');
  }

  modelRef(spec) {
    const raw = String(spec ?? '').trim();
    if (!raw) return null;
    const slash = raw.indexOf('/');
    if (slash <= 0 || slash === raw.length - 1) {
      // A bare id is accepted when exactly one provider offers it.
      const matches = this.models.getModels().filter((model) => model.id === raw);
      if (matches.length === 1) return { provider: matches[0].provider, modelId: matches[0].id };
      throw new Error(`durable model must be provider/modelId (got ${raw})`);
    }
    return { provider: raw.slice(0, slash), modelId: raw.slice(slash + 1) };
  }

  async availableModel() {
    const [first] = await availableChatModels(this.models);
    if (!first) throw new Error('durable runtime has no model with configured credentials; configure one in Pi (vika pi, then /login, or ~/.pi/agent/models.json) or set a provider API key such as OPENAI_API_KEY');
    return { provider: first.provider, modelId: first.id };
  }

  async resolveAgent() {
    const spec = this.agent?.model;
    let model = null;
    if (spec) model = this.modelRef(spec);
    else model = await this.availableModel();
    const effort = this.agent?.effort ?? 'medium';
    return { model, thinkingLevel: effort, cwd: this.workspace };
  }

  async buildModels() {
    if (process.env.DURABLE_FAUX === '1') {
      const models = createModels();
      const faux = fauxProvider({ models: [{ id: 'faux-model', reasoning: true }] });
      models.setProvider(faux.provider);
      this.faux = faux;
      this.scripted();
      return models;
    }
    const { models, error } = await loadDurableModels();
    // A broken models.json leaves the built-in providers; say why Pi's are
    // missing instead of listing a runtime that only looks unusable here.
    if (error) log({ type: 'pi_models_error', error });
    return models;
  }

  scripted() {
    const raw = process.env.DURABLE_FAUX_RESPONSES;
    if (!this.faux || !raw) return;
    let steps;
    try { steps = JSON.parse(raw); } catch { throw new Error('DURABLE_FAUX_RESPONSES must be JSON'); }
    if (!Array.isArray(steps)) throw new Error('DURABLE_FAUX_RESPONSES must be an array');
    const delay = Number.parseInt(process.env.DURABLE_FAUX_DELAY_MS ?? '0', 10) || 0;
    this.faux.setResponses(steps.map((step) => async () => {
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      if (step?.toolCall) {
        return fauxAssistantMessage(
          [fauxToolCall(step.toolCall.name, step.toolCall.arguments ?? {})],
          { stopReason: 'toolUse' },
        );
      }
      return fauxAssistantMessage(String(step?.text ?? ''));
    }));
  }

  async buildTools() {
    const tools = [];
    // The faux provider harness may run without a Core daemon (adapter and
    // host tests); every real host must expose the Core tools or fail closed.
    if (!process.env.PERSONAL_AGENT_SOCKET) {
      if (process.env.DURABLE_FAUX === '1') return tools;
      throw new Error('durable host requires PERSONAL_AGENT_SOCKET; start it through the Vika daemon');
    }
    const endpoint = actionEndpoint('durable');
    let definitions;
    try { definitions = await listTools(endpoint); } catch (error) {
      // The faux harness may run without a daemon socket; a real host must not
      // start with a broken tool surface, because every reply goes through it.
      if (process.env.DURABLE_FAUX === '1') {
        log({ type: 'tools_unavailable', error: safeError(error) });
        return tools;
      }
      throw new Error(`durable host cannot read the Core agent tools: ${safeError(error)}`);
    }
    for (const definition of definitions) {
      if (!definition?.name) continue;
      tools.push(defineTool({
        name: definition.name,
        description: String(definition.description ?? ''),
        parameters: Type.Unsafe(definition.inputSchema ?? { type: 'object', properties: {} }),
        execute: async (args) => {
          const result = await callTool(endpoint, definition.name, args);
          const output = [{ type: 'text', text: String(result?.text ?? '') }];
          // Images travel as content blocks, like the MCP adapter's inline
          // images, so get_message_media can hand one to a vision model.
          for (const media of result?.media ?? []) {
            if (!String(media.mime_type ?? '').startsWith('image/')) continue;
            if (!Number.isFinite(media.size) || media.size > MAX_INLINE_IMAGE_BYTES) continue;
            try {
              output.push({ type: 'image', data: fs.readFileSync(media.path).toString('base64'), mimeType: media.mime_type });
            } catch {}
          }
          return { output };
        },
      }));
    }
    return tools;
  }

  // Open the conversation the daemon named, or create the one it should name
  // next. The daemon publishes the answer, so a replacement here is reported to
  // it rather than written to a local index.
  async open({ create = true } = {}) {
    const mapped = this.nativeSessionId;
    if (Number.isSafeInteger(mapped)) {
      const existing = await this.harness.conversation(mapped, context);
      if (existing) return existing;
      this.nativeSessionId = null;
    }
    if (!create) throw new Error('durable session is not stored');
    const agent = await this.resolveAgent();
    const conversation = await this.harness.createConversation({ ownership: { kind: 'ownerless' }, agent }, context);
    this.nativeSessionId = conversation.id;
    return conversation;
  }

  async attach(conversation) {
    if (this.watchHandle) {
      try { await this.watchHandle.stop?.(); } catch {}
      this.watchHandle = null;
    }
    this.lastEntryId = 0;
    this.watchHandle = await conversation.watch(context);
    this.processView(this.watchHandle.value);
    // pi-durable 1.4.0 hands the listener (value, ops, context); the value is
    // the immutable newer revision of the structural view.
    this.watchHandle.start((value) => {
      try { this.processView(value); } catch (error) { log({ type: 'watch_error', error: safeError(error) }); }
      return Promise.resolve();
    });
  }

  processView(view) {
    for (const entry of view.entries ?? []) {
      if (!Number.isSafeInteger(entry.id) || entry.id <= this.lastEntryId) continue;
      this.lastEntryId = entry.id;
      try { this.processEntry(entry); } catch (error) { log({ type: 'entry_error', error: safeError(error) }); }
    }
  }

  processEntry(entry) {
    const message = entry.model?.[0];
    if (entry.kind === 'pi.assistant' && message) {
      for (const block of message.content ?? []) {
        if (block?.type === 'text' && block.text) {
          send({ type: 'note', text: String(block.text) });
        } else if (block?.type === 'thinking' && block.thinking) {
          send({ type: 'thinking', text: String(block.thinking) });
        } else if (block?.type === 'toolCall') {
          send({ type: 'tool_start', toolId: block.id, toolName: block.name, input: block.arguments ?? {} });
        }
      }
      const usage = message.usage;
      if (usage && Number.isFinite(usage.input)) {
        send({
          type: 'usage',
          inputTokens: usage.input + (usage.cacheRead ?? 0) + (usage.cacheWrite ?? 0),
          cachedInputTokens: usage.cacheRead ?? 0,
          outputTokens: usage.output ?? 0,
          costUsd: usage.cost?.total,
        });
      }
      return;
    }
    if (entry.kind === 'pi.tool-result' && message) {
      const text = (message.content ?? [])
        .filter((block) => block?.type === 'text')
        .map((block) => String(block.text ?? ''))
        .join('\n');
      send({
        type: 'tool_end',
        toolId: message.toolCallId,
        toolName: message.toolName,
        isError: message.isError === true,
        output: text,
      });
    }
  }

  async beginTurn() {
    if (this.busy) return;
    this.busy = true;
    this.turnSeq += 1;
    this.failures = [];
    send({ type: 'turn_start', turn: this.turnSeq });
    void this.waitIdle(this.turnSeq);
  }

  async waitIdle(turn) {
    if (this.waitingIdle) return;
    this.waitingIdle = true;
    try {
      let results = [];
      for (;;) {
        const before = this.pending.size;
        await this.conversation.waitForIdle(context);
        // A follow-up can start another run between the idle resolution and
        // this check; only report settled when the live run is gone and no
        // submission arrived while this loop was finalizing.
        let live = null;
        try { live = await this.harness.snapshot(LiveDoc, this.conversation.id, context); } catch {}
        if (live?.run) continue;
        results = await Promise.allSettled([...this.pending]);
        if (this.pending.size !== before) continue;
        break;
      }
      const failure = this.failures.find(Boolean)
        ?? results.map((result) => result.status === 'fulfilled' ? result.value : null)
          .find((settlement) => settlement && settlement.status !== 'done');
      const error = failure
        ? String(failure.detail?.message ?? failure.detail ?? failure.reason ?? 'run ended without an answer')
        : null;
      this.pending.clear();
      this.busy = false;
      send(error ? { type: 'settled', status: 'failed', error, turn } : { type: 'settled', status: 'done', turn });
    } catch (error) {
      this.pending.clear();
      this.busy = false;
      send({ type: 'settled', status: 'failed', error: safeError(error), turn });
    } finally {
      this.waitingIdle = false;
    }
  }

  async start(config) {
    this.dataDir = String(config?.dataDir ?? '');
    this.workspace = String(config?.workspace ?? process.cwd());
    this.policy = String(config?.policy ?? '');
    this.agent = { model: String(config?.model ?? ''), effort: String(config?.effort ?? 'medium') };
    const sessionKey = String(config?.sessionKey ?? '');
    const nativeSessionId = config?.nativeSessionId;
    if (!this.dataDir) throw new Error('durable host requires a dataDir');
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(sessionKey)) throw new Error('durable host requires a sessionKey');
    if (nativeSessionId !== null && nativeSessionId !== undefined && !Number.isSafeInteger(nativeSessionId)) {
      throw new Error('durable host requires an integer nativeSessionId');
    }

    fs.mkdirSync(path.join(this.dataDir, 'durable'), { recursive: true, mode: 0o700 });
    this.nativeSessionId = Number.isSafeInteger(nativeSessionId) ? nativeSessionId : null;
    this.models = await this.buildModels();
    const tools = await this.buildTools();
    const registry = createRegistry();
    registry.install(defineExtension({
      name: 'vika',
      sections: [section('policy', () => this.policy, { tag: false })],
      tools,
    }));
    // The policy promises local work ("run commands and write files in the
    // configured workspace"), and a delivery body may be a file in it. The
    // coding tools resolve their cwd from the same per-run NodeExecutionEnv as
    // the Core tools, so nothing else is wired here.
    registry.install(CodingTools);
    const storage = await openNodeSqliteStorage(this.storagePath());
    const env = ({ cwd }) => new NodeExecutionEnv({ cwd: cwd ?? this.workspace });
    this.harness = await Harness.open(storage, { models: this.models, registry, env }, context);
    this.sessionKey = sessionKey;
    this.conversation = await this.open();
    this.harness.resume();
    await this.attach(this.conversation);
    try {
      const resolved = await this.conversation.agent(context);
      if (resolved?.model) this.resolvedModel = `${resolved.model.provider}/${resolved.model.modelId ?? resolved.model.id}`;
    } catch {}
    this.harness.closed?.then((end) => {
      if (this.closing) return;
      send({ type: 'fatal', error: `durable harness closed (${end?.kind ?? 'unknown'})` });
      setTimeout(() => process.exit(1), 0);
    }).catch(() => {});
    const live = await this.harness.snapshot(LiveDoc, this.conversation.id, context);
    if (live?.run) {
      this.busy = true;
      send({ type: 'turn_start', turn: ++this.turnSeq });
      void this.waitIdle(this.turnSeq);
    }
    send({
      type: 'ready',
      protocol: DURABLE_PROTOCOL,
      version: VERSION,
      conversationId: this.conversation.id,
      model: this.agent.model,
      resolvedModel: this.resolvedModel,
      effort: this.agent.effort,
    });
  }

  async submit(command) {
    const text = String(command?.text ?? '');
    if (!text) throw new Error('durable submit requires text');
    const submission = await this.conversation.submit({
      type: 'input',
      content: text,
      whenBusy: 'steer',
      ...(command?.requestId ? { requestId: String(command.requestId) } : {}),
    }, context);
    this.pending.add(submission.wait(context).then((settlement) => {
      if (settlement.status !== 'done') this.failures.push(settlement);
      return settlement;
    }).catch((error) => {
      this.failures.push({ status: 'unanswered', reason: 'wait_failed', detail: safeError(error) });
    }));
    await this.beginTurn();
    return { submissionId: submission.id };
  }

  async configure(command) {
    const agent = {};
    if (typeof command?.model === 'string' && command.model.trim()) {
      const ref = this.modelRef(command.model);
      if (ref) agent.model = ref;
      this.agent.model = command.model;
    }
    if (typeof command?.effort === 'string' && command.effort.trim()) {
      agent.thinkingLevel = command.effort;
      this.agent.effort = command.effort;
    }
    await this.conversation.configure(agent, context);
    if (agent.model) this.resolvedModel = `${agent.model.provider}/${agent.model.modelId}`;
    return {};
  }

  async listModels() {
    const available = await availableChatModels(this.models);
    return {
      models: available
        .map((model) => ({
          id: `${model.provider}/${model.id}`,
          efforts: model.reasoning ? [...EFFORTS] : ['off'],
          ...(`${model.provider}/${model.id}` === this.resolvedModel ? { isDefault: true } : {}),
        })),
    };
  }

  async rotate(command) {
    const nextKey = String(command?.nextSessionKey ?? '');
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(nextKey)) throw new Error('durable rotate requires nextSessionKey');
    const agent = await this.resolveAgent();
    const conversation = await this.harness.createConversation({ ownership: { kind: 'ownerless' }, agent }, context);
    this.nativeSessionId = conversation.id;
    this.sessionKey = nextKey;
    this.conversation = conversation;
    await this.attach(conversation);
    return { conversationId: conversation.id };
  }

  async openSession(command) {
    const key = String(command?.sessionKey ?? '');
    const nativeSessionId = command?.nativeSessionId;
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(key)) throw new Error('durable open requires sessionKey');
    if (nativeSessionId !== null && nativeSessionId !== undefined && !Number.isSafeInteger(nativeSessionId)) {
      throw new Error('durable open requires an integer nativeSessionId');
    }
    this.nativeSessionId = Number.isSafeInteger(nativeSessionId) ? nativeSessionId : null;
    const conversation = await this.open({ create: command?.create !== false });
    this.sessionKey = key;
    this.conversation = conversation;
    await this.attach(conversation);
    const live = await this.harness.snapshot(LiveDoc, conversation.id, context);
    return { conversationId: conversation.id, busy: Boolean(live?.run) };
  }

  async abort() {
    if (this.conversation) await this.conversation.abort(context);
    return {};
  }

  async close() {
    this.closing = true;
    try { await this.watchHandle?.stop?.(); } catch {}
    try { await this.harness?.close(context); } catch (error) { log({ type: 'close_error', error: safeError(error) }); }
  }
}

const host = new Host();

async function dispatch(command) {
  const id = command?.id;
  try {
    let result = {};
    switch (command?.type) {
      case 'hello': await host.start(command); result = {}; break;
      case 'submit': result = await host.submit(command); break;
      case 'abort': result = await host.abort(); break;
      case 'configure': result = await host.configure(command); break;
      case 'models': result = await host.listModels(); break;
      case 'rotate': result = await host.rotate(command); break;
      case 'open': result = await host.openSession(command); break;
      case 'stop':
        await host.close();
        if (id !== undefined) send({ id, ok: true });
        send({ type: 'stopped' });
        setTimeout(() => process.exit(0), 10);
        return;
      default: throw new Error(`unknown durable command ${String(command?.type)}`);
    }
    if (id !== undefined) send({ id, ok: true, ...result });
  } catch (error) {
    log({ type: 'command_failed', command: command?.type, error: safeError(error) });
    if (id !== undefined) send({ id, ok: false, error: safeError(error) });
    else send({ type: 'fatal', error: safeError(error) });
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  if (!line.trim()) return;
  let command;
  try { command = JSON.parse(line); } catch {
    log({ type: 'protocol', error: 'invalid json' });
    return;
  }
  void dispatch(command);
});
rl.on('close', () => {
  void host.close().finally(() => process.exit(0));
});

process.on('uncaughtException', (error) => {
  log({ type: 'uncaught', error: safeError(error) });
  send({ type: 'fatal', error: safeError(error) });
  process.exit(1);
});
process.on('unhandledRejection', (error) => {
  log({ type: 'unhandled', error: safeError(error) });
});
