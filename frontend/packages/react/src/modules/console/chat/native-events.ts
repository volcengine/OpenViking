import type { OvStreamEvent } from '../../../client/types.js';
import { OvClientError } from '../../../client/errors.js';
import type { VikingBotChatEvent } from './types.js';

const textOf = (data: unknown): string => {
  if (typeof data === 'string') return data;
  if (data == null) return '';
  if (typeof data === 'object') {
    const value = data as Record<string, unknown>;
    for (const key of ['content', 'message'])
      if (typeof value[key] === 'string') return value[key];
  }
  return JSON.stringify(data);
};

/** SSE framing stays in Client; this only interprets the native Bot protocol. */
export async function* nativeChatEvents(
  source: AsyncIterable<OvStreamEvent>,
): AsyncGenerator<VikingBotChatEvent> {
  const pending: { id: string }[] = [];
  let sequence = 0;
  let iteration = 0;
  let reasoningHadDelta = false;
  for await (const frame of source) {
    if (!frame.data || frame.data === '[DONE]') continue;
    let event: { event?: string; data?: unknown };
    try {
      const parsed: unknown = JSON.parse(frame.data);
      if (!parsed || typeof parsed !== 'object') throw new Error();
      event = parsed;
    } catch {
      throw new OvClientError({
        code: 'BOT_INVALID_EVENT',
        message: 'Invalid VikingBot stream event',
      });
    }
    const data = event.data;
    if (
      event.event === 'response' &&
      data &&
      typeof data === 'object' &&
      'error' in data
    ) {
      throw new Error(textOf(data.error));
    }
    const text = textOf(data);
    if (event.event === 'error') throw new Error(text);
    if (event.event === 'reasoning_delta' || event.event === 'reasoning') {
      if (event.event === 'reasoning' && reasoningHadDelta) continue;
      if (event.event === 'reasoning_delta') reasoningHadDelta = true;
      yield {
        type: 'reasoning',
        text,
        delta: event.event === 'reasoning_delta',
      };
    } else if (event.event === 'content_delta') {
      reasoningHadDelta = false;
      yield { type: 'text', text, delta: true };
    } else if (event.event === 'iteration') {
      reasoningHadDelta = false;
      iteration = Number(text.match(/\d+/)?.[0] ?? iteration);
      yield { type: 'iteration', iteration };
    } else if (event.event === 'tool_call') {
      reasoningHadDelta = false;
      const opening = text.indexOf('(');
      const name = opening > 0 ? text.slice(0, opening) : text;
      const raw = opening > 0 ? text.slice(opening + 1, -1) : '';
      const id = `native-tool-${sequence++}`;
      let input: Record<string, unknown> | undefined;
      try {
        input = raw ? (JSON.parse(raw) as Record<string, unknown>) : undefined;
      } catch {
        input = { raw };
      }
      pending.push({ id });
      yield {
        type: 'tool',
        part: { type: 'tool', id, name, input, status: 'running' },
      };
    } else if (event.event === 'tool_result') {
      const call = pending.shift();
      if (call)
        yield {
          type: 'tool-result',
          id: call.id,
          output: text,
          isError:
            /^error\b/i.test(text) ||
            Boolean(
              data && typeof data === 'object' && 'error' in data && data.error,
            ),
        };
    } else if (event.event === 'interrupt') {
      yield { type: 'interrupted' };
      return;
    } else if (event.event === 'response') {
      yield { type: 'complete', text };
      return;
    }
  }
}
