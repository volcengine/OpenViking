import { DEMO_WIKI, DEMO_WIKI_FILE, DEMO_USER } from '#dashboard-demo-data';
import { demoTranscript, VIDEO, VIDEO_FRAMES } from './file-browser-video-demo';

export const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: 'ok', result }), {
    headers: { 'content-type': 'application/json' },
  });
export const VIDEO_SEGMENT = `${DEMO_USER}/resources/notes/video-segment`;
export const DEMO_PREVIEW_VIDEO = `${VIDEO_SEGMENT}/demo.webm`;
const VIDEO_METADATA = `${VIDEO_SEGMENT}/segment_info.json`;
const config = {
  compile_name: 'handbook',
  args: {},
  to: DEMO_WIKI,
  from: [`${DEMO_USER}/resources/notes`],
  skill: `${DEMO_USER}/skills/compile`,
  instruction: 'Maintain the handbook from the source notes.',
  auto_update: false,
  update_schedule: null,
};
const configurations = new Map([
  ['handbook', config],
  ['handbook-review', { ...config, compile_name: 'handbook-review' }],
]);
const SYSTEM = 'viking://system';
const COMPILE = `${SYSTEM}/compile`;
const EXAMPLE_SKILLS = `${COMPILE}/example-skills`;
const systemDirectories = new Map([
  [SYSTEM, [COMPILE]],
  [COMPILE, [EXAMPLE_SKILLS]],
  [EXAMPLE_SKILLS, []],
]);
const originalSnapshot = {
  oid: '0'.repeat(40),
  message: 'Offline example revision',
  author: { time_seconds: 1794268800 },
  committer: { time_seconds: 1794268800 },
};
const wikiVersion = (version: number, message: string) => ({
  oid: String(version).repeat(40),
  parents: version > 1 ? [String(version - 1).repeat(40)] : [],
  message,
  committer: { time_seconds: 1794268800 + version * 3600 },
});
const deletedWikiVersion = wikiVersion(2, 'Deleted release guide');
const demoWikiVersions = [
  wikiVersion(3, 'Recreated release guide'),
  deletedWikiVersion,
  wikiVersion(1, 'Created release guide'),
];
const savedSnapshots = new Map<
  string,
  { info: typeof originalSnapshot; content: string }[]
>();
let nextSnapshot = 1;
const canonicalUri = (path: string) =>
  path.replace(/^viking:\/\/~(?=\/|$)/, DEMO_USER);

/** Fictional native API responses; package bytes are demo text, not an actual backup. */
export function nativePreviewDemoResponse(
  url: URL,
  files: Readonly<Record<string, string>>,
  init?: RequestInit,
): Response | undefined {
  const uri = url.searchParams.get('uri') ?? '';
  if (uri === VIDEO_SEGMENT && url.pathname === '/api/v1/fs/ls') {
    return ok(
      [
        DEMO_PREVIEW_VIDEO,
        VIDEO_METADATA,
        `${VIDEO_SEGMENT}/frame-1.jpg`,
        `${VIDEO_SEGMENT}/frame-2.jpg`,
      ].map((uri) => ({ uri, isDir: false })),
    );
  }
  if (uri === VIDEO_SEGMENT && url.pathname === '/api/v1/fs/stat')
    return ok({ uri, isDir: true });
  if (uri === VIDEO_METADATA && url.pathname === '/api/v1/content/read')
    return ok(JSON.stringify(demoTranscript));
  const children = systemDirectories.get(uri);
  if (children && url.pathname === '/api/v1/fs/ls')
    return ok(children.map((child) => ({ uri: child, isDir: true })));
  if (children && url.pathname === '/api/v1/fs/stat')
    return ok({ uri, isDir: true });
  const body = typeof init?.body === 'string' ? init.body : '{}';
  switch (url.pathname) {
    case '/api/v1/content/download': {
      const frame = /\/video-segment\/frame-([12])\.jpg$/.exec(uri);
      const jpeg = frame && VIDEO_FRAMES[Number(frame[1]) - 1];
      if (jpeg) {
        return new Response(
          Uint8Array.from(atob(jpeg), (value) => value.charCodeAt(0)),
          { headers: { 'content-type': 'image/jpeg' } },
        );
      }
      return url.searchParams.get('uri')?.endsWith('/demo.webm')
        ? new Response(
            Uint8Array.from(atob(VIDEO), (value) => value.charCodeAt(0)),
            { headers: { 'content-type': 'video/webm' } },
          )
        : undefined;
    }
    case '/api/v1/snapshot/commit': {
      const { message, paths } = JSON.parse(body) as {
        message: string;
        paths: string[];
      };
      const targets = paths.map(canonicalUri);
      const changed = targets.filter((target) => {
        const latest = savedSnapshots.get(target)?.[0];
        return latest?.content !== files[target];
      });
      if (changed.length === 0) {
        const latest = savedSnapshots.get(targets[0] ?? '')?.[0];
        return ok({ result: 'noop', commit_oid: latest?.info.oid });
      }
      const oid = (nextSnapshot++).toString(16).padStart(40, '0');
      const timestamp = { time_seconds: Math.floor(Date.now() / 1000) };
      const info = { oid, message, author: timestamp, committer: timestamp };
      for (const target of changed) {
        const snapshot = { info, content: files[target] ?? '' };
        savedSnapshots.set(target, [
          snapshot,
          ...(savedSnapshots.get(target) ?? []),
        ]);
      }
      return ok({ result: 'created', commit_oid: oid });
    }
    case '/api/v1/snapshot/log': {
      const path = canonicalUri(url.searchParams.get('paths') ?? '');
      if (path === DEMO_WIKI_FILE) {
        if (url.searchParams.get('branch') === deletedWikiVersion.oid)
          return ok([deletedWikiVersion]);
        return ok(demoWikiVersions);
      }
      const history = (savedSnapshots.get(path) ?? []).map(({ info }) => info);
      return ok([...history, originalSnapshot]);
    }
    case '/api/v1/snapshot/versions':
      return ok({
        uri: canonicalUri(uri),
        versions:
          canonicalUri(uri) === DEMO_WIKI_FILE
            ? demoWikiVersions
            : [2, 1].map((version) => ({
                oid: String(version).repeat(40),
                message: `Offline revision ${version}`,
                committer: { time_seconds: 1794268800 + version * 3600 },
              })),
        next_before_snapshot: null,
      });
    case '/api/v1/snapshot/show': {
      const path = canonicalUri(url.searchParams.get('path') ?? '');
      const target = url.searchParams.get('target_ref');
      if (path === DEMO_WIKI_FILE && target === deletedWikiVersion.oid) {
        return Response.json(
          {
            status: 'error',
            error: {
              code: 'NOT_FOUND',
              message: 'Snapshot file not found.',
              details: {
                type: 'git_blob',
                resource: path,
                reason: 'path_not_found',
                deleted_file: true,
              },
            },
          },
          { status: 404 },
        );
      }
      const snapshot = savedSnapshots
        .get(path)
        ?.find(({ info }) => info.oid === target);
      return new Response(
        snapshot?.content ??
          `# Historical content\n\nSimulated ${target ?? 'revision'}.`,
      );
    }
    case '/api/v1/snapshot/restore':
      return ok({ result: 'noop' });
    case '/api/v1/compile/config':
      return ok(
        configurations.get(url.searchParams.get('compile_name') ?? 'handbook'),
      );
    case '/api/v1/compile/config/update': {
      const next = JSON.parse(body) as typeof config;
      configurations.set(next.compile_name, next);
      return ok(next);
    }
    case '/api/v1/resources/temp_upload':
      return ok({ temp_file_id: 'demo-upload' });
    case '/api/v1/pack/import':
      return ok({ uri: (JSON.parse(body) as { parent: string }).parent });
    case '/api/v1/pack/export':
      return new Response(
        'Offline demo only: this is not a valid ovpack archive.',
      );
    default:
      return undefined;
  }
}
