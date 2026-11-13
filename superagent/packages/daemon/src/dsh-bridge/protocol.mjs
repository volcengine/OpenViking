// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { PassThrough } from 'node:stream';
import { DshBridgeFramer, MAX_FRAME_BYTES } from './framer.mjs';
import { BRIDGE_ID, bridgeErrorFrame, createBridgeHandlers } from './handlers.mjs';
import { createWorkerHandlers } from './workers.mjs';

export { DshBridgeError, NOT_READY_CODE, createBridgeHandlers } from './handlers.mjs';
export { createWorkerHandlers, DSH_WORKER_METHODS, DSH_WORKER_PRESET } from './workers.mjs';
export { DshBridgeFramer, MAX_FRAME_BYTES } from './framer.mjs';

const LF_BYTES = Buffer.from('\n');

// JSON-RPC params are an object when present. Anything else is the caller's
// malformed request, not an internal failure, so it is answered with -32602.
function requestParams(params) {
  if (params === undefined) return {};
  if (!params || typeof params !== 'object' || Array.isArray(params)) {
    const error = new Error('params must be an object');
    error.code = -32602;
    throw error;
  }
  return params;
}

/**
 * Replace the stock SDK transport's stdin with the bridge, then hand the
 * pass-through stream to the stock server. Requests whose method the handlers
 * own are answered here; everything else is forwarded byte-for-byte.
 *
 * A v2 bridge additionally exposes the model catalogue and selection, the
 * `llm-pi-ai` settings document, and credential writes under the `superagent/`
 * namespace. Those method names deliberately do not shadow DSH's own SDK
 * methods, so a future upstream implementation cannot be intercepted by
 * accident.
 */
export function wireDshBridge({ ctx, stock, createUserMessage, scopeParentOf, config = {}, input = process.stdin, output = process.stdout }) {
  const workerHandlers = createWorkerHandlers({ ctx, agents: ctx.agents, createUserMessage, scopeParentOf });
  const handlers = createBridgeHandlers({ ctx, agents: ctx.agents, createUserMessage, workerHandlers });
  // Derived, never hand-listed: the advertised methods and the routed methods
  // are one set by construction.
  const HANDLED = new Set(Object.keys(handlers));
  const forwarded = new PassThrough();
  const pending = new Set();

  // stdout carries the protocol only. A response that cannot be serialized is
  // still answered as a JSON-RPC frame, and a write failure is logged to stderr.
  const writeFrame = frame => {
    let text = null;
    try { text = `${JSON.stringify(frame)}\n`; } catch {}
    if (text === null) {
      try {
        text = `${JSON.stringify({ jsonrpc: '2.0', id: frame.id, error: {
          code: -32603, message: 'the response could not be serialized',
        } })}\n`;
      } catch {}
    }
    if (text === null) {
      console.error(`[${BRIDGE_ID}] response could not be serialized`);
      return;
    }
    try {
      output.write(text);
    } catch (error) {
      console.error(`[${BRIDGE_ID}] response write failed`, error);
    }
  };
  const respond = (id, { result, error }) => {
    if (error) writeFrame({ jsonrpc: '2.0', id, error });
    else writeFrame({ jsonrpc: '2.0', id, result: result === undefined ? null : result });
  };
  const forward = line => {
    if (forwarded.writableEnded || forwarded.destroyed) return;
    try {
      forwarded.write(Buffer.concat([line, LF_BYTES]));
    } catch (error) {
      console.error(`[${BRIDGE_ID}] forward failed`, error);
    }
  };

  const framer = new DshBridgeFramer({
    onFrame({ text, line }) {
      let frame;
      try { frame = JSON.parse(text); } catch {
        // Preserve the stock transport's treatment of malformed input.
        forward(line);
        return;
      }
      if (!frame || typeof frame !== 'object' || Array.isArray(frame)
        || typeof frame.method !== 'string' || !HANDLED.has(frame.method)
        || (typeof frame.id !== 'string' && typeof frame.id !== 'number')) {
        // Not ours, or not a request: the stock transport decides. It ignores
        // frames without a usable id, so nothing here is answered twice.
        forward(line);
        return;
      }
      const task = (async () => {
        try {
          const result = await handlers[frame.method](requestParams(frame.params));
          respond(frame.id, { result });
        } catch (error) {
          respond(frame.id, { error: bridgeErrorFrame(error, frame.method) });
        }
      })().catch(error => {
        // A failed serialization is a transport failure, not a reason to write
        // non-protocol logging to stdout.
        console.error(`[${BRIDGE_ID}] response failed`, error);
      }).finally(() => pending.delete(task));
      pending.add(task);
    },
    onOversized(prefix) {
      console.error(`[${BRIDGE_ID}] dropped a request frame over ${MAX_FRAME_BYTES} bytes: ${prefix}`);
    },
  });

  let finished = false;
  const finishInput = () => {
    if (finished) return;
    finished = true;
    framer.end();
    forwarded.end();
  };
  input.on('data', chunk => framer.push(chunk));
  input.once('end', finishInput);
  input.once('close', finishInput);
  input.once('error', error => {
    console.error(`[${BRIDGE_ID}] input failed`, error);
    finishInput();
  });
  // A stdin that ended before the bridge was wired will never emit 'end'; the
  // stock server must still see EOF or it waits for input forever.
  if (input.readableEnded || input.destroyed) finishInput();
  return stock.apply(ctx, { ...config, input: forwarded, output });
}
