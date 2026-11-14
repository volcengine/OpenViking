import { readFile, writeFile } from 'node:fs/promises';
import { format, resolveConfig } from 'prettier';
const root = new URL(
  '../packages/react/src/client/monitoring/',
  import.meta.url,
);
const catalog = JSON.parse(
  await readFile(new URL('catalog.json', root), 'utf8'),
);
const profiles = new Map();
for (const file of ['views.ts', 'transport.ts', 'poll.ts', 'fault.ts']) {
  const source = await readFile(new URL(file, root), 'utf8');
  for (const match of source.matchAll(
    /export type (\w+) = ([\s\S]*?)(?=\nexport type |$)/g,
  ))
    profiles.set(match[1], match[2].trim());
}
const groups = {
  render: ['渲染异常', 'Render failures'],
  resource: ['代码与可视资源', 'Code and visual resources'],
  request: ['前端请求', 'Frontend requests'],
  stream: ['流连接与读取', 'Stream connection and reading'],
  poll: ['后台轮询', 'Background polling'],
  operation: ['区块与正文处理', 'Block and content processing'],
};
const names = catalog.map(({ name }) => name);
if (new Set(names).size !== names.length)
  throw new Error('Monitoring event names must be unique');
const start = '{/* monitoring-events:start */}',
  end = '{/* monitoring-events:end */}';
const code = (text) =>
  '`' +
  String(Array.isArray(text) ? text.join(' | ') : text).replaceAll('|', '\\|') +
  '`';
for (const lang of ['zh', 'en']) {
  const path = new URL(
    `../docs/docs/${lang}/guide/monitoring-events.mdx`,
    import.meta.url,
  );
  const original = await readFile(path, 'utf8');
  const parts = Object.entries(groups).map(([key, titles]) => {
    const rows = catalog
      .filter((entry) => entry.group === key)
      .map((entry) => {
        if (!profiles.has(entry.profile))
          throw new Error(`Unknown profile: ${entry.profile}`);
        return `| ${code(entry.name)} | ${code(entry.owner)} | ${code(entry.region ?? (lang === 'zh' ? '所属组件区域' : 'Owned region'))} | ${code(entry.profile)} | ${entry[lang]} |`;
      });
    return (
      `## ${titles[lang === 'zh' ? 0 : 1]}\n\n| ${lang === 'zh' ? '事件 / source.component / region / params 字段组 / 触发口径' : 'Event / source.component / region / params group / Occurrence'}`.replaceAll(
        ' / ',
        ' | ',
      ) + ` |\n| --- | --- | --- | --- | --- |\n${rows.join('\n')}`
    );
  });
  parts.push(
    lang === 'zh'
      ? '## params 字段组\n\n`Ready` 包含 `durationMs`（毫秒）和 `visibility`。`Fault` 包含 `errorKind`、配对的 `code`、`observedAt`、`incidentId`、`faultRole`、`causeEventId`、`elapsedMs` 和 `traceSnapshot`。公共字段含义见[前端监控](./monitoring)。'
      : '## params field groups\n\n`Ready` contains `durationMs` in milliseconds and `visibility`. `Fault` contains `errorKind`, its matching `code`, `observedAt`, `incidentId`, `faultRole`, `causeEventId`, `elapsedMs` and `traceSnapshot`. See [frontend monitoring](./monitoring) for shared fields.',
  );
  for (const [name, definition] of profiles)
    parts.push(`### ${name}\n\n\`\`\`ts\n${definition}\n\`\`\``);
  const first = original.indexOf(start),
    last = original.indexOf(end);
  if (first < 0 || last < first) throw new Error(`Missing markers: ${path}`);
  const next = await format(
    original.slice(0, first) +
      start +
      '\n\n' +
      parts.join('\n\n') +
      '\n\n' +
      original.slice(last),
    { ...(await resolveConfig(path.pathname)), filepath: path.pathname },
  );
  if (next !== original) {
    if (process.argv.includes('--check')) {
      console.error(`Stale monitoring catalogue: ${path.pathname}`);
      process.exitCode = 1;
    } else await writeFile(path, next);
  }
}
