import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CommandConsole } from '@openviking/react/components/command-console';

const output = await mkdtemp(join(tmpdir(), 'ov-command-console-record-time-'));
const source = new URL('../src/components/command-console/', import.meta.url);
const builder = await createRslib({
  config: {
    performance: { buildCache: false },
    plugins: [pluginReact()],
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            time: fileURLToPath(
              new URL('command-console-record-time.tsx', source),
            ),
            labels: fileURLToPath(new URL('command-console-labels.ts', source)),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const { formatCommandConsoleRecordTime } = await import(
  pathToFileURL(join(output, 'time.js'))
);
const { resolveCommandConsoleLabels } = await import(
  pathToFileURL(join(output, 'labels.js'))
);
after(() => rm(output, { recursive: true, force: true }));
const now = new Date(2026, 0, 16, 10, 24).getTime();

test('relative record times respect minute and hour boundaries in both locales', () => {
  const cases = [
    [0, 'Just now', '刚刚'],
    [59_999, 'Just now', '刚刚'],
    [60_000, '1 minute ago', '1 分钟前'],
    [119_999, '1 minute ago', '1 分钟前'],
    [120_000, '2 minutes ago', '2 分钟前'],
    [3_599_999, '59 minutes ago', '59 分钟前'],
    [3_600_000, '1 hour ago', '1 小时前'],
    [7_200_000, '2 hours ago', '2 小时前'],
    [86_399_999, '23 hours ago', '23 小时前'],
  ];
  for (const [elapsed, en, zh] of cases) {
    const record = { createdAtMs: now - elapsed, createdAt: 'legacy' };
    assert.equal(
      formatCommandConsoleRecordTime(
        record,
        resolveCommandConsoleLabels(),
        now,
      ),
      en,
    );
    assert.equal(
      formatCommandConsoleRecordTime(
        record,
        resolveCommandConsoleLabels({}, 'zh-CN'),
        now,
      ),
      zh,
    );
  }
});

test('records at or beyond 24 hours display the timestamp in local date format', () => {
  const createdAtMs = new Date(2026, 0, 15, 10, 24).getTime();
  for (const elapsed of [86_400_000, 172_800_000]) {
    assert.equal(
      formatCommandConsoleRecordTime(
        { createdAtMs, createdAt: 'legacy' },
        resolveCommandConsoleLabels(),
        createdAtMs + elapsed,
      ),
      '2026-01-15 10:24',
    );
  }
});

test('timestamp zero is valid and future timestamps use the just-now label', () => {
  const labels = resolveCommandConsoleLabels();
  assert.equal(
    formatCommandConsoleRecordTime({ createdAtMs: 0 }, labels, 0),
    'Just now',
  );
  assert.equal(
    formatCommandConsoleRecordTime({ createdAtMs: now + 60_000 }, labels, now),
    'Just now',
  );
});

test('invalid or missing timestamps preserve the host display text without parsing it', () => {
  for (const createdAtMs of [
    undefined,
    NaN,
    Infinity,
    -Infinity,
    8.64e15 + 1,
  ]) {
    assert.equal(
      formatCommandConsoleRecordTime(
        { createdAtMs, createdAt: '10:24' },
        resolveCommandConsoleLabels(),
        now,
      ),
      '10:24',
    );
  }
  assert.equal(
    formatCommandConsoleRecordTime({}, resolveCommandConsoleLabels(), now),
    undefined,
  );
});

test('hosts can replace dynamic time copy while keeping other locale defaults', () => {
  const labels = resolveCommandConsoleLabels(
    {
      justNow: 'now',
      formatMinutesAgo: (count) => `${count} min`,
      formatHoursAgo: (count) => `${count} hr`,
    },
    'zh-CN',
  );
  assert.equal(labels.noResult, '暂无返回结果');
  for (const [elapsed, expected] of [
    [0, 'now'],
    [120_000, '2 min'],
    [7_200_000, '2 hr'],
  ])
    assert.equal(
      formatCommandConsoleRecordTime(
        { createdAtMs: now - elapsed },
        labels,
        now,
      ),
      expected,
    );
});

test('server rendering uses stable fallback copy before the client reads its clock', () => {
  const render = (time) =>
    renderToStaticMarkup(
      createElement(CommandConsole, {
        value: '',
        locale: 'zh-CN',
        records: [{ id: 'time', command: 'demo', status: 'success', ...time }],
      }),
    );
  assert.match(
    render({ createdAtMs: now, createdAt: '10:24' }),
    /ov-command-console__bubble-time">10:24<\/span>/,
  );
  assert.doesNotMatch(
    render({ createdAtMs: now }),
    /ov-command-console__bubble-time/,
  );
  assert.match(
    render({ createdAt: 'host label' }),
    /ov-command-console__bubble-time">host label<\/span>/,
  );
});
