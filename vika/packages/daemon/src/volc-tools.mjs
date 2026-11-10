// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import { createHash } from 'node:crypto';
import { safeError } from './logger.mjs';
import { TOOLS } from './actions/tools.mjs';

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
// MA stores root input_schema as {type,properties,required}; it omits root
// additionalProperties and empty required (verified against the live API).
// Nested schemas, including propertyNames/pattern/default, are preserved.
// Core still validates its original, stricter schema on every call.
export function maInputSchema(inputSchema) {
  const schema = structuredClone(inputSchema);
  delete schema.additionalProperties;
  if (!schema.required?.length) delete schema.required;
  return schema;
}
// The OpenViking gateway offers these itself, under the same names; a client
// tool of the same name would turn its tools off (ovViaGateway).
const GATEWAY_TOOLS = new Set(['openviking_find', 'openviking_search', 'openviking_read', 'openviking_list']);
export function maTools(actions, { viaGateway = false } = {}) {
  return actions.tools().filter(({ name }) => !(viaGateway && GATEWAY_TOOLS.has(name)))
    .map(({ name, description, inputSchema }) => ({
      type: 'custom', name, description, input_schema: maInputSchema(inputSchema),
    }));
}
// The gateway turns all of its tools off for a session whose client already
// offers an OpenViking tool of the same name (openviking_<name>). Vika keeps a
// few of its own under ovViaGateway (openviking_remember, ...), so it asks the
// gateway, per request, to hide exactly those and offer every other one.
const OV_PREFIX = 'openviking_';
export function gatewayDisabledTools(tools) {
  return [...new Set(tools.filter(({ type, name }) => type === 'custom' && name.startsWith(OV_PREFIX))
    .map(({ name }) => name.slice(OV_PREFIX.length)))].sort();
}
// X-OpenViking-Context: its disabled_tools replaces the key profile's list.
export function gatewayContextHeader(tools) {
  return JSON.stringify({ disabled_tools: gatewayDisabledTools(tools) });
}
export function maToolsHash(tools) {
  return createHash('sha256').update(canonicalJson(tools.filter((tool) => tool.type === 'custom')
    .map(({ name, description, input_schema }) => ({ name, description, input_schema }))
    .sort((a, b) => a.name.localeCompare(b.name)))).digest('hex');
}
export const MA_DEFAULT_MODEL = 'doubao-seed-2-1-turbo-260628';

// The Agent's model block (scripts/volc-sync-agent.mjs). BYOK carries no
// credential: the model key rides on each events POST (volc.mjs).
export function maAgentModel(config) {
  const id = config.volcModel || MA_DEFAULT_MODEL;
  const byok = config.volcModelByok;
  if (!byok) return { id };
  return { id, provider: byok.provider, base_url: byok.baseUrl, protocol: byok.protocol,
    token_limits: { ...byok.tokenLimits } };
}

// MA may echo more model fields than it was sent; compare only the sent ones.
function pickSent(remote, desired) {
  if (!desired || typeof desired !== 'object' || Array.isArray(desired)) return remote;
  return Object.fromEntries(Object.keys(desired).map((key) => [key, pickSent(remote?.[key], desired[key])]));
}

export function maAgentModelChanged(remoteModel, desiredModel) {
  // Rolling BYOK back: only a Direct provider has a base_url (MA may echo its
  // own provider name for a built-in model, so provider is no marker).
  if (!desiredModel.base_url && remoteModel?.base_url != null) return true;
  return canonicalJson(pickSent(remoteModel ?? {}, desiredModel)) !== canonicalJson(desiredModel);
}

export function toolError(toolUseId, code, message) {
  return { type: 'user.custom_tool_result', custom_tool_use_id: toolUseId, is_error: true,
    content: [{ type: 'text', text: JSON.stringify({ error: code, message }) }] };
}

// The journal owns tool execution, independently of SSE's ephemeral seen IDs.
export class MAToolExecutor {
  constructor({ actions, store, files }) { Object.assign(this, { actions, store, files }); }

  see(event, turn) {
    if (!event.id || !event.name || !event.input || typeof event.input !== 'object' || Array.isArray(event.input)) {
      throw new Error('Invalid MA custom_tool_use event');
    }
    const inputJson = canonicalJson(event.input);
    return this.store.seeMAToolCall({ sessionId: turn.sessionId, toolUseId: event.id,
      epoch: turn.epoch, authorityToken: turn.authorityToken, fenceId: turn.fenceId,
      name: event.name, inputJson, inputSha: createHash('sha256').update(inputJson).digest('hex') });
  }

  async execute(id, turn, stillCurrent) {
    let row = this.store.getMAToolCall(turn.sessionId, id);
    if (!row || row.fence_id !== turn.fenceId || row.epoch !== turn.epoch) throw new Error('MA tool is outside the current turn fence');
    if (row.status === 'POSTED') return null;
    if (row.status === 'DONE' || row.status === 'ABANDONED') return JSON.parse(row.result_json);
    const finish = (result, status = 'DONE', uncertain = false) => {
      this.store.updateMAToolCall(turn.sessionId, id, status, result, { uncertain });
      return result;
    };
    // Across a crash there is no atomic transaction spanning arbitrary connector,
    // timer, inbox ack and network effects. Never replay an ambiguous operation.
    // A later explicit user decision can reconcile it; a model retry cannot.
    if (row.status === 'RUNNING') {
      return finish(toolError(id, 'uncertain_not_retried',
        'Execution was interrupted after it started. Its side effects may have completed. Do not retry this action or claim failure; reconcile its outcome first.'), 'DONE', true);
    }
    if ((row.authority_token !== turn.authorityToken && !(row.status === 'SEEN' && row.preparing)) || !stillCurrent()) {
      return finish(toolError(id, 'stale_authority', 'The original execution authority expired; this call was not executed.'), 'ABANDONED');
    }
    const readOnly = TOOLS.find((tool) => tool.name === row.name)?.annotations?.readOnlyHint === true;
    if (!readOnly && this.store.maTurnUncertain(turn.sessionId, turn.fenceId, id)) {
      return finish(toolError(id, 'uncertain_not_retried',
        'An earlier operation in this turn has an uncertain outcome. All mutating tools are blocked for this turn; use read-only tools to reconcile it.'), 'DONE', true);
    }
    // PREPARING has not crossed Core's side-effect boundary. Persist it so a
    // process restart can resume a large partial download under fresh authority.
    this.store.prepareMAToolCall(turn.sessionId, id);
    try {
      const input = await this.files.prepareInput(JSON.parse(row.input_json), turn.sessionId, id);
      if (!stillCurrent()) throw Object.assign(new Error('Turn authority expired before Core execution'), { code: 'stale_authority' });
      this.store.updateMAToolCall(turn.sessionId, id, 'RUNNING');
      const result = await this.actions.call(row.name, input, { authority: turn.authorityToken,
        via: 'ma', turn: { ...turn, toolUseId: id } });
      // Persist Core success before the media bridge yields or can fail. Recovery
      // will report completion, never replay a successful side effect.
      const completed = { type: 'user.custom_tool_result', custom_tool_use_id: id, is_error: false,
        content: [{ type: 'text', text: result.media?.length
          ? JSON.stringify({ completed: true, message: 'Core completed this operation. Media transfer was interrupted; request the attachment again, do not repeat a side effect.' })
          : JSON.stringify(result) }] };
      finish(completed);
      try {
        const content = await this.files.resultContent(result, turn.sessionId, id);
        return finish({ ...completed, content });
      } catch (error) {
        return finish({ ...completed, content: [{ type: 'text', text: JSON.stringify({ completed: true,
          output_error: safeError(error), message: 'Core completed this operation. Only output transfer failed; do not repeat a side effect.' }) }] });
      }
    } catch (error) {
      return finish(toolError(id, error.code ?? 'tool_failed', safeError(error)), 'DONE', error.code === 'uncertain_not_retried');
    }
  }
}
