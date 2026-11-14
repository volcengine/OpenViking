import type { RefObject } from 'react';
import { errorKind, startOperation } from './reporter.js';
import type { AnalyticsReporter } from './reporter.js';

export type MutationTarget =
  | 'file_browser_pack_import'
  | 'file_browser_pack_export'
  | 'file_browser_resource_delete'
  | 'file_browser_compile_config_save'
  | 'file_browser_acl_change'
  | 'file_preview_download';

/** Internal operation boundary. A receiver failure never affects an operation. */
export function beginMutation(
  ref: RefObject<AnalyticsReporter | undefined>,
  target?: MutationTarget,
) {
  if (!target || !ref.current) return;
  const operation = startOperation();
  if (target === 'file_preview_download')
    return {
      success: () =>
        ref.current?.emit('file_preview_download_start', operation.finish),
      fail: (error: unknown) =>
        ref.current?.emit('file_preview_download_fail', () => ({
          ...operation.finish(),
          error_kind: errorKind(error),
        })),
    };
  ref.current.emit(`${target}_submit`, () => ({
    operation_id: operation.operation_id,
  }));
  return {
    success: () => ref.current?.emit(`${target}_success`, operation.finish),
    fail: (error: unknown) =>
      ref.current?.emit(`${target}_fail`, () => ({
        ...operation.finish(),
        error_kind: errorKind(error),
      })),
  };
}
