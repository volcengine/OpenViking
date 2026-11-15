import { useEffect, useMemo } from 'react';
import { useAnalyticsRef } from '../../shared/analytics/context.js';
import { startOperation, errorKind } from '../../shared/analytics/reporter.js';
import {
  isTaskTerminal,
  type TrackedTask,
} from '../../shared/task-tracking/types.js';

export type CommandAnalytics = ReturnType<typeof useCommandAnalytics>;
export function useCommandAnalytics(
  tasks: readonly TrackedTask[],
  ownerKey: string,
) {
  const ref = useAnalyticsRef();
  const state = useMemo(() => {
    // Pending operations belong to this task source, including its account scope.
    void ownerKey;
    const pending = new Map<
      string,
      { operation: ReturnType<typeof startOperation>; keys: string[] }
    >();
    return {
      pending,
      begin(id: string) {
        if (!ref.current) return;
        const operation = startOperation();
        ref.current.emit('console_command_submit', () => ({
          operation_id: operation.operation_id,
        }));
        return {
          success(keys: string[]) {
            if (keys.length) {
              pending.set(id, { operation, keys });
              ref.current?.emit('console_command_accepted', operation.finish);
            } else
              ref.current?.emit('console_command_success', operation.finish);
          },
          fail: (error: unknown) =>
            ref.current?.emit('console_command_fail', () => ({
              ...operation.finish(),
              error_kind: errorKind(error),
            })),
          cancel: () =>
            ref.current?.emit('console_command_cancel', operation.finish),
        };
      },
    };
  }, [ref, ownerKey]);
  // Task completions arrive from the external task tracker after request acceptance.
  // Consume each terminal group once; unmount releases this instance's pending groups.
  useEffect(() => {
    for (const [id, { operation, keys }] of state.pending) {
      const current = keys.map((key) => tasks.find((task) => task.key === key));
      if (!current.every((task) => task && isTaskTerminal(task.status)))
        continue;
      state.pending.delete(id);
      if (current.some((task) => task?.status === 'failed'))
        ref.current?.emit('console_command_fail', () => ({
          ...operation.finish(),
          error_kind: 'business',
        }));
      else
        ref.current?.emit(
          current.some((task) => task?.status === 'cancelled')
            ? 'console_command_cancel'
            : 'console_command_success',
          operation.finish,
        );
    }
  });
  return state;
}
