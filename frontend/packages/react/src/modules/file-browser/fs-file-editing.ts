import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewEditing } from '../../components/file-preview/index.js';
import { createNativeTaskService } from '../../shared/task-tracking/native-service.js';
import {
  isTaskTerminal,
  taskStatuses,
} from '../../shared/task-tracking/types.js';
import { isExperienceFile } from './experience-data.js';
import {
  isSummaryUri,
  isSystemUri,
  supportsFileHistory,
} from './preview-policy.js';
import { readFileContent } from './fs-read-content.js';

const EDITABLE_TEXT = /\.(?:md|json|jsonl|txt|yaml|yml)$/i;
const SNAPSHOT_WAIT_LIMIT_MS = 60_000;

function waitForNextTaskCheck(ms: number, signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(
        signal.reason instanceof Error
          ? signal.reason
          : new DOMException('Aborted', 'AbortError'),
      );
    }
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

async function waitForWriteTask(
  client: OvClient,
  options: OvRequestOptions,
  taskId: string,
) {
  const timeout = AbortSignal.timeout(SNAPSHOT_WAIT_LIMIT_MS);
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeout])
    : timeout;
  const tasks = createNativeTaskService(client, options);
  let interval = 500;
  for (;;) {
    signal.throwIfAborted();
    const task = await tasks.getTask(taskId, signal);
    if (task.id !== taskId || !taskStatuses.includes(task.status ?? '')) {
      throw new Error('Invalid content write task response');
    }
    // The body was persisted before the task was returned. Even if derived
    // indexing failed, the file lease is released once the task has settled.
    if (isTaskTerminal(task.status)) return;
    await waitForNextTaskCheck(interval, signal);
    interval = Math.min(interval * 2, 2000);
  }
}

/** Load the full file before editing; the preview can contain only its first page. */
export function createFileEditing(
  client: OvClient,
  uri: string,
  name: string,
  options: OvRequestOptions,
  onSave?: () => void,
  isWiki = false,
): FilePreviewEditing | undefined {
  if (
    !EDITABLE_TEXT.test(name) ||
    isSystemUri(uri) ||
    isSummaryUri(uri) ||
    isExperienceFile(uri)
  )
    return undefined;
  let wiki = isWiki;
  const canSnapshot = supportsFileHistory(uri);
  return {
    get promptForVersionNote() {
      return !wiki && canSnapshot;
    },
    loadContent: async () => {
      const result = await readFileContent(client, uri, options, -1, wiki);
      wiki = result.wiki;
      return result.content;
    },
    save: async (content, note) => {
      const shouldCreateVersion = !wiki && canSnapshot && note !== undefined;
      const write = await client.request<{ task_id?: string }>({
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: {
          uri,
          content,
          mode: 'replace',
          edit_source: 'manual',
          // The native write contract preserves and updates Wiki conflict history.
          ...(wiki ? { resolve_conflicts: true } : {}),
        },
        options,
      });
      if (!shouldCreateVersion) return;
      try {
        // The write hands its lease to background processing. The snapshot
        // needs that same lease, so wait for this write's task to release it.
        if (write?.task_id)
          await waitForWriteTask(client, options, write.task_id);
        options.signal?.throwIfAborted();
        const result = await client.request<{ result: 'created' | 'noop' }>({
          method: 'POST',
          path: OV_PATHS.snapshotCommit,
          body: { message: note || `Update ${name}`, paths: [uri] },
          options,
        });
        if (result?.result === 'created') return;
      } catch {
        // The body write already succeeded; report only the missing snapshot.
      }
      return { warning: 'version-not-created' };
    },
    onSave,
  };
}
