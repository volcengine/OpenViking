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
    request<{ message_count: number; commit_count: number; uri: string }>(
      { method: 'GET', path: OV_PATH_BUILDERS.session(id) },
      signal,
    ),
    request<
      {
        task_id: string;
        status: TaskStatus;
        result?: { archive_uri?: string };
      }[]
    >(
      {
        method: 'GET',
        path: OV_PATHS.tasks,
        query: { task_type: 'session_commit', resource_id: id, limit: 1 },
      },
      signal,
    ),
  ]);
  // Session message_count includes the retained live tail, even when its
  // pending_tokens are zero. A commit task cannot cover those live messages.
  if (session?.message_count !== 0) return;
  const task = tasks?.[0];
  if (task?.task_id && !isTaskTerminal(task.status)) {
    return { id: task.task_id, kind: 'memory', status: task.status };
  }
  if (!session.uri || !(session.commit_count > 0)) return;
  const archiveId = `archive_${String(session.commit_count).padStart(3, '0')}`;
  const latestArchive = `${session.uri.replace(/\/+$/, '')}/history/${archiveId}`;
  if (
    task?.task_id &&
    task.status === 'completed' &&
    task.result?.archive_uri === latestArchive
  ) {
    return { id: task.task_id, kind: 'memory', status: 'completed' };
  }

  // A completed task can expire before a retained failure from an older
  // archive. The latest archive's own status can still confirm completion.
  const archive = await request<{ status: string }>(
    { method: 'GET', path: OV_PATH_BUILDERS.sessionArchive(id, archiveId) },
    signal,
  );
  if (archive?.status === 'completed') {
    return { id: latestArchive, kind: 'memory', status: 'completed' };
  }
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
