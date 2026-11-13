import type { VikingBotMessagePart } from '../../../components/viking-bot/model.js';
import type { VikingBotChatEvent } from './types.js';

/** Per-request accumulator: stable groups, ordered text, delta/snapshot semantics. */
export function createChatAccumulator(turnId: string) {
  let parts: VikingBotMessagePart[] = [];
  let iteration = 0;
  let group = 0;
  let groupId: string | undefined;
  const reasoningIndices = new Map<string, number>();
  const activityGroup = () => (groupId ??= `${turnId}:activity:${group++}`);
  const finishReasoning = () => {
    parts = parts.map((part) =>
      part.type === 'reasoning' && part.running
        ? { ...part, running: false, completedAt: Date.now() }
        : part,
    );
  };
  return {
    apply(event: VikingBotChatEvent) {
      const last = parts.at(-1);
      if (event.type === 'iteration') {
        finishReasoning();
        iteration = event.iteration;
        // Iterations are protocol metadata; continuous activity stays in one block.
      } else if (event.type === 'reasoning') {
        const index = event.id
          ? reasoningIndices.get(event.id)
          : last?.type === 'reasoning' && last.running
            ? parts.length - 1
            : undefined;
        const previous = index === undefined ? undefined : parts[index];
        if (previous?.type === 'reasoning') {
          parts = parts.map((part, offset) =>
            offset === index
              ? {
                  ...previous,
                  text: event.delta ? previous.text + event.text : event.text,
                }
              : part,
          );
        } else {
          finishReasoning();
          if (event.id) reasoningIndices.set(event.id, parts.length);
          parts = [
            ...parts,
            {
              type: 'reasoning',
              text: event.text,
              running: true,
              groupId: activityGroup(),
              startedAt: Date.now(),
            },
          ];
        }
      } else if (event.type === 'tool') {
        finishReasoning();
        const index = parts.findIndex(
          (part) => part.type === 'tool' && part.id === event.part.id,
        );
        const previous = parts[index];
        const item = {
          ...event.part,
          groupId:
            previous && 'groupId' in previous
              ? previous.groupId
              : activityGroup(),
          startedAt:
            event.part.startedAt ??
            (previous && 'startedAt' in previous
              ? previous.startedAt
              : undefined) ??
            Date.now(),
        };
        parts =
          index < 0
            ? [...parts, item]
            : parts.map((part, offset) =>
                offset === index ? { ...part, ...item } : part,
              );
      } else if (event.type === 'tool-result') {
        finishReasoning();
        parts = parts.map((part) =>
          part.type === 'tool' && part.id === event.id
            ? {
                ...part,
                output: event.output,
                status: event.isError ? 'error' : 'completed',
                completedAt: Date.now(),
              }
            : part,
        );
      } else if (event.type === 'text' || event.type === 'complete') {
        finishReasoning();
        groupId = undefined;
        if (event.text !== undefined) {
          const delta = event.type === 'text' && event.delta;
          if (last?.type === 'text') {
            parts = [
              ...parts.slice(0, -1),
              { ...last, text: delta ? last.text + event.text : event.text },
            ];
          } else if (event.text)
            parts = [...parts, { type: 'text', text: event.text }];
        }
      }
    },
    snapshot() {
      return { parts, iteration };
    },
    finish() {
      finishReasoning();
      parts = parts.map((part) =>
        part.type === 'tool' &&
        (part.status === 'running' || part.status === 'pending')
          ? { ...part, status: 'error' }
          : part,
      );
      return parts;
    },
  };
}
