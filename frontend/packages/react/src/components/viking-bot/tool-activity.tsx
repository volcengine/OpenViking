import type { TaskSnapshot } from '../../shared/task-tracking/types.js';
import { TaskStatusList } from '../../shared/task-status/task-status.js';
import type {
  ActivityItemRenderContext,
  McpToolCallActivityItem,
} from '@ark-design/ui/activity-timeline';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import { MessageCodeBlock } from './message-code-block.js';
import { ToolResultLinks } from './tool-result-links.js';
import type { VikingBotLabels, VikingBotToolResultLink } from './model.js';

const payload = (value: unknown): { code: string; language: string } => {
  if (value === undefined || value === null)
    return { code: '', language: 'text' };
  if (typeof value !== 'string')
    return { code: JSON.stringify(value, null, 2), language: 'json' };
  try {
    return {
      code: JSON.stringify(JSON.parse(value), null, 2),
      language: 'json',
    };
  } catch {
    return { code: value, language: 'text' };
  }
};

/** Keep Ark's tool row/icon; supply the reference version's input/output details. */
export function ToolActivity({
  context,
  labels,
  onOpenUri,
  results,
  tasks,
}: {
  context: ActivityItemRenderContext<McpToolCallActivityItem>;
  labels: VikingBotLabels;
  onOpenUri?: (uri: string) => void;
  results?: readonly VikingBotToolResultLink[];
  tasks?: readonly TaskSnapshot[];
}) {
  const Disclosure = context.components.ActivityToolDisclosure;
  const input = payload(context.item.arguments);
  const output = payload(context.item.error ?? context.item.resultPreview);
  const status = context.item.status;
  const name = [context.item.server, context.item.tool]
    .filter(Boolean)
    .join('.');
  const title =
    context.item.titleOverride ??
    `${status === 'running' || status === 'queued' ? labels.toolRunning : status === 'failed' ? labels.toolError : labels.toolCompleted} ${name}`;
  const detail = (
    <div className="ov-viking-bot__tool-details">
      <TaskStatusList tasks={tasks} />
      {[
        { value: input, label: labels.toolInput },
        { value: output, label: labels.toolOutput, results },
      ]
        .filter(({ value, results }) => value.code || results?.length)
        .map(({ value, label, results }, index) => (
          <section key={index} className="ov-viking-bot__tool-payload-section">
            <div className="ov-viking-bot__tool-payload-label">{label}</div>
            {results?.length ? (
              <ToolResultLinks
                results={results}
                labels={labels}
                onOpenUri={onOpenUri}
              />
            ) : (
              <div className="ov-viking-bot__tool-payload">
                <MessageCodeBlock
                  {...value}
                  labels={labels}
                  onOpenUri={onOpenUri}
                />
              </div>
            )}
          </section>
        ))}
    </div>
  );
  return (
    <Disclosure
      id={context.item.id}
      icon={<VikingBotIcon name="tool" />}
      title={title}
      expanded={context.expanded}
      onExpandedChange={context.setExpanded}
      status={context.status}
      detail={
        input.code || output.code || results?.length || tasks?.length
          ? detail
          : undefined
      }
    />
  );
}
