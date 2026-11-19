/** Fixed, public demo responses. No request reaches an OpenViking service. */
export function consoleTransport(): typeof fetch {
  return async (input, init) => {
    const signal = init?.signal;
    await new Promise<void>((resolve, reject) => {
      const stop = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener('abort', stop);
        resolve();
      }, 700);
      if (signal?.aborted) stop();
      else signal?.addEventListener('abort', stop, { once: true });
    });
    const url =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const { pathname: path, searchParams } = new URL(
      url,
      'https://demo.invalid',
    );
    const resource = {
      uri: 'viking://resources/demo/notes.md',
      name: 'notes.md',
      isDir: false,
    };
    const compileName =
      path === '/api/v1/compile/create' && typeof init?.body === 'string'
        ? (JSON.parse(init.body) as { compile_name: string }).compile_name
        : undefined;
    const uri = searchParams.get('uri');
    const menuEntries =
      uri === 'viking://'
        ? [{ uri: 'viking://resources', isDir: true }]
        : uri === 'viking://resources'
          ? [{ uri: 'viking://resources/demo', isDir: true }]
          : [resource];
    const pattern =
      path === '/api/v1/search/glob' && typeof init?.body === 'string'
        ? (JSON.parse(init.body) as { pattern: string }).pattern
        : undefined;
    const keyword = pattern?.slice(4, -1).replace(/\[([a-z])[A-Z]\]/g, '$1');
    const writes: Record<string, unknown> = {
      '/api/v1/resources': { task_id: 'demo-resource', root_uri: resource.uri },
      '/api/v1/skills': { root_uri: 'viking://agent/skills/llm-wiki' },
      '/api/v1/compile/create': { compile_name: compileName },
      '/api/v1/compile': {
        task_id: 'demo-compile',
        status: 'pending',
        resource_id: 'viking://resources/demo-wiki',
      },
      '/api/v1/tasks/demo-compile': {
        task_id: 'demo-compile',
        status: 'completed',
      },
      '/api/v1/tasks/demo-memory': {
        task_id: 'demo-memory',
        status: 'completed',
        result: {
          memory_diff_uri:
            'viking://session/demo/history/archive_001/memory_diff.json',
        },
      },
      '/api/v1/search/glob': {
        matches: resource.uri.includes(keyword ?? '') ? [resource.uri] : [],
      },
    };
    const writeResult =
      writes[path] ??
      (path.endsWith('/messages') ? { message_count: 1 } : undefined) ??
      (path.endsWith('/commit')
        ? { task_id: 'demo-memory', status: 'accepted' }
        : undefined);
    const result =
      writeResult ??
      (path === '/api/v1/fs/stat'
        ? { uri, isDir: true }
        : path.endsWith('/ls') || path.endsWith('/tree')
          ? menuEntries
          : ['/read', '/abstract', '/overview'].some((suffix) =>
                path.endsWith(suffix),
              )
            ? '# OpenViking\n\nA fixed public demo resource. / 固定公开示例资源。'
            : path.endsWith('/find') || path.endsWith('/search')
              ? {
                  memories: [],
                  skills: [],
                  resources: [
                    {
                      ...resource,
                      context_type: 'resource',
                      level: 2,
                      score: 0.93,
                      abstract: 'A fixed demo match / 固定模拟匹配',
                    },
                  ],
                }
              : { state: 'ready', demonstration: true });
    return Response.json({ status: 'ok', result });
  };
}
