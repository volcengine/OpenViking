import type { ConsoleRecord } from './command-messages.js';
import type { TrackedTask } from '../../shared/task-tracking/types.js';
import { isTaskTerminal } from '../../shared/task-tracking/types.js';
import type { Locale } from '../../shared/locale.js';
import { resolveConsoleLocale } from './labels.js';
import {
  getCommandConsoleSafeHref,
  readCommandConsoleProseUris,
} from '../../components/command-console/command-console-text.js';

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

/** Keep completed task targets alongside the original command output. */
export function formatConsoleTaskResult(
  record: ConsoleRecord,
  locale: Locale,
): ConsoleRecord {
  const { tasks, content } = record;
  if (content?.variant !== 'write-result') return record;
  const lines = [...content.lines];
  const uris = new Set(
    lines.flatMap((line) =>
      typeof line === 'string'
        ? readCommandConsoleProseUris(line)
        : line && typeof line === 'object' && 'uri' in line
          ? (getCommandConsoleSafeHref(line.uri) ?? [])
          : [],
    ),
  );
  for (const task of tasks ?? []) {
    const uri = getCommandConsoleSafeHref(task.uri);
    if (task.status !== 'completed' || !uri || uris.has(uri)) continue;
    uris.add(uri);
    lines.push({
      label: resolveConsoleLocale(locale).taskResult,
      uri,
    });
  }
  const result = { ...record, content: { ...content, lines } };
  const task = tasks?.[0];
  if (
    record.status !== 'success' ||
    tasks?.length !== 1 ||
    task?.kind !== 'resource' ||
    task.status !== 'completed' ||
    (task.stage && task.stage !== 'completed') ||
    task.error ||
    task.queryError ||
    !content.lines.includes(`Task id: ${task.id}`)
  ) {
    return result;
  }
  return {
    ...result,
    tasks: undefined,
    content: {
      ...content,
      lines: lines.filter((line) => line !== 'Submission status: success'),
    },
  };
}
