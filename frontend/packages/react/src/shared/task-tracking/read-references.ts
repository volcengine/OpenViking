import { taskStatuses, type TaskReference, type TaskStatus } from './types.js';

const kinds: Record<string, TaskReference['kind']> = {
  'add-resource': 'resource',
  'add-skill': 'skill',
  'add-memory': 'memory',
  compile: 'compile',
  'compile create': 'compile',
  openviking_add_resource: 'resource',
  openviking_add_skill: 'skill',
  openviking_memory_commit: 'memory',
  openviking_compile: 'compile',
};

/** Interpret explicit IDs in known submission results, never prose or UUID matches. */
export function readTaskReferences(
  operation: string,
  payload: unknown,
): TaskReference[] {
  if (!(operation in kinds)) return [];
  const found = new Map<string, TaskReference>();
  function read(value: unknown, depth: number) {
    if (depth > 5) return;
    if (typeof value === 'string') {
      try {
        read(JSON.parse(value), depth + 1);
      } catch {
        /* Text is not a task reference. */
      }
      return;
    }
    if (Array.isArray(value)) {
      for (const item of value) read(item, depth + 1);
      return;
    }
    if (!value || typeof value !== 'object') return;
    const data = value as Record<string, unknown>;
    if (data.isError || (data.status === 'error' && !data.task_id)) return;
    const id = data.task_id;
    if (typeof id === 'string' && id.trim()) {
      const status = data.task_status ?? data.status;
      const uri = data.root_uri ?? data.archive_uri ?? data.to;
      found.set(id, {
        id,
        kind: kinds[operation],
        ...(typeof uri === 'string' ? { uri } : {}),
        ...(typeof status === 'string' && taskStatuses.includes(status)
          ? { status: status as TaskStatus }
          : {}),
      });
    }
    for (const key of [
      'result',
      'results',
      'installed',
      'tasks',
      'commit',
      'structuredContent',
    ])
      read(data[key], depth + 1);
    if (Array.isArray(data.content)) {
      for (const block of data.content as unknown[]) {
        if (
          block &&
          typeof block === 'object' &&
          'type' in block &&
          block.type === 'text' &&
          'text' in block
        )
          read(block.text, depth + 1);
      }
    }
  }
  read(payload, 0);
  return [...found.values()];
}
