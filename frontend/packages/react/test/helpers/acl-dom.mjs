import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './module-dom.mjs';
export { act, h, mount, ok, recordingFetch, settle };
for (const name of ['MutationObserver', 'HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
export const URI = 'viking://resources/handbook';
export const initial = () => ({
  uri: URI,
  acl_mode: 'inherit',
  direct_entries: [{ principal: 'user:bob', level: 'write' }],
  inherited_entries: [
    { principal: 'user:alice', level: 'manage' },
    { principal: 'group:research', level: 'read' },
  ],
  effective_entries: [
    { principal: 'user:alice', level: 'manage' },
    { principal: 'user:bob', level: 'write' },
    { principal: 'group:research', level: 'read' },
  ],
});
export function fixture(intercept) {
  let report = initial();
  const fetch = recordingFetch(async (call) => {
    const override = intercept?.(call);
    if (override !== undefined) return override;
    if (call.path.endsWith('/acl')) {
      if (call.init.method === 'PUT') {
        const parent =
          call.body.acl_mode === 'inherit' ? report.inherited_entries : [];
        report = {
          ...report,
          acl_mode: call.body.acl_mode,
          direct_entries: call.body.entries,
          effective_entries: [
            ...parent.filter(
              (entry) =>
                !call.body.entries.some(
                  (item) => item.principal === entry.principal,
                ),
            ),
            ...call.body.entries,
          ],
        };
      }
      if (call.init.method === 'DELETE')
        report = {
          ...report,
          acl_mode: 'inherit',
          direct_entries: [],
          effective_entries: report.inherited_entries,
        };
      return ok(report);
    }
    if (call.path.endsWith('/fs/ls')) return ok([{ uri: URI, isDir: true }]);
    if (call.path.endsWith('/fs/stat'))
      return ok({ isDir: true, permission: 'manage' });
    if (
      call.path.endsWith('/content/abstract') ||
      call.path.endsWith('/content/overview')
    )
      return ok('');
    if (call.path.endsWith('/users'))
      return ok({
        users: [{ user_id: 'carol' }, { user_id: 'bob' }],
        total: 2,
      });
    if (call.path.endsWith('/groups'))
      return ok([
        { group_id: 'research', member_count: 2 },
        { group_id: 'engineering', member_count: 1 },
      ]);
    return ok([]);
  });
  return fetch;
}
export const tree = (fetch, props = {}, provider = {}) =>
  h(
    OpenVikingProvider,
    { fetch, accountId: 'demo', userId: 'alice', ...provider },
    h(FileBrowser, {
      acl: true,
      rootUri: 'viking://resources',
      defaultUri: URI,
      ...props,
    }),
  );
export async function click(node) {
  if (!node) throw new Error('Missing click target');
  await act(async () => node.click());
  await settle();
}
export const button = (text, root = document) =>
  [...root.querySelectorAll('button')].find(
    (node) => node.textContent.trim() === text,
  );
export async function open() {
  await click(document.querySelector('.ov-file-browser-acl__manage'));
  return document.querySelector('.ov-file-browser-acl [role="dialog"]');
}
export async function level(principal, text) {
  const entry = [
    ...document.querySelectorAll('.ov-file-browser-acl__entry'),
  ].find((node) => node.querySelector(`button[aria-label^="${principal}:"]`));
  await click(entry?.querySelector('button'));
  await click(button(text, entry));
}
export const writes = (fetch) =>
  fetch.calls.filter(
    (call) => call.path.endsWith('/acl') && call.init.method !== 'GET',
  );
export const failure = (message = 'Access denied') =>
  Promise.resolve(
    new Response(
      JSON.stringify({
        status: 'error',
        error: { code: 'PERMISSION_DENIED', message },
      }),
      { status: 403 },
    ),
  );
