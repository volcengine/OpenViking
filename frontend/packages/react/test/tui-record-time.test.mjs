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
import { Tui } from '@openviking/react/components/tui';

const output = await mkdtemp(join(tmpdir(), 'ov-tui-record-time-'));
const source = new URL('../src/components/tui/', import.meta.url);
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
            time: fileURLToPath(new URL('tui-record-time.tsx', source)),
            labels: fileURLToPath(new URL('tui-labels.ts', source)),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const { formatTuiRecordTime } = await import(
  pathToFileURL(join(output, 'time.js'))
);
const { resolveTuiLabels } = await import(
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
    assert.equal(formatTuiRecordTime(record, resolveTuiLabels(), now), en);
    assert.equal(
      formatTuiRecordTime(record, resolveTuiLabels({}, 'zh'), now),
      zh,
    );
  }
});

test('records at or beyond 24 hours display the timestamp in local date format', () => {
  const createdAtMs = new Date(2026, 0, 15, 10, 24).getTime();
  for (const elapsed of [86_400_000, 172_800_000]) {
    assert.equal(
      formatTuiRecordTime(
        { createdAtMs, createdAt: 'legacy' },
        resolveTuiLabels(),
        createdAtMs + elapsed,
      ),
      '2026-01-15 10:24',
    );
  }
});

test('timestamp zero is valid and future timestamps use the just-now label', () => {
  const labels = resolveTuiLabels();
  assert.equal(formatTuiRecordTime({ createdAtMs: 0 }, labels, 0), 'Just now');
  assert.equal(
    formatTuiRecordTime({ createdAtMs: now + 60_000 }, labels, now),
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
      formatTuiRecordTime(
        { createdAtMs, createdAt: '10:24' },
        resolveTuiLabels(),
        now,
      ),
      '10:24',
    );
  }
  assert.equal(formatTuiRecordTime({}, resolveTuiLabels(), now), undefined);
});

test('hosts can replace dynamic time copy while keeping other locale defaults', () => {
  const labels = resolveTuiLabels(
    {
      justNow: 'now',
      formatMinutesAgo: (count) => `${count} min`,
      formatHoursAgo: (count) => `${count} hr`,
    },
    'zh',
  );
  assert.equal(labels.noResult, '暂无返回结果');
  for (const [elapsed, expected] of [
    [0, 'now'],
    [120_000, '2 min'],
    [7_200_000, '2 hr'],
  ])
    assert.equal(
      formatTuiRecordTime({ createdAtMs: now - elapsed }, labels, now),
      expected,
    );
});

test('server rendering uses stable fallback copy before the client reads its clock', () => {
  const render = (time) =>
    renderToStaticMarkup(
      createElement(Tui, {
        value: '',
        locale: 'zh',
        records: [{ id: 'time', command: 'demo', status: 'success', ...time }],
      }),
    );
  assert.match(
    render({ createdAtMs: now, createdAt: '10:24' }),
    /ov-tui__bubble-time">10:24<\/span>/,
  );
  assert.doesNotMatch(render({ createdAtMs: now }), /ov-tui__bubble-time/);
  assert.match(
    render({ createdAt: 'host label' }),
    /ov-tui__bubble-time">host label<\/span>/,
  );
});
