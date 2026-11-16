import type { ConsoleRecord } from './command-messages.js';
import type { TrackedTask } from '../../shared/task-tracking/types.js';
import { isTaskTerminal } from '../../shared/task-tracking/types.js';

export function projectConsoleTasks(
  records: readonly ConsoleRecord[],
  tasks: readonly TrackedTask[],
): ConsoleRecord[] {
  const byKey = new Map(tasks.map((task) => [task.key, task]));
  return records.map((record) => {
    const snapshots =
      record.taskKeys?.flatMap((key) => byKey.get(key) ?? []) ?? [];
    if (!snapshots.length) return record;
    const status = snapshots.some((task) => !isTaskTerminal(task.status))
      ? 'pending'
      : snapshots.some((task) => task.status === 'failed')
        ? 'error'
        : snapshots.some((task) => task.status === 'cancelled')
          ? 'cancelled'
          : 'success';
    return { ...record, status, tasks: snapshots };
  });
}
