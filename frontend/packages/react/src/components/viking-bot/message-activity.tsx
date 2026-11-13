import type { TaskSnapshot } from '../../shared/task-tracking/types.js';
import { useEffect, useMemo, useState } from 'react';
import { ActivityTimeline } from '@ark-design/ui/activity-timeline';
import type {
  ActivityItemRenderContext,
  McpToolCallActivityItem,
} from '@ark-design/ui/activity-timeline';
import { groupActivityParts } from './ark-adapter.js';
import { ToolActivity } from './tool-activity.js';
import type {
  VikingBotLabels,
  VikingBotMessagePart,
  VikingBotToolResultLink,
} from './model.js';

const timestamp = (value?: number | string) =>
  value === undefined
    ? NaN
    : typeof value === 'number'
      ? value
      : new Date(value).getTime();

/** Ark owns the disclosure, completion collapse and execution status header. */
export function MessageActivity({
  parts,
  labels,
  onOpenUri,
}: {
  parts: readonly VikingBotMessagePart[];
  labels: VikingBotLabels;
  onOpenUri?: (uri: string) => void;
}) {
  const { groups, order } = groupActivityParts(parts);
  const resultLinks = useMemo(() => {
    const links = new Map<string, readonly VikingBotToolResultLink[]>();
    for (const part of parts) {
      if (
        (part.type === 'tool' || part.type === 'tool_result') &&
        part.results !== undefined
      )
        links.set(part.id, part.results);
    }
    return links;
  }, [parts]);
  const taskSnapshots = useMemo(() => {
    const snapshots = new Map<string, readonly TaskSnapshot[]>();
    for (const part of parts) {
      if ((part.type === 'tool' || part.type === 'tool_result') && part.tasks)
        snapshots.set(part.id, part.tasks);
    }
    return snapshots;
  }, [parts]);
  const items = order.flatMap((id) => groups.get(id)?.items ?? []);
  const running = items.some(
    (item) => item.status === 'running' || item.status === 'queued',
  );
  const [now, setNow] = useState(() => Date.now());
  // Effect necessity: the wall clock advances while a streamed activity is
  // running, without user events; unsubscribe when complete or unmounted.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const starts = parts.flatMap((part) =>
    'startedAt' in part
      ? [timestamp(part.startedAt)].filter(Number.isFinite)
      : [],
  );
  const ends = parts.flatMap((part) =>
    'completedAt' in part
      ? [timestamp(part.completedAt)].filter(Number.isFinite)
      : [],
  );
  const elapsed =
    starts.length && (running || ends.length)
      ? Math.max(0, (running ? now : Math.max(...ends)) - Math.min(...starts))
      : items.reduce((total, item) => total + (item.durationMs ?? 0), 0);
  const renderers = useMemo(
    () => ({
      mcpToolCall: (context: ActivityItemRenderContext) => (
        <ToolActivity
          context={
            context as ActivityItemRenderContext<McpToolCallActivityItem>
          }
          labels={labels}
          onOpenUri={onOpenUri}
          results={resultLinks.get(context.item.id)}
          tasks={taskSnapshots.get(context.item.id)}
        />
      ),
    }),
    [labels, onOpenUri, resultLinks, taskSnapshots],
  );
  return (
    <ActivityTimeline
      className="ov-viking-bot__activity"
      items={items.map((item) => ({
        ...item,
        durationMs:
          item.durationMs !== undefined && item.durationMs >= 1000
            ? item.durationMs
            : undefined,
      }))}
      defaultCollapseCompleted
      density="compact"
      durationMs={
        elapsed >= 1000 ? Math.floor(elapsed / 1000) * 1000 : undefined
      }
      statusLabel={running ? labels.activityRunning : labels.activityCompleted}
      renderers={renderers}
      onOpenSearchResult={(result) => onOpenUri?.(result.url)}
    />
  );
}
