// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// A real dsh turn proving a model switch reaches the provider.

import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import {
  cleanupLiveDshRoot, hermeticDshEnv, prepareLiveDshRoot, realDshAvailability, startLiveDsh,
} from './real-dsh.mjs';
import { startFakeProvider } from './fake-provider.mjs';

// End-to-end proof of the design's switch contract (§4.2): `select` records the
// requested pair, DSH applies it at the next model step, and the process that
// answered the previous turn is the one that answers the next.
//
// A stub cannot show that — `agent.model.select` is a DSH-internal durable
// event — so this suite drives a real child over the bridge and asserts on what
// reached the provider socket. Each turn's request body is the only honest
// evidence that the switch ran: the daemon's `appliedModel` bookkeeping is not.
//
// The built-in `deepseek-official` route speaks the DeepSeek Messages protocol
// (Anthropic-shaped) at `${DEEPSEEK_BASE_URL}/v1/messages`. DEEPSEEK_BASE_URL is
// the documented override for a built-in route's endpoint, so the fixture points
// it at the fake provider and passes a placeholder key — the only credential the
// route ever sees. Nothing here reaches the network, ~/.dsh, or ~/.ovsa: the
// temp root owns HOME and DSH_HOME, and `hermeticDshEnv` strips operator values.
//
// The suite is a scenario, not independent cases: each test starts the next
// turn from the selection the previous one left. The order is the subject.

const availability = await realDshAvailability();
const liveSkip = availability.ok ? false : availability.reason;

const API_KEY = 'vika-dsh-turn-placeholder-key';
const INITIAL_MODEL = 'deepseek-flash';
const SWITCHED_MODEL = 'deepseek-v4-pro';

/** Every model request DSH made to the Messages route, in arrival order. */
function modelRequests(provider) {
  return provider.requests.filter((request) => request.method === 'POST' && request.path.endsWith('/messages'));
}

test('the fake provider answers the DeepSeek Messages protocol with Anthropic SSE', async (t) => {
  const provider = await startFakeProvider({ reply: 'messages reply' });
  t.after(() => provider.close());
  const response = await fetch(`${provider.baseURL}/messages`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-api-key': 'test-key' },
    body: JSON.stringify({
      model: 'deepseek-flash', stream: true, max_tokens: 32,
      thinking: { type: 'enabled' }, messages: [{ role: 'user', content: 'hi' }],
    }),
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type') ?? '', /text\/event-stream/);
  const text = await response.text();
  // A `[DONE]` sentinel is an OpenAI-completions detail; the Messages adapter
  // ends on `message_stop` and would treat a stray frame as provider data.
  assert.doesNotMatch(text, /\[DONE\]/);
  const frames = text.split('\n\n')
    .filter((frame) => frame.startsWith('data: '))
    .map((frame) => JSON.parse(frame.slice('data: '.length)));
  assert.deepEqual(frames.map((frame) => frame.type)[0], 'message_start');
  assert.equal(frames.at(-1).type, 'message_stop');
  assert.equal(frames[0].message.model, 'deepseek-flash');
  assert.equal(frames[0].message.role, 'assistant');
  const reply = frames
    .filter((frame) => frame.type === 'content_block_delta')
    .map((frame) => frame.delta.text)
    .join('');
  assert.equal(reply, 'messages reply');
  const settled = frames.find((frame) => frame.type === 'message_delta');
  assert.equal(settled.delta.stop_reason, 'end_turn');
  assert.equal(provider.requests.length, 1);
  assert.equal(provider.requests[0].body.model, 'deepseek-flash');
});

describe('live dsh applies a model switch on the next turn without restarting', { skip: liveSkip }, () => {
  /** @type {ReturnType<typeof prepareLiveDshRoot>} */
  let root;
  /** @type {Awaited<ReturnType<typeof startLiveDsh>>} */
  let dsh;
  /** @type {Awaited<ReturnType<typeof startFakeProvider>>} */
  let provider;
  let sessionId;

  before(async () => {
    provider = await startFakeProvider({ reply: 'turn reply from the fake provider' });
    root = prepareLiveDshRoot({ prefix: 'vika-dsh-turn-' });
    dsh = await startLiveDsh({
      root,
      env: hermeticDshEnv({ root, apiKey: API_KEY, baseURL: provider.baseURL }),
      policy: 'ovsa dsh model switch e2e',
    });
    await dsh.initialize();
    sessionId = randomUUID();
    // An unknown id creates the session; this is the handle the steer and the
    // select both address.
    await dsh.rpc('session/working-directory/get', { sessionId });
  });

  after(async () => {
    const stderr = dsh?.stderr() ?? '';
    await Promise.allSettled([dsh?.stop(), provider?.close()]);
    cleanupLiveDshRoot(root?.root);
    assert.doesNotMatch(stderr, /did not activate|unmatched|not matched/i, `dsh stderr: ${stderr}`);
  });

  /**
   * Run one prompt to completion and return the single model request it made.
   * A plain-text reply ends the turn in one model step, so a second request
   * would mean the fixture is measuring something other than one turn.
   */
  async function turn(text) {
    const before = modelRequests(provider).length;
    await dsh.rpc('session/steer', { sessionId, contentBlocks: [{ type: 'text', text }] });
    await dsh.rpc('session/wait', { sessionId });
    const requests = modelRequests(provider);
    assert.equal(requests.length, before + 1,
      `one turn must issue exactly one model request; stderr: ${dsh.stderr().slice(-2000)}`);
    return requests.at(-1);
  }

  test('the first turn sends the initialized model and effort to the route', async () => {
    const request = await turn('first turn');
    assert.equal(request.path, '/v1/messages');
    assert.equal(request.body.stream, true);
    assert.equal(request.body.model, INITIAL_MODEL);
    // `initialize` carried reasoningEffort high, which is the adapter default
    // too: this frame is the baseline the later switches are measured against.
    assert.deepEqual(request.body.thinking, { type: 'enabled' });
    assert.deepEqual(request.body.output_config, { effort: 'high' });
    assert.equal(request.headers['x-api-key'], API_KEY,
      'the placeholder key must reach the route instead of a real credential');
  });

  test('a select without an effort switches the next turn and keeps the same process', async () => {
    const pid = dsh.pid;
    // No `reasoningEffort`: the bridge records the request as made (null), it
    // does not materialize the adapter default into the user's selection.
    assert.deepEqual(
      await dsh.rpc('vika/session/model/select', { sessionId, provider: 'deepseek-official', model: SWITCHED_MODEL }),
      { provider: 'deepseek-official', model: SWITCHED_MODEL, reasoningEffort: null },
    );

    const request = await turn('second turn');
    assert.equal(request.body.model, SWITCHED_MODEL,
      'the turn after select must run the newly selected model');
    // The effort is still the adapter default, not a value pinned by select.
    assert.deepEqual(request.body.thinking, { type: 'enabled' });
    assert.deepEqual(request.body.output_config, { effort: 'high' });

    assert.equal(dsh.pid, pid, 'a model switch must not restart the dsh process');
    assert.equal(dsh.exitInfo(), null);
  });

  test('an explicit effort governs the next turn and clearing it restores the default', async () => {
    assert.deepEqual(
      await dsh.rpc('vika/session/model/select', {
        sessionId, provider: 'deepseek-official', model: SWITCHED_MODEL, reasoningEffort: 'low',
      }),
      { provider: 'deepseek-official', model: SWITCHED_MODEL, reasoningEffort: 'low' },
    );
    const custom = await turn('low effort turn');
    assert.equal(custom.body.model, SWITCHED_MODEL);
    assert.deepEqual(custom.body.output_config, { effort: 'low' },
      'an explicit effort must reach the provider as the request effort');

    assert.deepEqual(
      await dsh.rpc('vika/session/model/select', { sessionId, provider: 'deepseek-official', model: SWITCHED_MODEL }),
      { provider: 'deepseek-official', model: SWITCHED_MODEL, reasoningEffort: null },
    );
    const cleared = await turn('cleared effort turn');
    assert.equal(cleared.body.model, SWITCHED_MODEL);
    assert.deepEqual(cleared.body.output_config, { effort: 'high' },
      'an omitted effort must not keep the previously explicit effort pinned');
  });

  test('an off effort disables thinking on the wire', async () => {
    assert.deepEqual(
      await dsh.rpc('vika/session/model/select', {
        sessionId, provider: 'deepseek-official', model: SWITCHED_MODEL, reasoningEffort: 'off',
      }),
      { provider: 'deepseek-official', model: SWITCHED_MODEL, reasoningEffort: 'off' },
    );
    const request = await turn('off effort turn');
    assert.equal(request.body.model, SWITCHED_MODEL);
    assert.deepEqual(request.body.thinking, { type: 'disabled' });
    assert.equal(request.body.output_config, undefined, 'off carries no per-request effort');
  });

  test('a rejected effort leaves the running selection untouched', async () => {
    const pid = dsh.pid;
    const rejected = await dsh.rpcRaw('vika/session/model/select', {
      sessionId, provider: 'deepseek-official', model: INITIAL_MODEL, reasoningEffort: 'xhigh',
    });
    // The transport keeps JSON-RPC -32603; the failure class rides in data.code
    // so a caller routes on it instead of matching prose.
    assert.equal(rejected.error?.code, -32603, JSON.stringify(rejected.error));
    assert.equal(rejected.error?.data?.code, 'UNSUPPORTED_REASONING_EFFORT', JSON.stringify(rejected.error));
    assert.match(rejected.error?.message ?? '', /does not support reasoning effort "xhigh"/);

    // A refused select is not a queue: the session keeps the model and the off
    // effort the previous step applied, and the process is still the same one.
    const request = await turn('after the rejected select');
    assert.equal(request.body.model, SWITCHED_MODEL, 'a refused select must not move the session');
    assert.deepEqual(request.body.thinking, { type: 'disabled' },
      'the applied off effort must survive a refused select');
    assert.equal(request.body.output_config, undefined);
    assert.equal(dsh.pid, pid, 'a refused select must not restart the dsh process');
  });
});
