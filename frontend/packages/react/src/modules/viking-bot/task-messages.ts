import type {
  VikingBotMessage,
  VikingBotMessagePart,
  VikingBotStreamingState,
} from '../../components/viking-bot/model.js';
import type {
  TaskReference,
  TrackedTask,
} from '../../shared/task-tracking/types.js';

export function submittedBotTasks(
  messages: readonly VikingBotMessage[],
  streaming?: VikingBotStreamingState,
): TaskReference[] {
  return [
    ...messages.flatMap((message) => message.parts),
    ...(streaming?.parts ?? []),
  ].flatMap((part) =>
    part.type === 'tool' || part.type === 'tool_result'
      ? (part.tasks ?? [])
      : [],
  );
}

export function projectBotTasks(
  messages: readonly VikingBotMessage[],
  streaming: VikingBotStreamingState | undefined,
  tasks: readonly TrackedTask[],
  sourceKey: string,
) {
  const byKey = new Map(tasks.map((task) => [task.key, task]));
  const parts = (
    source: readonly VikingBotMessagePart[],
  ): readonly VikingBotMessagePart[] =>
    source.map((part) => {
      if (
        (part.type !== 'tool' && part.type !== 'tool_result') ||
        !part.tasks?.length
      )
        return part;
      return {
        ...part,
        tasks: part.tasks.map(
          (task) => byKey.get(`${sourceKey}:${task.id}`) ?? task,
        ),
      };
    });
  return {
    messages: messages.map((message) => ({
      ...message,
      parts: parts(message.parts),
    })),
    streaming: streaming
      ? { ...streaming, parts: parts(streaming.parts) }
      : undefined,
  };
}
