import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { format, resolveConfig } from 'prettier';
import { analyticsDefinitions } from '../packages/react/dist/docs/client/analytics/events.js';

const groups = [
  ['file_browser_', '文件浏览区块', 'File browser'],
  ['file_tree_', '文件树', 'File tree'],
  ['file_preview_', '文件预览', 'File preview'],
  ['console_', '控制台区块', 'Console block'],
  ['command_console_', '命令视图', 'Command view'],
  ['viking_bot_', '对话', 'Conversation'],
  ['scope_', '作用域选择', 'Scope selection'],
  ['agent_connect_', 'Agent 接入', 'Agent connection'],
  ['directory_tour_', '目录引导', 'Directory tour'],
  ['wiki_dashboard_', 'Wiki 面板', 'Wiki dashboard'],
  ['experience_dashboard_', '经验面板', 'Experience dashboard'],
  ['email_login_', '邮箱登录', 'Email login'],
  ['api_key_login_', 'API Key 登录', 'API key login'],
  ['password_', '密码表单', 'Password forms'],
  ['login_method_', '登录方式', 'Login method'],
  ['invitation_', '邀请确认', 'Invitation confirmation'],
];
const start = '{/* analytics-events:start */}';
const end = '{/* analytics-events:end */}';
const check = process.argv.includes('--check');
const code = (value) => '`' + value.replaceAll('|', '\\|') + '`';

for (const locale of ['zh', 'en']) {
  const url = new URL(
    `../docs/docs/${locale}/guide/analytics.mdx`,
    import.meta.url,
  );
  const original = await readFile(url, 'utf8');
  const first = original.indexOf(start),
    last = original.indexOf(end);
  if (first === -1 || last <= first)
    throw new Error(`Missing catalogue markers: ${url}`);
  const tables = groups
    .map(([prefix, chinese, english]) => {
      const headings =
        locale === 'zh'
          ? ['事件名', '触发时机', 'params']
          : ['Event name', 'Occurrence', 'params'];
      const rows = Object.entries(analyticsDefinitions)
        .filter(([key]) => key.startsWith(prefix))
        .map(([key, definition]) => {
          const params =
            Object.entries(definition.fields)
              .map(([field, value]) =>
                code(
                  `${field}: ${typeof value === 'string' ? value : value.map((item) => `'${item}'`).join(' | ')}`,
                ),
              )
              .join('<br />') || code('{}');
          return `| ${code(`openviking_${key}`)} | ${definition[locale]} | ${params} |`;
        });
      return `### ${locale === 'zh' ? chinese : english}\n\n| ${headings.join(' | ')} |\n| --- | --- | --- |\n${rows.join('\n')}`;
    })
    .join('\n\n');
  const path = fileURLToPath(url);
  const next = await format(
    original.slice(0, first) +
      start +
      '\n\n' +
      tables +
      '\n\n' +
      original.slice(last),
    { ...(await resolveConfig(path)), filepath: path },
  );
  if (original === next) continue;
  if (check) {
    console.error(
      `Analytics catalogue is outdated: ${path}. Run npm run sync:analytics-docs.`,
    );
    process.exitCode = 1;
  } else await writeFile(url, next);
}
