import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  mount,
  type,
  key,
  click,
  record,
  act,
  dom,
} from './helpers/tui-dom.mjs';

const markdown = (text) =>
  record({ variant: 'text', format: 'markdown', text });

test('record updates preserve a scrolled-up reading position and follow the bottom or a new submission', async () => {
  const view = await mount({ records: [markdown('old')] });
  try {
    const body = view.container.querySelector('.ov-tui__body');
    Object.defineProperties(body, {
      scrollHeight: { value: 1000, configurable: true },
      clientHeight: { value: 200 },
    });
    body.scrollTop = 100;
    await act(() => body.dispatchEvent(new dom.window.Event('scroll')));
    await view.render({ records: [markdown('updated')] });
    assert.equal(body.scrollTop, 100);
    body.scrollTop = 795;
    await act(() => body.dispatchEvent(new dom.window.Event('scroll')));
    Object.defineProperty(body, 'scrollHeight', { value: 1200 });
    await view.render({ records: [markdown('longer')] });
    assert.equal(body.scrollTop, 1200);
    body.scrollTop = 50;
    await act(() => body.dispatchEvent(new dom.window.Event('scroll')));
    await view.render({ onSubmit() {} });
    await type(view.input, 'ov read demo');
    await key(view.input, 'Enter');
    await view.render({ records: [markdown('new command')] });
    assert.equal(body.scrollTop, 1200);
  } finally {
    await view.dispose();
  }
});

test('Markdown code copy feedback, DOM identity and focused links survive controlled input updates', async () => {
  const copied = [];
  const opened = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async () => {} },
  });
  const view = await mount({
    records: [
      markdown(
        '```json\n{"id":9007199254740993}\n```\n\n[Open](viking://resources/a.md)',
      ),
    ],
    onCopy: (value) => copied.push(value),
    onOpenUri: (uri) => opened.push(`old:${uri}`),
  });
  try {
    const button = view.container.querySelector('.ov-tui__copy');
    await click(button);
    assert.deepEqual(copied, ['{"id":9007199254740993}']);
    assert.equal(button.textContent, '');
    assert.ok(button.querySelector('svg[aria-hidden="true"]'));
    assert.equal(button.getAttribute('aria-label'), 'Copied');
    const link = view.container.querySelector('a');
    link.focus();
    await view.render({
      onOpenUri: (uri) => opened.push(uri),
      labels: { copied: 'Done' },
    });
    await type(view.input, 'hello');
    assert.ok(view.container.querySelector('.ov-tui__copy') === button);
    assert.ok(document.activeElement === link);
    assert.equal(button.getAttribute('aria-label'), 'Done');
    await click(link);
    assert.deepEqual(opened, ['viking://resources/a.md']);
  } finally {
    await view.dispose();
  }
});

test('formatted Markdown link labels contain one anchor and invoke the host once', async () => {
  const uri = 'viking://resources/a.md';
  const opened = [];
  const view = await mount({
    records: [
      markdown(
        `3. third\n4. fourth\n\n[\`${uri}\`](${uri}) [**${uri}**](${uri})`,
      ),
    ],
    onOpenUri: (value) => opened.push(value),
  });
  try {
    assert.equal(view.container.querySelector('ol').start, 3);
    assert.equal(view.container.querySelectorAll('a').length, 2);
    assert.ok(!view.container.querySelector('a a'));
    await click(view.container.querySelector('a'));
    assert.deepEqual(opened, [uri]);
  } finally {
    await view.dispose();
  }
});

test('failed clipboard writes keep the copy action and do not report success', async () => {
  const copied = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText: async () => {
        throw new Error('Clipboard unavailable');
      },
    },
  });
  const view = await mount({
    locale: 'zh-CN',
    records: [markdown('```json\n{"demo":true}\n```')],
    onCopy: (text) => copied.push(text),
  });
  try {
    const button = view.container.querySelector('.ov-tui__copy');
    await click(button);
    assert.equal(button.getAttribute('aria-label'), '复制');
    assert.equal(button.textContent, '');
    assert.deepEqual(copied, []);
  } finally {
    await view.dispose();
  }
});

test('literal resource punctuation survives code, quoted arguments and callback activation', async () => {
  for (const name of ['a;b.md', 'a,b.md', 'a(b).md', 'a[b].md', 'a.md.']) {
    const uri = `viking://resources/${name}`;
    const opened = [];
    const value = `ov read "${uri}"`;
    const view = await mount({
      value,
      records: [
        {
          ...markdown(`\`${uri}\`\n\n\`\`\`json\n{"uri":"${uri}"}\n\`\`\``),
          command: value,
        },
      ],
      onOpenUri: (target) => opened.push(target),
    });
    try {
      const links = [...view.container.querySelectorAll('a')];
      assert.equal(links.length, 4);
      for (const link of links) {
        assert.equal(link.getAttribute('href'), uri);
        await click(link);
      }
      assert.deepEqual(opened, Array(4).fill(uri));
      assert.equal(view.input.value, value);
    } finally {
      await view.dispose();
    }
  }
});
