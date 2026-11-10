import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { within } from './dashboard-model.js';

interface CompileTarget {
  to: string;
  conflicts_num?: number;
  compile_type?: string;
  initialized?: boolean;
}

export async function readCompileTargets(
  client: OvClient,
  rootUri: string,
  options: OvRequestOptions,
) {
  const targets: CompileTarget[] = [];
  let cursor: string | undefined;
  const seen = new Set<string>();
  do {
    const result = await client.request<{
      targets: CompileTarget[];
      next_cursor?: string | null;
    }>({
      method: 'GET',
      path: OV_PATHS.compileConfigs,
      query: { limit: 100, cursor },
      options,
    });
    if (!Array.isArray(result?.targets))
      throw new TypeError('Invalid Compile target registry');
    targets.push(
      ...result.targets.filter(
        (item) =>
          typeof item.to === 'string' &&
          item.compile_type !== 'memory' &&
          item.initialized !== false &&
          (within(item.to, rootUri) || within(rootUri, item.to)),
      ),
    );
    cursor = result.next_cursor ?? undefined;
    if (cursor && seen.has(cursor))
      throw new TypeError('Compile target cursor did not advance');
    if (cursor) seen.add(cursor);
  } while (cursor);
  return targets;
}
