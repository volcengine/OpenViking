import type {
  ActivityItemRenderContext,
  ReasoningActivityItem,
} from '@ark-design/ui/activity-timeline';
import type { VikingBotLabels } from './model.js';

/** Reuse Ark's keyboard-operable disclosure; its default reasoning renderer is text-only. */
export function ReasoningActivity({
  context,
  labels,
}: {
  context: ActivityItemRenderContext<ReasoningActivityItem>;
  labels: VikingBotLabels;
}) {
  const Disclosure = context.components.ActivityToolDisclosure;
  const running =
    context.item.status === 'running' || context.item.status === 'queued';
  return (
    <Disclosure
      id={context.item.id}
      status={context.item.status}
      title={
        context.item.titleOverride ??
        (running ? labels.thinking : labels.reasoning)
      }
      defaultExpanded={running}
      detail={
        <div className="ov-viking-bot__reasoning-body">
          {context.item.summary.join('\n')}
        </div>
      }
    />
  );
}
