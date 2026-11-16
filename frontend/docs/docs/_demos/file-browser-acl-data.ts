import type { FileBrowserAclReport } from '@openviking/react/modules/file-browser';
export const ACL_URI = 'viking://resources/team-handbook';
const parent = [
  { principal: 'user:alice', level: 'manage' },
  { principal: 'group:research', level: 'read' },
] as const;
const initial = (): FileBrowserAclReport => ({
  uri: ACL_URI,
  acl_mode: 'inherit',
  inherited_entries: [...parent],
  direct_entries: [{ principal: 'user:bob', level: 'write' }],
  effective_entries: [...parent, { principal: 'user:bob', level: 'write' }],
});
const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: 'ok', result }), {
    headers: { 'Content-Type': 'application/json' },
  });
/** Isolated, public fixtures. Each demo owns its own grants and failure switches. */
export function createAclDemo() {
  let report = initial();
  let failSave = false;
  let failDirectory = false;
  let level: 'read' | 'write' | 'manage' = 'manage';
  const fetch: typeof globalThis.fetch = async (input, init) => {
    const url = new URL(
      input instanceof Request ? input.url : input,
      'https://demo.invalid',
    );
    const path = url.pathname;
    const uri = url.searchParams.get('uri') ?? ACL_URI;
    const error = (message: string) =>
      new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'DEMO_FAILURE', message },
        }),
        { status: 403 },
      );
    if (path.endsWith('/acl')) {
      if (level !== 'manage') return error('Demo: management access required');
      if (init?.method === 'PUT' || init?.method === 'DELETE') {
        await new Promise((resolve) => setTimeout(resolve, 500));
        if (failSave)
          return error('Demo: permission changed; the update was rejected');
        if (init.method === 'DELETE')
          report = {
            ...initial(),
            direct_entries: [],
            effective_entries: [...parent],
          };
        else {
          const body = JSON.parse(
            typeof init.body === 'string' ? init.body : '{}',
          ) as {
            acl_mode: 'inherit' | 'restricted';
            entries: FileBrowserAclReport['direct_entries'];
          };
          const inherited = body.acl_mode === 'inherit' ? [...parent] : [];
          report = {
            ...report,
            acl_mode: body.acl_mode,
            direct_entries: body.entries,
            effective_entries: [
              ...inherited.filter(
                (entry) =>
                  !body.entries.some(
                    (item) => item.principal === entry.principal,
                  ),
              ),
              ...body.entries,
            ],
          };
        }
      }
      return ok(report);
    }
    if (path.endsWith('/compile/configs')) return ok({ targets: [] });
    if (path.endsWith('/fs/attrs')) return ok({ uri, attrs: {} });
    if (path.endsWith('/fs/stat'))
      return ok({ uri, isDir: !uri.endsWith('.md'), access: level });
    if (path.endsWith('/fs/ls'))
      return ok(
        uri === 'viking://resources'
          ? [
              { uri: ACL_URI, isDir: true, access: level },
              { uri: 'viking://resources/public-notes.md', access: 'read' },
              {
                uri: 'viking://resources/restricted',
                isDir: true,
                access: 'denied',
              },
              {
                uri: 'viking://resources/shared-container',
                isDir: true,
                access: 'container',
              },
            ]
          : uri.endsWith('shared-container')
            ? [{ uri: `${uri}/accessible.md`, access: 'read' }]
            : [],
      );
    if (path.endsWith('/content/abstract'))
      return ok('A shared handbook for the research team.');
    if (path.endsWith('/content/overview'))
      return ok(
        '# Team handbook\n\nShared notes, working agreements and release guidance.\n\nSelect **Manage permissions** to edit access.',
      );
    if (path.endsWith('/content/read'))
      return ok('# Public notes\n\nThis example has read-only content.');
    if (path.endsWith('/users') || path.endsWith('/groups')) {
      if (failDirectory) return error('Demo: principal directory unavailable');
      if (path.endsWith('/groups'))
        return ok(
          ['research', 'engineering', 'design'].map((group_id) => ({
            group_id,
            member_count: 2,
          })),
        );
      const query = url.searchParams.get('query')?.toLowerCase() ?? '';
      const users = ['alice', 'bob', 'carol', 'david']
        .filter((name) => name.includes(query))
        .map((user_id) => ({ user_id }));
      return ok({ users, total: users.length });
    }
    return ok({ resources: [] });
  };
  return {
    fetch,
    setFailSave: (value: boolean) => {
      failSave = value;
    },
    setFailDirectory: (value: boolean) => {
      failDirectory = value;
    },
    setLevel: (value: typeof level) => {
      level = value;
    },
  };
}
