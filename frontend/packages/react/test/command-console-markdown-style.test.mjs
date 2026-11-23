import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { mount, record, dom } from './helpers/command-console-dom.mjs';

const require = createRequire(import.meta.url);
const css = await readFile(
  require.resolve('@openviking/react/components/command-console/style.css'),
  'utf8',
);

test('published Markdown styles retain visible native ordered markers and indentation', async () => {
  const style = document.createElement('style');
  style.textContent = css;
  document.head.append(style);
  const view = await mount({
    records: [
      record({
        variant: 'text',
        format: 'markdown',
        text: '3. Continue\n4. Finish\n\n- Detail',
      }),
    ],
  });
  try {
    const ol = view.container.querySelector('ol');
    const computed = dom.window.getComputedStyle(ol);
    assert.equal(ol.start, 3);
    assert.equal(computed.listStyle, 'decimal');
    assert.equal(computed.display, 'block');
    assert.ok(parseFloat(computed.paddingInlineStart) > 0);
    assert.equal(
      dom.window.getComputedStyle(ol.querySelector('li')).display,
      'list-item',
    );
    assert.equal(
      dom.window.getComputedStyle(view.container.querySelector('ul')).listStyle,
      'none',
    );
  } finally {
    await view.dispose();
    style.remove();
  }
});
