import { useEffect, useRef } from 'react';
import { useTaskTrackerContext } from '../../shared/task-tracking/context.js';
import { isTaskTerminal } from '../../shared/task-tracking/types.js';
import { useNotification } from '../../shared/primitives/notification/index.js';
import { TaskStatusList } from '../../shared/task-status/task-status.js';
import { taskLabels } from '../../shared/task-status/labels.js';
import type { Locale } from '../../shared/locale.js';

export function ConsoleTaskNotifications({ locale }: { locale: Locale }) {
  const { tracker } = useTaskTrackerContext()!;
  const [api, holder] = useNotification({ locale });
  const hidden = useRef(new Set<string>());
  const versions = useRef(new Map<string, string>());
  // Effect necessity: bridge the external scheduler's events to the imperative
  // notification API, including query updates that have no user event here.
  // Subscribe directly instead of mirroring task snapshots in React state;
  // cleanup unsubscribes, while the holder owns notice timers and portal cleanup.
  useEffect(() => {
    const update = () => {
      const tasks = tracker.getSnapshot();
      const current = new Set(tasks.map((task) => task.key));
      for (const key of versions.current.keys()) {
        if (!current.has(key)) {
          versions.current.delete(key);
          api.remove(key);
        }
      }
      const labels = taskLabels(locale);
      for (const task of tasks) {
        if (hidden.current.has(task.key)) continue;
        const version = JSON.stringify([
          locale,
          task.status,
          task.stage,
          task.error,
          task.queryError,
          task.uri,
        ]);
        if (versions.current.get(task.key) === version) continue;
        versions.current.set(task.key, version);
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
          onDismiss: () => hidden.current.add(task.key),
        });
      }
    };
    update();
    return tracker.subscribe(update);
  }, [tracker, locale, api]);
  return holder;
}
