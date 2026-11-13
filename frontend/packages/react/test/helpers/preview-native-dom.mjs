import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import {
  h,
  mount,
  recordingFetch,
  ok,
  click,
  act,
  settle,
  button,
} from './acl-dom.mjs';
export { h, mount, recordingFetch, ok, click, act, settle, button };
export const rootUri = 'viking://resources';
export const target = `${rootUri}/wiki`;
export function fixture(uri, isDir, intercept, targets = []) {
  return recordingFetch((call) => {
    const result = intercept?.(call);
    if (result !== undefined) return result;
    if (call.path.endsWith('/compile/configs')) return ok({ targets });
    if (call.path.endsWith('/fs/attrs')) return ok({ attrs: { tags: [] } });
    if (call.path.endsWith('/fs/stat'))
      return ok({
        isDir: call.uri === uri ? isDir : true,
        access: 'manage',
      });
    if (call.path.endsWith('/fs/ls'))
      return ok(
        call.uri === uri
          ? []
          : [
              ...targets.map(({ to }) => ({
                uri: to,
                isDir: true,
                tags: ['compile_target=true'],
              })),
              ...(!targets.some(({ to }) => to === uri)
                ? [{ uri, isDir }]
                : []),
            ],
      );
    if (/\/content\/(read|abstract|overview)$/.test(call.path)) return ok('');
    if (call.path.endsWith('/snapshot/log')) return ok([]);
    if (call.path.endsWith('/snapshot/versions')) return ok({ versions: [] });
    if (call.path.endsWith('/content/download'))
      return Promise.resolve(new Response('raw bytes'));
    return ok({});
  });
}
export function tree(fetch, uri, props = {}, identity = {}) {
  return h(
    OpenVikingProvider,
    { fetch, ...identity },
    h(FileBrowser, { rootUri, uri, ...props }),
  );
}
export async function openAction(label) {
  const trigger = document.querySelector('[aria-haspopup="menu"]');
  if (trigger) await click(trigger);
  const node = [
    ...document.querySelectorAll('[role="menuitem"], button[aria-label]'),
  ].find(
    (entry) =>
      entry.textContent.trim() === label ||
      entry.getAttribute('aria-label') === label,
  );
  await click(node);
}
export const workflow = () =>
  document.querySelector('.ov-file-browser-workflow [role="dialog"]');
