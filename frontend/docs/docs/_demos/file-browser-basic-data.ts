import { nativePreviewDemoResponse } from '#file-browser-native-demo';
import { image } from '#file-preview-console-data';
import {
  dashboardDemoResponse,
  DEMO_WIKI,
  DEMO_WIKI_FILE,
} from '#dashboard-demo-data';

/**
 * 离线运行的文件浏览示例。数据由本文件里的 `offlineFetch` 按原生接口的形状返回，
 * 不访问网络，也不带任何凭证——示例只用来演示视图与交互。
 *
 * 换成真实服务时，删掉 `fetch` 与下面的假数据，改为在 Provider 上传服务地址与
 * 身份字段；正式接入由宿主从自己的配置与登录态取值。
 *
 * An offline file browser demo. `offlineFetch` below answers in the shape of the
 * native API, so the example touches no network and carries no credential; it
 * exists only to show the view and its interactions.
 *
 * For a real service, drop `fetch` and the fixtures below and pass the address and
 * identity fields to the Provider; a real host reads them from its own
 * configuration and sign-in state.
 */

/** 一条目录项，形状与 `fs/ls` 的返回一致。One directory entry, shaped like `fs/ls`. */
interface Entry {
  uri: string;
  isDir?: boolean;
  tags?: string[];
}

const USER = 'viking://user/alice';
export const MEMORIES = `${USER}/memories`;
const PREFERENCES = `${MEMORIES}/preferences`;
const ENTITIES = `${MEMORIES}/entities`;
const RESOURCES = `${USER}/resources`;
const NOTES = `${RESOURCES}/notes`;
export const DEMO_PREVIEW_FILE = `${NOTES}/rollout.md`;
const DEMO_PREVIEW_IMAGE = `${NOTES}/cli.svg`;

/**
 * 目录内容。故意留几个长名称，用来演示树宽收窄后靠横向滚动而不是省略号。
 *
 * Directory contents. A few long names are deliberate: they show the tree
 * scrolling sideways rather than truncating once its column narrows.
 */
const DIRS: Record<string, Entry[]> = {
  'viking://': [
    { uri: 'viking://agent', isDir: true },
    { uri: 'viking://resources', isDir: true },
    { uri: USER, isDir: true },
    { uri: 'viking://system', isDir: true },
  ],
  [USER]: [
    { uri: MEMORIES, isDir: true },
    { uri: `${USER}/peers`, isDir: true },
    { uri: `${USER}/privacy`, isDir: true },
    { uri: RESOURCES, isDir: true },
    { uri: `${USER}/sessions`, isDir: true },
    { uri: `${USER}/skills`, isDir: true },
  ],
  [MEMORIES]: [
    { uri: PREFERENCES, isDir: true },
    { uri: ENTITIES, isDir: true },
    { uri: `${MEMORIES}/events`, isDir: true },
    { uri: `${MEMORIES}/experiences`, isDir: true },
    { uri: `${MEMORIES}/profile.md` },
    { uri: `${MEMORIES}/identity.md` },
  ],
  [PREFERENCES]: [
    { uri: `${PREFERENCES}/package-manager.md` },
    { uri: `${PREFERENCES}/code-review-with-a-single-blocking-priority.md` },
  ],
  [ENTITIES]: [{ uri: `${ENTITIES}/openviking.md` }],
  [RESOURCES]: [
    { uri: DEMO_WIKI, isDir: true, tags: ['compile_target=true'] },
    { uri: NOTES, isDir: true },
    { uri: `${RESOURCES}/2026-Q4-context-management-retrospective.md` },
    { uri: `${RESOURCES}/design-system-alignment-notes.md` },
  ],
  [NOTES]: [
    { uri: DEMO_PREVIEW_FILE },
    { uri: DEMO_PREVIEW_IMAGE },
    { uri: `${NOTES}/demo.webm` },
  ],
};

/** 文件正文。Body copy of each file. */
const FILES: Record<string, string> = {
  [`${MEMORIES}/profile.md`]:
    '# Profile\n\n- Role: frontend engineer\n- Focus: context database\n',
  [`${MEMORIES}/identity.md`]:
    '# Identity\n\nOpenViking, a context database for agents.\n',
  [`${PREFERENCES}/package-manager.md`]:
    '# Package manager\n\nUse pnpm for installs and scripts; npm only when a task names it.\n',
  [`${PREFERENCES}/code-review-with-a-single-blocking-priority.md`]:
    '# Review priorities\n\nReport one blocking finding at a time, with the evidence that supports it.\n',
  [`${ENTITIES}/openviking.md`]:
    '# OpenViking\n\nUnifies agent memory, knowledge RAG and skills behind one API.\n',
  [`${RESOURCES}/2026-Q4-context-management-retrospective.md`]:
    '# Q4 retrospective\n\nThe tree moved to lazy loading per directory, which cut the first paint.\n',
  [`${RESOURCES}/design-system-alignment-notes.md`]:
    '# Design alignment\n\nColours come from the console theme; components never hard-code them.\n',
  [DEMO_PREVIEW_FILE]:
    '# Rollout\n\nStage first, then production once the release ticket passes.\n\n![CLI](./cli.svg)\n\n| Check | Result |\n| --- | --- |\n| First open | Ready |',
};

/** 目录的两级摘要。The two summary levels of a directory. */
const SUMMARIES: Record<string, { l0: string; l1: string }> = {
  [MEMORIES]: {
    l0: 'Long-term memory accumulated across sessions: preferences, entities and events.',
    l1: 'Memory is split by kind. Preferences hold stated habits, entities hold people and projects, events hold dated milestones.',
  },
  [PREFERENCES]: {
    l0: 'Stated preferences of this user.',
    l1: 'Each file records one preference with the evidence that produced it.',
  },
  [RESOURCES]: {
    l0: 'Private knowledge of this user.',
    l1: 'Notes and retrospectives imported or written by the user.',
  },
};

/** 按查询词在全部条目里做子串匹配，模拟语义检索的返回形状。 */
/** Substring matching over every entry, in the shape of the semantic search. */
function search(query: string) {
  const needle = query.trim().toLowerCase();
  if (!needle) return {};
  const hits = Object.values(DIRS)
    .flat()
    .filter((entry) => entry.uri.toLowerCase().includes(needle));
  return {
    memories: hits.filter((entry) => entry.uri.startsWith(USER)),
    resources: hits.filter((entry) =>
      entry.uri.startsWith('viking://resources'),
    ),
  };
}

const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: 'ok', result }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

/**
 * 按原生接口的路径与形状作答，不发出任何请求。
 * Answers by native API path and shape without sending a request.
 */
export function offlineFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  // 客户端只会传字符串地址；`Request` 与 `URL` 两种形态一并接住，避免示例
  // 在类型上假定某种调用方式。
  //
  // The client only passes a string address; the `Request` and `URL` shapes are
  // accepted too, so the demo does not assume one calling convention.
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.href
        : input.url;
  const url = new URL(raw, 'https://offline.demo');
  const uri = (url.searchParams.get('uri') ?? '').replace(
    /^viking:\/\/~(?=\/|$)/,
    USER,
  );
  if (url.searchParams.has('uri')) url.searchParams.set('uri', uri);
  const body = typeof init?.body === 'string' ? init.body : '';

  const native = nativePreviewDemoResponse(url, init);
  if (native) return Promise.resolve(native);
  const board = dashboardDemoResponse(url);
  if (board) return Promise.resolve(board);

  switch (url.pathname) {
    case '/api/v1/fs': {
      if (init?.method !== 'DELETE')
        return Promise.resolve(new Response(null, { status: 405 }));
      delete FILES[uri];
      for (const [directory, entries] of Object.entries(DIRS))
        DIRS[directory] = entries.filter((entry) => entry.uri !== uri);
      return Promise.resolve(ok({ uri }));
    }
    case '/api/v1/fs/ls':
      return Promise.resolve(ok(DIRS[uri] ?? []));
    case '/api/v1/fs/stat':
      return Promise.resolve(ok({ uri, isDir: uri in DIRS }));
    case '/api/v1/content/download':
      return Promise.resolve(
        uri === DEMO_PREVIEW_IMAGE
          ? new Response(
              decodeURIComponent(image.slice(image.indexOf(',') + 1)),
              {
                headers: { 'content-type': 'image/svg+xml' },
              },
            )
          : new Response(FILES[uri] ?? '# Offline demo'),
      );
    case '/api/v1/content/read':
      return Promise.resolve(ok(FILES[uri] ?? ''));
    case '/api/v1/content/write': {
      const { uri: target, content } = JSON.parse(body) as {
        uri: string;
        content: string;
      };
      FILES[target.replace(/^viking:\/\/~(?=\/|$)/, USER)] = content;
      return Promise.resolve(ok({}));
    }
    case '/api/v1/content/abstract':
      return Promise.resolve(ok(SUMMARIES[uri]?.l0 ?? ''));
    case '/api/v1/content/overview':
      return Promise.resolve(ok(SUMMARIES[uri]?.l1 ?? ''));
    case '/api/v1/search/find': {
      const { query } = JSON.parse(body || '{}') as { query?: string };
      if (query?.trim().toLowerCase() === 'error')
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: 'error',
              error: {
                code: 'DEMO_SEARCH_FAILED',
                message: 'Demo search unavailable',
              },
            }),
            { status: 503, headers: { 'content-type': 'application/json' } },
          ),
        );
      return Promise.resolve(ok(search(query ?? '')));
    }
    default:
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            code: 'NOT_FOUND',
            message: `The offline demo does not answer ${url.pathname}`,
          }),
          { status: 404, headers: { 'content-type': 'application/json' } },
        ),
      );
  }
}

export { DEMO_WIKI_FILE };
