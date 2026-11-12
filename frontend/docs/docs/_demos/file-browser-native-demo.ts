import { DEMO_WIKI, DEMO_USER } from '#dashboard-demo-data';
// Original silent test pattern, generated with ffmpeg testsrc2 (160x90, 2 seconds).
const VIDEO =
  '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';
const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: 'ok', result }), {
    headers: { 'content-type': 'application/json' },
  });
let config = {
  to: DEMO_WIKI,
  from: [`${DEMO_USER}/resources/notes`],
  skill: `${DEMO_USER}/skills/compile`,
  instruction: 'Maintain the handbook from the source notes.',
  auto_update: false,
  update_schedule: null,
};
const SYSTEM = 'viking://system';
const COMPILE = `${SYSTEM}/compile`;
const EXAMPLE_SKILLS = `${COMPILE}/example-skills`;
const systemDirectories = new Map([
  [SYSTEM, [COMPILE]],
  [COMPILE, [EXAMPLE_SKILLS]],
  [EXAMPLE_SKILLS, []],
]);

/** Fictional native API responses; package bytes are demo text, not an actual backup. */
export function nativePreviewDemoResponse(
  url: URL,
  init?: RequestInit,
): Response | undefined {
  const uri = url.searchParams.get('uri') ?? '';
  const children = systemDirectories.get(uri);
  if (children && url.pathname === '/api/v1/fs/ls')
    return ok(children.map((child) => ({ uri: child, isDir: true })));
  if (children && url.pathname === '/api/v1/fs/stat')
    return ok({ uri, isDir: true });
  const body = typeof init?.body === 'string' ? init.body : '{}';
  switch (url.pathname) {
    case '/api/v1/content/download':
      return url.searchParams.get('uri')?.endsWith('/demo.webm')
        ? new Response(
            Uint8Array.from(atob(VIDEO), (value) => value.charCodeAt(0)),
            { headers: { 'content-type': 'video/webm' } },
          )
        : undefined;
    case '/api/v1/snapshot/log':
      return ok([
        {
          oid: 'demo-snapshot',
          message: 'Offline example revision',
          author: { time_seconds: 1794268800 },
          committer: { time_seconds: 1794268800 },
        },
      ]);
    case '/api/v1/snapshot/versions':
      return ok({
        versions: [
          {
            version_id: 'demo-version',
            version_number: 1,
            snapshot_id: 'demo-snapshot',
            snapshot_path: url.searchParams.get('uri'),
            created_at: 1794268800,
          },
        ],
        next_before_version: null,
      });
    case '/api/v1/snapshot/show':
      return new Response(
        '# Historical content\n\nThis revision is simulated.',
      );
    case '/api/v1/compile/config':
      return ok(config);
    case '/api/v1/compile/config/update':
      config = JSON.parse(body) as typeof config;
      return ok(config);
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
