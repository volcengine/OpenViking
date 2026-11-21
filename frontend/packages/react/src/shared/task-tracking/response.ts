import { normalizeTaskUri, readTaskResourceUri } from './resource-uri.js';
import { taskStatuses, type TaskSnapshot, type TrackedTask } from './types.js';

export class InvalidTaskResponse extends Error {}

function assertTask(value: unknown, id: string): asserts value is TaskSnapshot {
  if (
    !value ||
    typeof value !== 'object' ||
    !('id' in value) ||
    value.id !== id ||
    !('status' in value) ||
    typeof value.status !== 'string' ||
    !taskStatuses.includes(value.status)
  )
    throw new InvalidTaskResponse();
}

/** Adapt native and host task service responses into the shared snapshot. */
export function mergeTaskResponse(
  previous: TrackedTask,
  value: unknown,
): TrackedTask {
  assertTask(value, previous.id);
  const { uri, ...task } = value;
  const completedUri =
    task.status === 'completed' ? readTaskResourceUri(task.result) : undefined;
  const resourceUri = completedUri ?? normalizeTaskUri(uri);
  return {
    ...previous,
    ...task,
    ...(resourceUri && { uri: resourceUri }),
    queryError: undefined,
  };
}

export function taskQueryError(
  error: unknown,
): NonNullable<TaskSnapshot['queryError']> {
  if (error instanceof InvalidTaskResponse)
    return { code: 'invalid-response', message: undefined };
  return {
    code: 'request',
    message: error instanceof Error ? error.message : String(error),
  };
}
