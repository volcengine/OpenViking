import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h } from 'react';
import { act, mount, type } from './helpers/command-console-dom.mjs';
import { mount as mountConsole, ok } from './helpers/console-dom.mjs';

let renders = 0;
function Crash() {
  renders++;
  throw new Error('command result failed');
}
const result = (id, line) => ({
  id,
  command: 'ov health',
  status: 'success',
  content: { variant: 'write-result', lines: [line] },
});

test('one command result fails without losing adjacent results, composer DOM or draft', async (t) => {
  t.mock.method(console, 'error', () => {});
  const host = await mount({
    records: [result('healthy', 'Healthy result')],
    locale: 'zh-CN',
  });
  try {
    const input = host.input;
    await type(input, 'unfinished command');
    await host.render({
      records: [
        result('broken', h(Crash)),
        result('healthy', 'Healthy result'),
      ],
    });
    assert.match(host.container.textContent, /Healthy result/);
    assert.match(host.container.textContent, /命令结果显示失败/);
    assert.equal(host.container.querySelector('textarea'), input);
    assert.equal(input.value, 'unfinished command');
    const attempts = renders;
    await host.render({
      locale: 'en-US',
      records: [
        result('broken', h(Crash)),
        result('healthy', 'Healthy result'),
      ],
    });
    assert.equal(
      renders,
      attempts,
      'language and rebuilt content objects do not retry failures',
    );
    assert.match(
      host.container.textContent,
      /Command result could not be displayed/,
    );
    await host.render({ records: [result('broken', 'Replaced result')] });
    await act(() =>
      host.container.querySelector('.ov-error-boundary button').click(),
    );
    assert.match(host.container.textContent, /Replaced result/);
  } finally {
    await host.dispose();
  }
});

test('Console keeps command draft and history above a failed panel and mode switching works', async (t) => {
  t.mock.method(console, 'error', () => {});
  const host = await mountConsole(async () => ok('Recorded response'));
  try {
    await host.execute('ov health');
    await type(host.input, 'unfinished command');
    await host.render({ slots: { commandHelp: h(Crash) } });
    assert.ok(host.container.querySelector('.ov-error-boundary'));
    await host.press('VikingBot');
    assert.match(host.text, /Welcome to VikingBot/);
    await host.press('Terminal');
    await host.render({});
    await host.press('Retry display');
    assert.equal(host.input.value, 'unfinished command');
    assert.match(host.text, /Recorded response/);
  } finally {
    await host.dispose();
  }
});

test('Console inherits its error slot into a failed panel, preserving toolbar slots and command state', async (t) => {
  t.mock.method(console, 'error', () => {});
  const errorFallback = h(
    'aside',
    { className: 'console-host-error' },
    'Host console failure',
  );
  const host = await mountConsole(async () => ok('Saved history'));
  try {
    await host.execute('ov health');
    await type(host.input, 'pending draft');
    await host.render({
      slots: {
        errorFallback,
        commandHelp: h(Crash),
        toolbar: h('button', null, 'Host tool'),
      },
    });
    assert.match(host.text, /Host console failure/);
    assert.ok(host.button('Host tool'));
    assert.equal(host.container.querySelector('.ov-error-boundary'), null);
    await host.render({ slots: { toolbar: h('button', null, 'Host tool') } });
    assert.match(host.text, /Command panel could not be displayed/);
    await host.press('Retry display');
    assert.equal(host.input.value, 'pending draft');
    assert.match(host.text, /Saved history/);
  } finally {
    await host.dispose();
  }
});
