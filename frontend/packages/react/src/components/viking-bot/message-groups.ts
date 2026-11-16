import type { VikingBotMessage, VikingBotMessagePart } from './model.js';

const URI_RE = /viking:\/\/[^\s<>"'`)\]]+/gu;

/** 把 `viking://` 地址从片段里抽出来，用于渲染可点击引用。 */
export function collectUris(parts: readonly VikingBotMessagePart[]): string[] {
  const found = new Set<string>();
  for (const part of parts) {
    if (part.type === 'context') {
      found.add(part.uri);
      continue;
    }
    if (part.type === 'tool' || part.type === 'tool_result') {
      const text =
        part.type === 'tool' ? (part.output ?? '') : (part.output ?? '');
      for (const match of text.match(URI_RE) ?? []) found.add(match);
      continue;
    }
    if (part.type === 'text') {
      for (const match of part.text.match(URI_RE) ?? []) found.add(match);
    }
  }
  return [...found];
}

/** 把连续消息按轮次合并，便于渲染同一轮的助手步骤。 */
export function groupMessages(
  messages: readonly VikingBotMessage[],
): VikingBotMessage[] {
  const groups: VikingBotMessage[] = [];
  for (const message of messages) {
    const previous = groups.at(-1);
    const sameTurn =
      previous !== undefined &&
      message.role === 'assistant' &&
      previous.role === 'assistant' &&
      (!previous.turnId ||
        !message.turnId ||
        previous.turnId === message.turnId);
    if (sameTurn && previous) {
      previous.parts = [...previous.parts, ...message.parts];
      previous.turnId ??= message.turnId;
      previous.streaming = previous.streaming || message.streaming;
      previous.createdAt = message.createdAt ?? previous.createdAt;
      // 动作与原始消息身份属于各自的消息，不能随合并转移：带动作的后继消息
      // 若被并进前一条，动作会挂到错误的 messageId 上或直接丢失。
      if (message.action) {
        previous.actions = [
          ...(previous.actions ?? []),
          { messageId: message.id, action: message.action },
        ];
      }
      continue;
    }
    groups.push({
      ...message,
      parts: [...message.parts],
      actions: message.action
        ? [{ messageId: message.id, action: message.action }]
        : undefined,
    });
  }
  return groups;
}

/** 取消息的纯文本，用于复制。Plain text of a message, used for copying. */
export function messageText(message: VikingBotMessage): string {
  if (message.answerSummary)
    return message.answerSummary
      .map((row) => `${row.label}: ${row.value}`)
      .join('\n');
  return message.parts
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('\n')
    .trim();
}

/** 同一执行块的片段归到一组，供折叠块使用。Groups parts of one activity block. */
export function groupByActivity(
  parts: readonly VikingBotMessagePart[],
): { groupId?: string; parts: VikingBotMessagePart[] }[] {
  const blocks: { groupId?: string; parts: VikingBotMessagePart[] }[] = [];
  for (const part of parts) {
    const groupId = 'groupId' in part ? part.groupId : undefined;
    const previous = blocks.at(-1);
    const activity = part.type === 'reasoning' || part.type === 'tool';
    const previousType = previous?.parts[0]?.type;
    const previousActivity =
      previousType === 'reasoning' || previousType === 'tool';
    if (
      previous &&
      groupId &&
      previous.groupId === groupId &&
      activity === previousActivity
    ) {
      previous.parts.push(part);
      continue;
    }
    blocks.push({ groupId, parts: [part] });
  }
  return blocks;
}
