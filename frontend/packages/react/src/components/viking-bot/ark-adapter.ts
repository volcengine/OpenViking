import type { ActivityTimelineItem } from '@ark-design/ui/activity-timeline';

import type {
  VikingBotMessage,
  VikingBotMessagePart,
  VikingBotStreamingState,
} from './model.js';

/**
 * 把归一化片段映射成 Ark 的渲染输入。两种宿主的流事件在接入层就已经变成
 * 这里的片段，因此映射只做形态转换，不再判断来源协议。
 *
 * Maps normalized parts onto Ark's render inputs. Both hosts already turn their
 * own stream events into these parts in their integration layer, so this only
 * converts shape and never inspects a source protocol.
 */

/** 消息渲染单元的标识，供 Ark 列表做 key 与测量。 */
export interface VikingBotRenderItem {
  /** 唯一标识。Stable key. */
  id: string;
  /** 来源消息；流式单元没有对应消息。Source message; the streaming unit has none. */
  message?: VikingBotMessage;
  /** 助手仍在生成时追加的片段。Parts appended while streaming. */
  streaming?: VikingBotStreamingState;
}

/** 一轮回复的时间线条目，由推理与工具片段聚合而来。 */
export interface VikingBotActivityGroup {
  /** 执行块标识。Activity block key. */
  id: string;
  /** 条目。Items. */
  items: ActivityTimelineItem[];
}

const toStatus = (
  status: 'pending' | 'running' | 'completed' | 'error',
): 'queued' | 'running' | 'success' | 'failed' =>
  status === 'pending'
    ? 'queued'
    : status === 'running'
      ? 'running'
      : status === 'completed'
        ? 'success'
        : 'failed';

const toReasoningItem = (
  part: Extract<VikingBotMessagePart, { type: 'reasoning' }>,
  index: number,
): ActivityTimelineItem => ({
  id: `${part.groupId ?? 'reasoning'}:${index}`,
  type: 'reasoning',
  status: part.running ? 'running' : 'success',
  summary: part.text ? [part.text] : [],
  visibility: 'contentAllowed',
  durationMs: part.durationMs,
});

const toToolItem = (
  part: Extract<VikingBotMessagePart, { type: 'tool' }>,
): ActivityTimelineItem => ({
  id: part.id,
  type: 'mcpToolCall',
  status: toStatus(part.status),
  server: part.server ?? '',
  tool: part.name,
  arguments: part.input,
  resultPreview: part.output,
  durationMs: part.durationMs,
});

const isActivityPart = (part: VikingBotMessagePart) =>
  part.type === 'reasoning' || part.type === 'tool';

/**
 * 把片段聚合成执行块：带 `groupId` 的相邻推理与工具片段归为一组，
 * 其余片段原样保留，由调用方按类型分别渲染。
 */
export function groupActivityParts(parts: readonly VikingBotMessagePart[]): {
  groups: Map<string, VikingBotActivityGroup>;
  order: string[];
} {
  const groups = new Map<string, VikingBotActivityGroup>();
  const order: string[] = [];

  parts.forEach((part, index) => {
    if (!isActivityPart(part)) return;
    const key = ('groupId' in part && part.groupId) || `loose:${index}`;
    const group = groups.get(key) ?? { id: key, items: [] };
    if (!groups.has(key)) {
      groups.set(key, group);
      order.push(key);
    }
    group.items.push(
      part.type === 'reasoning'
        ? toReasoningItem(part, index)
        : toToolItem(part),
    );
  });

  return { groups, order };
}

/**
 * 把消息列表展平成 Ark 列表的渲染单元，流式片段插到 `insertIndex` 指定位置。
 *
 * 插入位置在原始消息序列上确定：调用方先按原始下标切分，再分别合并两侧，
 * 避免先合并再适配导致下标含义改变、把边界两侧的助手消息并到一起。
 *
 * Flattens the messages into Ark render items, inserting the streaming unit at
 * `insertIndex`. The boundary is resolved against the original sequence so
 * merging on either side cannot shift its meaning.
 */
export function toRenderItems(
  messages: readonly VikingBotMessage[],
  streaming?: VikingBotStreamingState,
  group: (messages: readonly VikingBotMessage[]) => VikingBotMessage[] = (
    list,
  ) => [...list],
): VikingBotRenderItem[] {
  const insertIndex = streaming
    ? Math.min(
        Math.max(streaming.insertIndex ?? messages.length, 0),
        messages.length,
      )
    : messages.length;
  const before = group(messages.slice(0, insertIndex));
  const after = streaming ? group(messages.slice(insertIndex)) : [];
  const items: VikingBotRenderItem[] = before.map((message) => ({
    id: message.id,
    message,
  }));
  if (streaming) {
    items.push({ id: '__streaming__', streaming });
  }
  for (const message of after) {
    items.push({ id: message.id, message });
  }
  return items;
}
