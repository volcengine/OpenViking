import { useLayoutEffect, useMemo } from 'react';
import type { DirState } from './fs-model.js';

/** Observe committed directory state; never use a fetch callback as UI readiness. */
export function useDirectoryMonitoring(
  dirs: Record<string, DirState>,
  ready: boolean,
) {
  const operations = useMemo(
    () => Object.values(dirs).flatMap((dir) => dir.monitoring ?? []),
    [dirs],
  );
  // 将已提交的目录结果同步给宿主监控；请求完成不能证明当前目录已呈现，不能在请求回调中上报。
  // 请求及 span 由查询层管理，无新增订阅可清理；只处理当前 dirs，ready 幂等以避免 Strict Mode 重放上报。
  useLayoutEffect(() => {
    if (!ready) return;
    for (const dir of Object.values(dirs)) {
      if (dir.loading || dir.error) continue;
      for (const operation of dir.monitoring ?? [])
        operation.ready({
          entryCount: dir.entries.length,
          empty: dir.entries.length === 0,
        });
    }
  }, [dirs, ready]);
  return operations;
}
