import { taskStatuses, type TaskSnapshot } from './types.js';
export class InvalidTaskResponse extends Error {}
export function assertTask(
  value: unknown,
  id: string,
): asserts value is TaskSnapshot {
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
