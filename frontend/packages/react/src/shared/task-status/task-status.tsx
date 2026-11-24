import type { Locale } from '../locale.js';
import { useLocale } from '../locale-context.js';
import type { TaskSnapshot } from '../task-tracking/types.js';
import { taskLabels } from './labels.js';

/** A passive view of task snapshots supplied by a module or host. */
export function TaskStatusList({
  tasks,
  locale,
}: {
  tasks?: readonly TaskSnapshot[];
  locale?: Locale;
}) {
  const labels = taskLabels(useLocale(locale));
  if (!tasks?.length) return null;
  return (
    <div className="ov-task-status" role="status">
      {tasks.map((task) => (
        <div
          key={task.id}
          className="ov-task-status__item"
          data-task-status={task.status ?? 'submitted'}
        >
          <span>{labels[task.status ?? 'submitted']}</span>
          <code className="ov-task-status__id">{task.id}</code>
          {task.stage ? <span>{task.stage}</span> : null}
          {task.error ? (
            <span className="ov-task-status__error">{task.error}</span>
          ) : null}
          {task.queryError ? (
            <span>
              {labels.queryError}
              {task.queryError.code === 'invalid-response'
                ? `: ${labels.invalidResponse}`
                : task.queryError.message
                  ? `: ${task.queryError.message}`
                  : ''}
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}
