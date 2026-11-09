// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import http from 'node:http';

// A local provider endpoint for tests that need a route on the wire:
// `/v1/chat/completions` (OpenAI-shaped, SSE by default, JSON when `stream:
// false`), `/v1/messages` (DeepSeek Messages — Anthropic-shaped — SSE), and
// `/v1/models`. It answers on 127.0.0.1:0 so a test never binds a fixed port,
// records every request for assertions, and can hold a stream open so a test
// cancels a live turn deterministically.
//
// The two completion routes exist because DSH drives two protocols: a settings
// route declared `openai-completions` posts to `/chat/completions`, while the
// built-in `deepseek-official` route posts to `<baseURL>/v1/messages`. The
// reply in both cases is deliberately valid wire data rather than a DSH-shaped
// stub: a runtime adapter that rejects the real protocol is the bug this
// fixture should expose.

function sseChunk(payload) {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function splitReply(reply, size = 8) {
  if (reply.length === 0) return [];
  const pieces = [];
  for (let index = 0; index < reply.length; index += size) pieces.push(reply.slice(index, index + size));
  return pieces;
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

/**
 * Start a fake OpenAI-compatible provider.
 * @param options.reply - assistant text for the next completion.
 * @param options.deltas - explicit content deltas; defaults to slices of `reply`.
 * @param options.delayMs - wait before the first frame, so a cancel can race it.
 * @param options.chunkDelayMs - wait between content frames.
 * @param options.status - non-200 answers an error body in the route's own
 *   protocol instead of a stream.
 * @param options.models - ids the `/v1/models` listing advertises.
 * @param options.hold - keep each stream open until `release()` is called.
 * @param options.finishReason - finish_reason of the final content frame;
 *   `length` answers `max_tokens` on the Messages route and `length` on the
 *   completions route.
 * @returns the endpoint (`baseURL` includes `/v1`), the recorded requests, and
 *   `close()` / `release()` controls. `close()` is idempotent.
 */
export async function startFakeProvider({
  reply = 'Hello from the fake provider.',
  deltas = null,
  delayMs = 0,
  chunkDelayMs = 0,
  status = 200,
  models = ['fake-model'],
  hold = false,
  finishReason = 'stop',
} = {}) {
  const requests = [];
  const sockets = new Set();
  const held = [];
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    const raw = await readBody(request);
    let body = null;
    try { body = raw ? JSON.parse(raw) : null; } catch { body = null; }
    requests.push({ method: request.method, path: url.pathname, headers: { ...request.headers }, body, raw });

    if (request.method === 'GET' && url.pathname.endsWith('/models')) {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(`${JSON.stringify({
        object: 'list',
        data: models.map((id, index) => ({ id, object: 'model', created: 1_700_000_000 + index, owned_by: 'fake' })),
      })}\n`);
      return;
    }

    const isCompletions = request.method === 'POST' && url.pathname.endsWith('/chat/completions');
    // The built-in `deepseek-official` route posts to `<baseURL>/v1/messages`
    // and parses Anthropic-shaped events; it never calls the completions route.
    const isMessages = request.method === 'POST' && url.pathname.endsWith('/messages');

    if (!isCompletions && !isMessages) {
      response.writeHead(404, { 'content-type': 'application/json' });
      response.end(`${JSON.stringify({ error: { message: `no route for ${request.method} ${url.pathname}`, type: 'invalid_request_error' } })}\n`);
      return;
    }

    if (status !== 200) {
      response.writeHead(status, { 'content-type': 'application/json' });
      response.end(`${JSON.stringify(isMessages
        ? { type: 'error', error: { type: 'api_error', message: `fake provider status ${status}` } }
        : { error: { message: `fake provider status ${status}`, type: 'invalid_request_error' } })}\n`);
      return;
    }

    const model = typeof body?.model === 'string' && body.model ? body.model : models[0];

    if (isMessages) {
      // Anthropic-shaped SSE: typed data events and no `[DONE]` sentinel. DSH
      // rejects a frame whose `event:` name disagrees with its `type`, so these
      // frames carry only the data line. The content comes from the same
      // `reply`/`deltas` as the completions route, so a test asserts either
      // protocol the same way.
      response.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      });
      const write = (payload) => { if (!response.writableEnded) response.write(sseChunk(payload)); };
      const chunks = Array.isArray(deltas) ? [...deltas] : splitReply(reply);
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
      write({
        type: 'message_start',
        message: {
          id: 'msg-fake', type: 'message', role: 'assistant', model, content: [],
          stop_reason: null, stop_sequence: null, usage: { input_tokens: 7, output_tokens: 0 },
        },
      });
      write({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } });
      if (hold) {
        // Same hold contract as the completions stream: the test decides when
        // the reply ends, so a cancel can race a live turn without a sleep.
        await new Promise((resolve) => held.push(resolve));
      }
      for (const delta of chunks) {
        if (chunkDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, chunkDelayMs));
        write({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: delta } });
      }
      write({ type: 'content_block_stop', index: 0 });
      write({
        type: 'message_delta',
        delta: { stop_reason: finishReason === 'length' ? 'max_tokens' : 'end_turn', stop_sequence: null },
        usage: { output_tokens: 5 },
      });
      write({ type: 'message_stop' });
      response.end();
      return;
    }

    const completionId = 'chatcmpl-fake';
    const created = 1_700_000_000;
    const usage = { prompt_tokens: 7, completion_tokens: 5, total_tokens: 12 };

    if (body?.stream === false) {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(`${JSON.stringify({
        id: completionId, object: 'chat.completion', created, model,
        choices: [{ index: 0, message: { role: 'assistant', content: reply }, finish_reason: finishReason }],
        usage,
      })}\n`);
      return;
    }

    response.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    });
    const write = (payload) => { if (!response.writableEnded) response.write(sseChunk(payload)); };
    const chunks = Array.isArray(deltas) ? [...deltas] : splitReply(reply);
    if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
    write({ id: completionId, object: 'chat.completion.chunk', created, model, choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] });
    if (hold) {
      // A response object ends the stream only once the test says so, which is
      // how a cancel test keeps a turn live without a network sleep.
      await new Promise((resolve) => held.push(resolve));
    }
    for (const delta of chunks) {
      if (chunkDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, chunkDelayMs));
      write({ id: completionId, object: 'chat.completion.chunk', created, model, choices: [{ index: 0, delta: { content: delta }, finish_reason: null }] });
    }
    write({ id: completionId, object: 'chat.completion.chunk', created, model, choices: [{ index: 0, delta: {}, finish_reason: finishReason }], usage });
    response.write('data: [DONE]\n\n');
    response.end();
  });
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();
  let closed = false;
  return {
    server,
    port,
    /** `http://127.0.0.1:<port>/v1` — the baseURL a provider profile expects. */
    baseURL: `http://127.0.0.1:${port}/v1`,
    requests,
    /** Resolve every held stream so a test can end a turn it kept open. */
    release() {
      for (const resolve of held.splice(0)) resolve();
    },
    /** Stop listening and drop open streams; safe to call more than once. */
    close() {
      if (closed) return Promise.resolve();
      closed = true;
      for (const resolve of held.splice(0)) resolve();
      return new Promise((resolve) => {
        server.close(() => resolve());
        for (const socket of sockets) socket.destroy();
        sockets.clear();
      });
    },
  };
}
