import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATH_BUILDERS } from '../../client/paths.js';
import type { TaskService, TaskStatus } from './types.js';

export function createNativeTaskService(
  client: OvClient,
  options: OvRequestOptions,
): TaskService {
  return {
    async getTask(id, signal) {
      const task = await client.request<{
        task_id: string;
        status: TaskStatus;
        stage?: string;
        result?: unknown;
        error?: string;
      }>({
        method: 'GET',
        path: OV_PATH_BUILDERS.task(id),
        options: {
          ...options,
          signal: options.signal
            ? AbortSignal.any([options.signal, signal])
            : signal,
        },
      });
      return {
        id: task.task_id,
        status: task.status,
        stage: task.stage ?? undefined,
        result: task.result,
        error: task.error ?? undefined,
      };
    },
  };
}
