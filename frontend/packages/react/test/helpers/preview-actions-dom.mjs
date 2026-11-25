import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import {
  act,
  h,
  mount,
  ok,
  recordingFetch,
  settle,
  click,
  button,
} from './acl-dom.mjs';

const uri = 'viking://resources/guide.md';
const parent = 'viking://resources';
function fixture(remove, permission = 'manage') {
  return recordingFetch((call) => {
    if (call.path === '/api/v1/fs') return remove(call);
    if (call.path.endsWith('/fs/stat'))
      return ok({ isDir: call.uri !== uri, access: permission });
    if (call.path.endsWith('/fs/ls')) return ok([{ uri, isDir: false }]);
    if (/\/content\/(read|abstract|overview)$/.test(call.path)) return ok('');
    return ok([]);
  });
}
const tree = (fetch, props = {}, accountId = 'a') =>
  h(
    OpenVikingProvider,
    { fetch, accountId },
    h(FileBrowser, { rootUri: parent, defaultUri: uri, ...props }),
  );
const more = () => document.querySelector('[aria-haspopup="menu"]');
const menuItem = (name) =>
  [...document.querySelectorAll('[role="menuitem"]')].find(
    (node) => node.textContent.trim() === name,
  );
const dialog = () =>
  document.querySelector('.ov-file-browser-delete [role="dialog"]');
const openDelete = async () => {
  if (more()) {
    await click(more());
    await click(menuItem('Delete document'));
  } else {
    await click(document.querySelector('[aria-label="Delete document"]'));
  }
};
const failure = () =>
  new Response(
    JSON.stringify({
      status: 'error',
      error: { code: 'DENIED', message: 'Try again' },
    }),
    { status: 403 },
  );

export {
  act,
  mount,
  ok,
  settle,
  click,
  button,
  uri,
  parent,
  fixture,
  tree,
  more,
  menuItem,
  dialog,
  openDelete,
  failure,
};
