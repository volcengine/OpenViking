import type { OvRequestSpec } from '../../client/types.js';
import type { TuiSkeletonKind } from '../../components/tui/index.js';
import type { Argument } from './command-parser.js';

type Values = Record<string, string | number | boolean>;
export interface CommandSpec {
  name: string;
  description: readonly [string, string];
  positional?: readonly Argument[];
  flags?: readonly Argument[];
  skeleton?: TuiSkeletonKind;
  request?: (values: Values) => OvRequestSpec;
}

const uri: Argument = { name: 'uri', kind: 'uri' };
const limit: Argument = { name: 'limit', kind: 'integer', min: 1 };
const recursive: Argument = { name: 'recursive', kind: 'boolean' };
const wait: Argument = { name: 'wait', kind: 'boolean' };
const taskId: Argument = { name: 'id' };

/** One catalogue drives validation, execution, completion and help. */
export const COMMAND_SPECS: readonly CommandSpec[] = [
  { name: 'help', description: ['Show available commands', '查看可用命令'] },
  { name: 'clear', description: ['Clear local records', '清空本地记录'] },
  {
    name: 'health',
    description: ['Check server health', '检查服务健康状态'],
    skeleton: 'status',
    request: () => ({ method: 'GET', path: '/health' }),
  },
  {
    name: 'status',
    description: ['Show system status', '查看系统状态'],
    skeleton: 'status',
    request: () => ({ method: 'GET', path: '/api/v1/system/status' }),
  },
  ...['ls', 'tree'].map((name): CommandSpec => ({
    name,
    description:
      name === 'ls'
        ? ['List directory entries', '列出目录内容']
        : ['List a resource tree', '查看资源树'],
    positional: [{ ...uri, optional: true }],
    flags: [
      limit,
      { name: 'offset', kind: 'integer' },
      ...(name === 'tree'
        ? [{ name: 'level_limit', kind: 'integer' as const, min: 1 }]
        : [recursive]),
    ],
    skeleton: 'tree',
    request: (values) => ({
      method: 'GET',
      path: `/api/v1/fs/${name}`,
      query: { uri: 'viking://', limit: 100, ...values, output: 'original' },
    }),
  })),
  {
    name: 'stat',
    description: ['Inspect a resource', '查看资源属性'],
    positional: [uri],
    skeleton: 'status',
    request: (query) => ({ method: 'GET', path: '/api/v1/fs/stat', query }),
  },
  ...['read', 'abstract', 'overview'].map((name): CommandSpec => ({
    name,
    description:
      name === 'read'
        ? ['Read file content', '读取文件内容']
        : name === 'abstract'
          ? ['Read an L0 abstract', '读取 L0 摘要']
          : ['Read an L1 overview', '读取 L1 概览'],
    positional: [uri],
    flags:
      name === 'read'
        ? [
            { name: 'offset', kind: 'integer' },
            { ...limit, min: -1 },
          ]
        : [],
    skeleton: 'resource',
    request: (query) => ({
      method: 'GET',
      path: `/api/v1/content/${name}`,
      query,
    }),
  })),
  ...['find', 'search'].map((name): CommandSpec => ({
    name,
    description:
      name === 'find'
        ? ['Find matching context', '检索匹配上下文']
        : ['Search with query planning', '通过查询规划检索'],
    positional: [{ name: 'query' }],
    flags: [{ name: 'target_uri', kind: 'uri' }, limit],
    skeleton: 'search',
    request: (body) => ({
      method: 'POST',
      path: `/api/v1/search/${name}`,
      body,
    }),
  })),
  ...['grep', 'glob'].map((name): CommandSpec => ({
    name,
    description:
      name === 'grep'
        ? ['Match text in resources', '匹配资源文本']
        : ['Match resource paths', '匹配资源路径'],
    positional: [{ name: 'pattern' }],
    flags: [
      uri,
      { name: 'node_limit', kind: 'integer', min: 1 },
      ...(name === 'grep'
        ? [{ name: 'case_insensitive', kind: 'boolean' as const }]
        : []),
    ],
    skeleton: 'search',
    request: (values) => ({
      method: 'POST',
      path: `/api/v1/search/${name}`,
      body: { uri: 'viking://', ...values },
    }),
  })),
  {
    name: 'mkdir',
    description: ['Create a directory', '创建目录'],
    positional: [uri],
    flags: [{ name: 'description' }],
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: '/api/v1/fs/mkdir', body }),
  },
  ...['cp', 'mv'].map((name): CommandSpec => ({
    name,
    description:
      name === 'cp'
        ? ['Copy a resource', '复制资源']
        : ['Move a resource', '移动资源'],
    positional: [
      { name: 'from_uri', kind: 'uri' },
      { name: 'to_uri', kind: 'uri' },
    ],
    flags: name === 'cp' ? [recursive] : [],
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: `/api/v1/fs/${name}`, body }),
  })),
  {
    name: 'rm',
    description: ['Remove a resource', '删除资源'],
    positional: [uri],
    flags: [recursive, wait],
    skeleton: 'write',
    request: (query) => ({ method: 'DELETE', path: '/api/v1/fs', query }),
  },
  {
    name: 'write',
    description: ['Write text content', '写入文本内容'],
    positional: [uri, { name: 'content' }],
    flags: [{ name: 'mode', choices: ['create', 'replace', 'append'] }, wait],
    skeleton: 'write',
    request: (body) => ({
      method: 'POST',
      path: '/api/v1/content/write',
      body,
    }),
  },
  {
    name: 'task list',
    description: ['List background tasks', '列出后台任务'],
    flags: [limit, { name: 'status' }, { name: 'task_type' }],
    skeleton: 'status',
    request: (query) => ({ method: 'GET', path: '/api/v1/tasks', query }),
  },
  {
    name: 'task status',
    description: ['Inspect a background task', '查看后台任务'],
    positional: [taskId],
    skeleton: 'status',
    request: ({ id }) => ({
      method: 'GET',
      path: `/api/v1/tasks/${encodeURIComponent(String(id))}`,
    }),
  },
  {
    name: 'task cancel',
    description: ['Request task cancellation', '请求取消后台任务'],
    positional: [taskId],
    skeleton: 'write',
    request: ({ id }) => ({
      method: 'POST',
      path: `/api/v1/tasks/${encodeURIComponent(String(id))}/cancel`,
    }),
  },
];
