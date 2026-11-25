import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { withBundledPreview } from './helpers/bundled-preview.mjs';

test('ordinary JSON uses formatted line numbers and preserves nested fold state', () =>
  withBundledPreview('file-preview-session-messages', async (dom) => {
    const { document } = dom.window;
    async function waitFor(predicate) {
      for (let attempt = 0; attempt < 400; attempt += 1) {
        if (predicate()) return;
        await delay(20);
      }
      assert.fail('JSON preview did not become ready');
    }

    await waitFor(() =>
      document.querySelector('#json-preview [data-file-preview-json]'),
    );
    await waitFor(() =>
      document.querySelector('#json-explicit [data-file-preview-json]'),
    );
    const structured = document.querySelector(
      '#json-preview [data-file-preview-json]',
    );
    assert.ok(structured);
    assert.deepEqual(
      [...structured.querySelectorAll('[data-line]')].map((line) =>
        Number(line.dataset.line),
      ),
      [1, 2, 3, 4, 5, 6, 7, 8],
    );
    assert.deepEqual(
      [
        ...document.querySelectorAll(
          '#json-explicit [data-file-preview-json] [data-line]',
        ),
      ].map((line) => Number(line.dataset.line)),
      [1, 2, 3, 4, 5],
    );
    const toggle = (label) =>
      [...structured.querySelectorAll('button')].find(
        (button) => button.getAttribute('aria-label') === label,
      );
    toggle('Collapse line 3').click();
    await waitFor(() => toggle('Expand line 3'));
    toggle('Collapse line 2').click();
    await waitFor(() => toggle('Expand line 2'));
    toggle('Expand line 2').click();
    await waitFor(
      () =>
        structured.textContent.includes('"sibling"') &&
        !structured.textContent.includes('"value"'),
    );
  }));
