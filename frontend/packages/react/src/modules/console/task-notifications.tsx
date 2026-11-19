import { useEffect, useRef } from 'react';
import { useTaskTrackerContext } from '../../shared/task-tracking/context.js';
import { isTaskTerminal } from '../../shared/task-tracking/types.js';
import { normalizeTaskUri } from '../../shared/task-tracking/resource-uri.js';
import { useNotification } from '../../shared/primitives/notification/index.js';
import { Button } from '../../shared/primitives/button/button.js';
import { TaskStatusList } from '../../shared/task-status/task-status.js';
import { taskLabels } from '../../shared/task-status/labels.js';
import type { Locale } from '../../shared/locale.js';
import { resolveConsoleLocale } from './labels.js';

type OpenUri = (uri: string) => void;

export function ConsoleTaskNotifications({
  locale,
  onOpenMemoryUri,
}: {
  locale: Locale;
  onOpenMemoryUri?: OpenUri;
}) {
  const { tracker } = useTaskTrackerContext()!;
  const [api, holder] = useNotification({ locale });
  const hidden = useRef(new Set<string>());
  const versions = useRef(
    new Map<string, { version: string; open?: OpenUri }>(),
  );
  // Effect necessity: bridge the external scheduler's events to the imperative
  // notification API, including query updates that have no user event here.
  // Subscribe directly instead of mirroring task snapshots in React state;
  // cleanup unsubscribes, while the holder owns notice timers and portal cleanup.
  useEffect(() => {
    const update = () => {
      const tasks = tracker
        .getSnapshot()
        .filter(
          (task) =>
            task.origin !== 'command' &&
            ((task.kind !== 'resource' && task.origin !== 'memory-commit') ||
              isTaskTerminal(task.status) ||
              task.queryError),
        );
      const current = new Set(tasks.map((task) => task.key));
      for (const key of versions.current.keys()) {
        if (!current.has(key)) {
          versions.current.delete(key);
          api.remove(key);
        }
      }
      const labels = taskLabels(locale);
      const { memoryCommit } = resolveConsoleLocale(locale);
      for (const task of tasks) {
        if (hidden.current.has(task.key)) continue;
        const memoryCompleted =
          task.status === 'completed' && task.origin === 'memory-commit';
        const target =
          memoryCompleted &&
          task.result &&
          typeof task.result === 'object' &&
          'memory_diff_uri' in task.result
            ? normalizeTaskUri(task.result.memory_diff_uri)
            : undefined;
        const open = target ? onOpenMemoryUri : undefined;
        const version = JSON.stringify([
          locale,
          task.status,
          task.stage,
          task.error,
          task.queryError,
          task.uri,
          memoryCompleted,
          target,
        ]);
        const previous = versions.current.get(task.key);
        if (previous?.version === version && previous.open === open) continue;
        versions.current.set(task.key, { version, open });
        const dismiss = () => hidden.current.add(task.key);
        if (memoryCompleted) {
          api.success({
            id: task.key,
            className: 'ov-console__task-completion',
            title: target ? memoryCommit.saved : memoryCommit.success,
            content: target ?? memoryCommit.completed,
            duration: 5000,
            onDismiss: dismiss,
            btn: target ? (
              <Button
                type="primary"
                size="small"
                onClick={() => {
                  dismiss();
                  api.remove(task.key);
                  if (open) open(target);
                  else window.location.assign(target);
                }}
              >
                {memoryCommit.view}
              </Button>
            ) : undefined,
          });
          continue;
        }
        const terminal = isTaskTerminal(task.status);
        const type =
          task.status === 'completed'
            ? 'success'
            : task.status === 'failed'
              ? 'error'
              : task.status === 'cancelled' || task.queryError
                ? 'warning'
                : 'loading';
        api[type]({
          id: task.key,
          title: task.kind ? labels.kinds[task.kind] : labels.task,
          content: (
            <>
              {task.uri ? <div>{task.uri}</div> : null}
              <TaskStatusList tasks={[task]} />
            </>
          ),
          duration: terminal && task.status !== 'failed' ? 3000 : 0,
          onDismiss: dismiss,
        });
      }
    };
    update();
    return tracker.subscribe(update);
  }, [tracker, locale, api, onOpenMemoryUri]);
  return holder;
}
