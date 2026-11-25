import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { withBundledPreview } from './helpers/bundled-preview.mjs';

const loading = (document) =>
  document.querySelector('.ov-file-preview__layers-loading');
const layers = (document) => [
  ...document.querySelectorAll('.ov-file-preview__layer'),
];
const visibleText = (document) =>
  layers(document).find((layer) => !layer.hasAttribute('aria-hidden'))
    ?.textContent ?? '';
const level = (document, key) =>
  [...document.querySelectorAll('.ov-file-preview__level')].find(
    (button) => button.textContent === key,
  );

async function waitFor(check, message) {
  const deadline = Date.now() + 4000;
  do {
    if (check()) return;
    await delay(10);
  } while (Date.now() < deadline);
  assert.fail(message);
}

test('returning to the visible level cancels the loading transition', () =>
  withBundledPreview(
    'file-preview-transition',
    async ({ window }) => {
      const { document } = window;
      await waitFor(
        () => visibleText(document).includes('A body'),
        'the initial level did not finish loading',
      );

      level(document, 'L1').click();
      await waitFor(() => loading(document), 'loading overlay did not appear');
      level(document, 'L0').click();

      await waitFor(
        () => !loading(document) && layers(document).length === 1,
        'the loading overlay and pending preview should disappear',
      );
      assert.match(visibleText(document), /A body/);
    },
    '?case=cancel',
  ));

test('a late preview replaces its placeholder and stays visible until the next level loads', () =>
  withBundledPreview(
    'file-preview-transition',
    async ({ window }) => {
      const { document } = window;
      await waitFor(
        () => visibleText(document).includes('A body'),
        'the initial level did not finish loading',
      );

      level(document, 'L1').click();
      await waitFor(() => loading(document), 'loading overlay did not appear');
      window.__setBReady();
      await waitFor(
        () => !loading(document) && visibleText(document).includes('B body'),
        'the loaded body should replace its placeholder',
      );

      level(document, 'L2').click();
      await waitFor(() => loading(document), 'loading overlay did not appear');
      assert.match(visibleText(document), /B body/);
      window.__setCReady();
      await waitFor(() => {
        const text = visibleText(document);
        assert.match(text, /B body|C body/);
        return text.includes('C body');
      }, 'the next level did not finish loading');
      assert.equal(layers(document).length, 1);
    },
    '?case=snapshot',
  ));
