import { useExposure } from './use-exposure.js';

type Target = 'copyContent' | 'download';
const targets = new WeakMap<object, Target>();
/** Internal identity marker; never infer analytics from a host's arbitrary action key. */
export function markPreviewAction<T extends object>(
  action: T,
  target: Target,
): T {
  targets.set(action, target);
  return action;
}
export const previewActionTarget = (action: object) => targets.get(action);

export function usePreviewActionExposure(
  items: readonly (object | readonly object[])[],
) {
  const action = items.flat()[0];
  const target = action && targets.get(action);
  return useExposure(
    target === 'download'
      ? 'file_preview_download_show'
      : 'file_preview_copy_content_show',
    target,
    undefined,
    Boolean(target),
  );
}
