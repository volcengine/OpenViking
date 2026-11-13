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
    const path = new URL(url, 'https://demo.invalid').pathname;
    const resource = {
      uri: 'viking://resources/demo/notes.md',
      name: 'notes.md',
      isDir: false,
    };
    const configOnly =
      path === '/api/v1/compile' &&
      typeof init?.body === 'string' &&
      (JSON.parse(init.body) as { args?: { auto_update?: boolean } }).args
        ?.auto_update === false;
    const writes: Record<string, unknown> = {
      '/api/v1/resources': { task_id: 'demo-resource', root_uri: resource.uri },
      '/api/v1/skills': { root_uri: 'viking://agent/skills/llm-wiki' },
      '/api/v1/compile': configOnly
        ? { to: 'viking://resources/demo-wiki', auto_update: false }
        : {
            task_id: 'demo-compile',
            status: 'pending',
            resource_id: resource.uri,
          },
    };
    const writeResult =
      writes[path] ??
      (path.endsWith('/messages') ? { message_count: 1 } : undefined) ??
      (path.endsWith('/commit')
        ? { task_id: 'demo-memory', status: 'pending' }
        : undefined);
    const result =
      writeResult ??
      (path.endsWith('/ls') || path.endsWith('/tree')
        ? [resource]
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
