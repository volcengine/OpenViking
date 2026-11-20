import { OV_PATHS, OV_PATH_BUILDERS } from '../../../client/paths.js';
import type { OvRequestSpec } from '../../../client/types.js';
import {
  isTaskTerminal,
  type TaskReference,
  type TaskStatus,
} from '../../../shared/task-tracking/types.js';

import type { VikingBotChatService } from './types.js';

type Request = <T>(spec: OvRequestSpec, signal: AbortSignal) => Promise<T>;

export async function readSessionCommitTask(
  request: Request,
  id: string,
  signal: AbortSignal,
): Promise<TaskReference | undefined> {
  const [session, tasks] = await Promise.all([
    request<{ pending_tokens?: number }>(
      { method: 'GET', path: OV_PATH_BUILDERS.session(id) },
      signal,
    ),
    request<{ task_id: string; status: TaskStatus }[]>(
      {
        method: 'GET',
        path: OV_PATHS.tasks,
        query: { task_type: 'session_commit', resource_id: id, limit: 1 },
      },
      signal,
    ),
  ]);
  const task = tasks?.[0];
  // A completed task only covers this history if there are no new messages.
  if (!task?.task_id || (session?.pending_tokens ?? 0) > 0) return;
  if (task.status !== 'completed' && isTaskTerminal(task.status)) return;
  if (task.status === 'completed' && session?.pending_tokens !== 0) return;
  return { id: task.task_id, kind: 'memory', status: task.status };
}

export async function submitSessionCommit(
  request: Request,
  id: string,
  signal: AbortSignal,
): ReturnType<NonNullable<VikingBotChatService['commitSession']>> {
  const result = await request<{
    status?: unknown;
    reason?: unknown;
    task_id?: unknown;
  }>(
    {
      method: 'POST',
      path: OV_PATH_BUILDERS.sessionCommit(id),
      body: { keep_recent_count: 0 },
    },
    signal,
  );
  if (
    result?.status === 'accepted' &&
    typeof result.task_id === 'string' &&
    result.task_id.trim()
  )
    return { status: 'accepted', taskId: result.task_id };
  if (result?.status === 'skipped')
    return {
      status: 'skipped',
      reason: typeof result.reason === 'string' ? result.reason : undefined,
    };
  throw new Error('Invalid session commit response');
}
